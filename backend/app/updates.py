"""System updates via apt.

IMPORTANT: updates are NEVER automatic. The admin must trigger `install`
explicitly. MurOS exposes:
- `check()`      : apt-get update + apt list --upgradable, read-only.
- `install()`    : apt-get -y upgrade, manually triggered.
- `last_check()` : timestamp + package count from the last check.

State is persisted to a small JSON file in MUROS_STATE_DIR.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

STATE_DIR = Path(os.environ.get("MUROS_STATE_DIR", "/var/lib/muros"))
STATE_FILE = STATE_DIR / "updates_state.json"

# Changelog shipped with the package (see packaging/debian/rules). The
# dashboard surfaces the notes for the latest version from it; overridable
# for tests / dev via MUROS_CHANGELOG.
CHANGELOG_PATH = Path(os.environ.get("MUROS_CHANGELOG", "/opt/muros/CHANGELOG.md"))

# Keep a Changelog header: "## [version] - date" (date optional).
_CHANGELOG_HEADER_RE = re.compile(r"^##\s+\[([^\]]+)\](?:\s*-\s*(.+?))?\s*$")

_PKG_RE = re.compile(r"^([^/]+)/[^ ]+ ([^ ]+) [^ ]+ \[upgradable from: ([^\]]+)\]")

# MurOS package prefix. The muros package is managed by a distinct update
# channel (the signed apt repository apt.muros.org) which we exclude from
# the system update stream, so the admin clearly distinguishes the two kinds
# of updates. The candidate and the upgrade go through apt, exactly like the
# initial installation.
MUROS_PACKAGE_PREFIX = "muros"


def _load_state() -> dict:
    if not STATE_FILE.is_file():
        return {"last_check_at": None, "packages": []}
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {"last_check_at": None, "packages": []}


def _save_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def _is_muros_pkg(name: str) -> bool:
    return name == MUROS_PACKAGE_PREFIX or name.startswith(MUROS_PACKAGE_PREFIX + "-")


def _read_changelog_sections() -> list[dict]:
    """Parse the shipped CHANGELOG.md into ordered sections.

    Each section is {version, date, body}, where version is the bracketed
    label ("Unreleased" or "v0.9.0-rcN"), date the optional trailing date,
    and body the markdown notes up to the next header. Returns an empty
    list when the changelog is missing or unreadable (dev boxes without
    the package installed), so callers degrade to "no changelog".
    """
    try:
        text = CHANGELOG_PATH.read_text(encoding="utf-8")
    except OSError:
        return []
    sections: list[dict] = []
    current: dict | None = None
    for line in text.splitlines():
        match = _CHANGELOG_HEADER_RE.match(line)
        if match:
            if current is not None:
                sections.append(current)
            current = {
                "version": match.group(1).strip(),
                "date": (match.group(2) or "").strip() or None,
                "lines": [],
            }
        elif current is not None:
            current["lines"].append(line)
    if current is not None:
        sections.append(current)
    for section in sections:
        section["body"] = "\n".join(section["lines"]).strip()
        section.pop("lines", None)
    return sections


def _normalize_version(value: str) -> str:
    return value.lstrip("vV").strip()


def _changelog_for(version: str | None) -> tuple[str | None, str | None]:
    """Return (notes, date) for the changelog entry matching `version`.

    Matching ignores a leading "v". When there is no exact match (the
    changelog is not kept per release candidate), fall back to the newest
    released section, i.e. the first "## [vX...]" entry that is not the
    Unreleased block. Returns (None, None) when no changelog is available.
    """
    sections = _read_changelog_sections()
    if not sections:
        return None, None
    if version:
        target = _normalize_version(version)
        for section in sections:
            if _normalize_version(section["version"]) == target:
                return (section["body"] or None), section["date"]
    for section in sections:
        if section["version"].lower() != "unreleased":
            return (section["body"] or None), section["date"]
    first = sections[0]
    return (first["body"] or None), first["date"]


def get_status() -> dict:
    """Etat courant : dernier check + paquets systeme en attente
    (hors paquets MurOS qui ont leur propre flux)."""
    state = _load_state()
    all_pkgs = state.get("packages", [])
    sys_pkgs = [p for p in all_pkgs if not _is_muros_pkg(p["name"])]
    return {
        "last_check_at": state.get("last_check_at"),
        "packages": sys_pkgs,
        "packages_count": len(sys_pkgs),
        "apt_available": _apt_available(),
    }


def _dpkg_installed_version(pkg: str) -> str | None:
    try:
        proc = subprocess.run(
            ["dpkg-query", "-W", "-f=${Version}", pkg],
            capture_output=True, text=True, timeout=5,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        pass
    return None


def _parse_version_tuple(version: str) -> tuple:
    """Best-effort semver parser for MurOS tags like '0.9.0-rc97'.

    Returns a tuple suitable for direct comparison: stable releases sort
    above pre-releases of the same base version, and pre-releases sort
    by numeric suffix. Unknown formats sort lowest so they never win
    against a well-formed tag.
    """
    import re
    if not version:
        return (-1,)
    base, _, pre = version.partition("-")
    base_parts: list[int] = []
    for piece in base.split("."):
        m = re.match(r"^(\d+)", piece)
        base_parts.append(int(m.group(1)) if m else 0)
    while len(base_parts) < 3:
        base_parts.append(0)
    # Stable (no pre-release) ranks above any pre-release of the same
    # base: encode as (1, ...) > (0, ...).
    if not pre:
        return (tuple(base_parts), 1, 0)
    m = re.search(r"(\d+)", pre)
    pre_num = int(m.group(1)) if m else 0
    return (tuple(base_parts), 0, pre_num)


def _apt_candidate_version(pkg: str) -> str | None:
    """Return the apt candidate version for `pkg`, or None.

    Reads `apt-cache policy <pkg>` and parses the "Candidate:" line. The
    candidate is whatever apt would install from the configured sources,
    i.e. apt.muros.org for the muros package. This relies on the apt
    metadata being reasonably fresh; callers that need an up-to-date
    answer run `apt-get update` first (check_all does).
    """
    try:
        proc = subprocess.run(
            ["apt-cache", "policy", pkg],
            capture_output=True, text=True, timeout=10,
            env={**os.environ, "LC_ALL": "C"},
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return None
    if proc.returncode != 0:
        return None
    for line in proc.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("Candidate:"):
            value = stripped.split(":", 1)[1].strip()
            if not value or value == "(none)":
                return None
            return value
    return None


def get_muros_status() -> dict:
    """MurOS package state: installed version + apt candidate version.

    The installed version is read via dpkg-query, the candidate version via
    `apt-cache policy muros` (so from apt.muros.org, the same channel as the
    installation). No more calls to GitHub: the UI only offers a "release
    notes" link to the GitHub page of the matching tag.
    """
    state = _load_state()
    pending = [p for p in state.get("packages", []) if _is_muros_pkg(p["name"])]

    installed = _dpkg_installed_version(MUROS_PACKAGE_PREFIX)
    candidate = _apt_candidate_version(MUROS_PACKAGE_PREFIX)
    # Only offer the upgrade when the candidate is strictly newer than
    # the installed version. This protects against transient glitches
    # (mirror lag, metadata not refreshed) that would otherwise propose
    # a no-op or a downgrade.
    upgrade_available = bool(
        installed
        and candidate
        and _parse_version_tuple(candidate) > _parse_version_tuple(installed)
    )

    # Notes for the latest version, read from the shipped changelog. We
    # describe the candidate when an upgrade is available, otherwise the
    # installed version.
    release_notes, release_published_at = _changelog_for(candidate or installed)

    return {
        "apt_available": _apt_available(),
        "installed": installed,
        "candidate": candidate,
        "upgrade_available": upgrade_available,
        "pending_packages": pending,
        "last_check_at": state.get("last_check_at"),
        "deb_url": None,
        "release_notes": release_notes,
        "release_published_at": release_published_at,
    }


def _apt_available() -> bool:
    try:
        subprocess.check_call(["which", "apt-get"], stdout=subprocess.DEVNULL, timeout=2)
        return True
    except (subprocess.SubprocessError, FileNotFoundError):
        return False


def check_all() -> dict:
    """Verification UNIQUE des deux flux de MAJ.

    Combine :
      - apt-get update + apt list --upgradable (paquets Debian)
      - re-fetch de la derniere release GitHub (paquet muros)

    Renvoie un payload combine `{apt: UpdateStatusOut, muros:
    MurosUpdateStatusOut, last_check_at}` pour que l'UI n'ait qu'un seul
    bouton "Verifier" et qu'elle puisse afficher un horodatage commun.
    """
    apt_result = check_updates()
    # check_updates() has just run `apt-get update`, so the candidate version
    # read by get_muros_status (apt-cache policy) is fresh.
    # We sync last_check_at on the same stamp.
    muros_result = get_muros_status()
    return {
        "apt": apt_result,
        "muros": muros_result,
        "last_check_at": apt_result.get("last_check_at"),
    }


def check_updates() -> dict:
    """Lance `apt-get update` + `apt list --upgradable`."""
    env = {**os.environ, "DEBIAN_FRONTEND": "noninteractive", "LC_ALL": "C"}
    # Aggressive timeout: on a production firewall, if apt-get update has not
    # responded within 25s, DNS is down or a mirror is down. Better to fail
    # fast with a clear message than to block a FastAPI worker for 2 minutes
    # (which ends up saturating the whole thread pool).
    try:
        update = subprocess.run(
            ["apt-get", "update", "-q"],
            env=env, capture_output=True, text=True, timeout=25,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            "apt-get update did not respond within 25s. Check that DNS "
            "resolution works (System > DNS) and that the Debian mirrors "
            "are reachable."
        )
    if update.returncode != 0:
        stderr = (update.stderr or "").strip()
        if os.geteuid() != 0:
            raise RuntimeError(
                "apt-get update failed: MurOS must run with root privileges "
                "to manage updates. "
                f"Output: {stderr[:400] or 'none'}"
            )
        raise RuntimeError(f"apt-get update code {update.returncode}: {stderr[:400]}")
    try:
        listing = subprocess.run(
            ["apt", "list", "--upgradable"],
            env=env, capture_output=True, text=True, timeout=12,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("apt list --upgradable did not respond within 12s.")
    if listing.returncode != 0:
        raise RuntimeError(f"apt list --upgradable failed: {(listing.stderr or '').strip()[:400]}")
    out = listing.stdout

    packages = []
    for line in out.splitlines():
        m = _PKG_RE.match(line)
        if m:
            packages.append({
                "name": m.group(1),
                "new_version": m.group(2),
                "current_version": m.group(3),
            })
    state = {
        "last_check_at": datetime.now(timezone.utc).isoformat(),
        "packages": packages,
    }
    _save_state(state)
    return get_status()


def install_updates() -> dict:
    """Applique les MAJ systeme (apt-get upgrade -y) en EXCLUANT les
    paquets MurOS, qui ont leur propre flux de MAJ.

    Declenchement manuel uniquement, jamais en arriere-plan.
    """
    env = {**os.environ, "DEBIAN_FRONTEND": "noninteractive", "LC_ALL": "C"}
    state = _load_state()
    all_pkgs = state.get("packages", [])
    sys_names = [p["name"] for p in all_pkgs if not _is_muros_pkg(p["name"])]
    if not sys_names:
        return {"installed": True, "output_tail": "Rien a installer (aucun paquet systeme en attente)."}

    # `apt-get install <names>` upgrade les paquets cites sans toucher aux
    # autres. C'est plus precis qu'`upgrade` global et evite d'embarquer
    # involontairement les paquets MurOS si l'apt repo MurOS est present.
    cmd = ["apt-get", "-y", "install", "--only-upgrade", *sys_names]
    try:
        proc = subprocess.run(
            cmd, env=env, capture_output=True, text=True, timeout=900,
        )
    except (subprocess.SubprocessError, FileNotFoundError) as exc:
        raise RuntimeError(f"apt-get install failed: {exc}") from exc
    if proc.returncode != 0:
        raise RuntimeError(
            f"apt-get install code {proc.returncode}: {proc.stderr[:400]}"
        )
    # On re-check pour rafraichir le cache (les paquets MurOS restent en
    # attente, eux).
    _save_state({
        "last_check_at": datetime.now(timezone.utc).isoformat(),
        "packages": [p for p in all_pkgs if _is_muros_pkg(p["name"])],
    })
    return {
        "installed": True,
        "output_tail": proc.stdout[-2000:],
    }


def install_muros() -> dict:
    """Update the `muros` package from the signed apt repository (apt.muros.org).

    Steps:
      1. Pre-upgrade snapshot (DB + nftables.conf) via backups.create_backup
      2. apt-get update (refreshes the apt.muros.org metadata)
      3. apt-get install --only-upgrade -y muros (apt handles deps + postinst)

    Integrity verification is ensured by the repository's GPG signature
    (the signed-by keyring), so no more .deb download nor application-side
    SHA-256 check: everything goes through apt, like the installation.
    """
    if not _apt_available():
        raise RuntimeError("apt is not available: cannot update MurOS.")

    # 1. Pre-upgrade snapshot: DB + nftables.conf, timestamped archive.
    # The label carries the version installed BEFORE the upgrade so the admin
    # identifies at a glance what a restore would bring them back to.
    current_pkg_version = _dpkg_installed_version(MUROS_PACKAGE_PREFIX) or "unknown"
    snap_label = f"pre-upgrade-{current_pkg_version}"
    try:
        from app import backups
        snap = backups.create_backup(label=snap_label)
    except Exception as exc:  # noqa: BLE001
        snap = {"name": None, "error": str(exc)}

    # 2. Refresh the apt metadata (apt.muros.org) before the upgrade so the
    # new version is visible even if no check was run recently from the UI.
    env = {**os.environ, "DEBIAN_FRONTEND": "noninteractive", "LC_ALL": "C"}
    try:
        upd = subprocess.run(
            ["apt-get", "update", "-q"],
            env=env, capture_output=True, text=True, timeout=60,
        )
        if upd.returncode != 0:
            raise RuntimeError(
                f"apt-get update failed: {(upd.stderr or '').strip()[:400]}"
            )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("apt-get update did not respond within 60s.") from exc

    # apt install --only-upgrade muros: we can NOT do a blocking subprocess.run
    # here because the postinst of the new .deb will `systemctl restart
    # muros-backend.service`, which sends SIGTERM to the backend (and thus to
    # the apt-get spawned by the backend). apt-get dies with code -15 and dpkg
    # leaves the package in a "half-configured" state.
    #
    # Solution: run apt-get in a detached transient systemd unit
    # (`systemd-run --no-block --unit=muros-self-upgrade`). The unit survives
    # the muros-backend restart, apt does its job to the end, and the UI polls
    # /api/updates/muros/progress to follow progress and
    # reconnect when the new backend responds.
    progress_log = STATE_DIR / "muros-upgrade.log"
    progress_log.parent.mkdir(parents=True, exist_ok=True)
    candidate = _apt_candidate_version(MUROS_PACKAGE_PREFIX) or "latest"
    progress_log.write_text(
        f"# {datetime.now(timezone.utc).isoformat()}: "
        f"apt install --only-upgrade muros (-> {candidate})\n"
    )

    if shutil.which("systemd-run") is None or os.geteuid() != 0:
        # Dev / non-root fallback: we still attempt apt synchronously
        # (and too bad if SIGTERM cuts us off; at least in dev without
        # muros-backend.service the scenario does not exist).
        cmd = ["apt-get", "-y", "install", "--only-upgrade", "muros"]
        try:
            proc = subprocess.run(
                cmd, env=env, capture_output=True, text=True, timeout=600,
            )
        except (subprocess.SubprocessError, FileNotFoundError) as exc:
            raise RuntimeError(f"apt-get install --only-upgrade muros failed: {exc}") from exc
        if proc.returncode != 0:
            raise RuntimeError(
                f"apt-get install code {proc.returncode}: {proc.stderr[:400]}"
            )
        progress_log.write_text(
            progress_log.read_text() + (proc.stdout or "") + "\n# done\n"
        )
        started_detached = False
    else:
        # Run in a detached transient unit. `--collect` cleans up the unit
        # on exit, `--no-block` returns immediately, we redirect stdout/stderr
        # into the log file for UI follow-up.
        cmd = [
            "systemd-run",
            "--collect",
            "--no-block",
            "--unit=muros-self-upgrade",
            "--description=MurOS self upgrade",
            "--setenv=DEBIAN_FRONTEND=noninteractive",
            "--setenv=LC_ALL=C",
            "--property=StandardOutput=append:" + str(progress_log),
            "--property=StandardError=append:" + str(progress_log),
            "apt-get", "-y", "install", "--only-upgrade", "muros",
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        except (subprocess.SubprocessError, FileNotFoundError) as exc:
            raise RuntimeError(
                f"systemd-run failed for the MurOS upgrade: {exc}"
            ) from exc
        if proc.returncode != 0:
            raise RuntimeError(
                f"systemd-run refused to launch the upgrade (code {proc.returncode}): "
                f"{(proc.stderr or proc.stdout or '').strip()[:400]}"
            )
        started_detached = True

    # Reset the MurOS entry from the local pending-packages cache
    state = _load_state()
    state["packages"] = [p for p in state.get("packages", []) if not _is_muros_pkg(p["name"])]
    state["last_check_at"] = datetime.now(timezone.utc).isoformat()
    _save_state(state)

    return {
        "installed": True,
        "snapshot": snap,
        "output_tail": (
            "Update started in the background via systemd-run "
            "(unit muros-self-upgrade.service). The backend will restart "
            "automatically at the end of the postinst, the UI will reconnect "
            "by itself. Follow the progress in "
            "/var/lib/muros/muros-upgrade.log."
        ) if started_detached else "Upgrade applied synchronously (dev).",
    }


def get_muros_install_progress() -> dict:
    """Return the state of the auto-triggered MurOS upgrade.

    Reads both the transient systemd unit `muros-self-upgrade.service`
    (active / done / failed) and the tail of the log file for display
    in the UI.
    """
    log_path = STATE_DIR / "muros-upgrade.log"
    log_tail = ""
    if log_path.is_file():
        try:
            content = log_path.read_text(errors="replace")
            log_tail = content[-4000:]
        except OSError:
            pass

    state = "idle"  # idle | running | done | failed | unknown
    detail = None
    if shutil.which("systemctl"):
        try:
            r = subprocess.run(
                ["systemctl", "show", "muros-self-upgrade.service",
                 "--property=ActiveState,Result,ExecMainStatus"],
                capture_output=True, text=True, timeout=3,
            )
            kv = {}
            for line in r.stdout.splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    kv[k] = v
            active = kv.get("ActiveState", "")
            result = kv.get("Result", "")
            exit_code = kv.get("ExecMainStatus", "")
            detail = f"{active or '?'}/{result or '?'} exit={exit_code or '?'}"
            if active in ("active", "activating", "reloading"):
                state = "running"
            elif active == "failed" or (result and result not in ("success", "")):
                state = "failed"
            elif active == "inactive" and result == "success":
                state = "done"
            elif active in ("inactive", "") and not result:
                # The unit has already been collected (after --collect): we
                # fall back on the log tail to guess whether it went well.
                if log_tail and "# done" in log_tail:
                    state = "done"
                elif "Setting up muros" in log_tail or "Unpacking muros" in log_tail:
                    # We saw apt handle the package, the service was running,
                    # it exited cleanly.
                    state = "done"
                else:
                    state = "idle"
        except (subprocess.SubprocessError, FileNotFoundError):
            state = "unknown"

    # Real dpkg package state, ultimate source of truth after the upgrade.
    pkg_status = None
    if shutil.which("dpkg-query"):
        try:
            r = subprocess.run(
                ["dpkg-query", "-W", "-f=${Status}\\n${Version}", "muros"],
                capture_output=True, text=True, timeout=3,
            )
            if r.returncode == 0:
                lines = r.stdout.splitlines()
                pkg_status = {
                    "status": lines[0] if lines else "",
                    "version": lines[1] if len(lines) > 1 else "",
                }
        except (subprocess.SubprocessError, FileNotFoundError):
            pass

    return {
        "state": state,
        "detail": detail,
        "log_tail": log_tail,
        "package": pkg_status,
    }


def repair_muros_package() -> dict:
    """Reconfigure dpkg packages left in an inconsistent state.

    Typical case: an `apt install muros.deb` launched from muros-backend
    was killed by the service restart in the middle of the postinst. dpkg
    leaves muros in `half-configured`, nothing works anymore until a
    `dpkg --configure -a` is run. This function launches the repair and
    returns the output for display in the UI.
    """
    if not _apt_available():
        raise RuntimeError("apt/dpkg unavailable: repair impossible.")
    if os.geteuid() != 0:
        raise RuntimeError(
            "Repair impossible: MurOS must run as root. "
            "Run manually: sudo dpkg --configure -a"
        )

    env = {**os.environ, "DEBIAN_FRONTEND": "noninteractive", "LC_ALL": "C"}
    log_path = STATE_DIR / "muros-upgrade.log"

    # Repair strategy, from least to most aggressive:
    #   1. dpkg --configure -a            (package "half-configured")
    #   2. dpkg --remove --force-remove-reinstreq muros
    #                                     (package "ReinstReq" without archive)
    #   3. dpkg --purge --force-all muros (last resort)
    # Then clean up the /var/lib/dpkg/info/muros.* leftovers that can block
    # the reinstall.
    # We chain it in a bash one-liner so the sequence is logged consistently
    # in the same log file.
    repair_script = (
        "set +e\n"
        "echo '--- repair muros dpkg ---'; date -Is\n"
        "dpkg-query -W -f='state before: ${Status}\\n' muros 2>/dev/null || true\n"
        "echo '[1] dpkg --configure -a'\n"
        "dpkg --configure -a\n"
        "STATUS=$(dpkg-query -W -f='${Status}' muros 2>/dev/null || echo absent)\n"
        "echo \"state after 1: $STATUS\"\n"
        "case \"$STATUS\" in\n"
        "  *reinstreq*|*half-*|*unpacked*|*triggers-pending*|*failed-config*)\n"
        "    echo '[2] dpkg --remove --force-remove-reinstreq muros'\n"
        "    dpkg --remove --force-remove-reinstreq muros\n"
        "    STATUS=$(dpkg-query -W -f='${Status}' muros 2>/dev/null || echo absent)\n"
        "    echo \"state after 2: $STATUS\"\n"
        "    ;;\n"
        "esac\n"
        "case \"$STATUS\" in\n"
        "  *reinstreq*|*half-*|*unpacked*|*triggers-pending*|*failed-config*)\n"
        "    echo '[3] dpkg --purge --force-all muros'\n"
        "    dpkg --purge --force-all muros\n"
        "    rm -f /var/lib/dpkg/info/muros.*\n"
        "    ;;\n"
        "esac\n"
        "dpkg-query -W -f='final state: ${Status}\\n' muros 2>/dev/null || echo 'muros absent (OK)'\n"
        "echo '--- repair done ---'\n"
    )

    if shutil.which("systemd-run") and shutil.which("dpkg") and shutil.which("bash"):
        # We use bash via systemd-run to chain the case / conditional test
        # cleanly, and survive the muros-backend restart if it ever gets
        # triggered by a step.
        cmd = [
            "systemd-run",
            "--collect",
            "--unit=muros-repair-dpkg",
            "--no-block",
            "--setenv=DEBIAN_FRONTEND=noninteractive",
            "--setenv=LC_ALL=C",
            "--property=StandardOutput=append:" + str(log_path),
            "--property=StandardError=append:" + str(log_path),
            "bash", "-c", repair_script,
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        except (subprocess.SubprocessError, FileNotFoundError) as exc:
            raise RuntimeError(f"systemd-run failed: {exc}") from exc
        if proc.returncode != 0:
            raise RuntimeError(
                f"Failed to launch systemd-run (code {proc.returncode}): "
                f"{(proc.stderr or proc.stdout or '').strip()[:400]}"
            )
        return {
            "started": True,
            "message": (
                "Repair started in the background: configure -a, then "
                "force-remove-reinstreq if needed, then --purge --force-all "
                "as a last resort. Follow /var/lib/muros/muros-upgrade.log."
            ),
        }

    # Fallback without systemd-run: synchronous.
    proc = subprocess.run(
        ["bash", "-c", repair_script],
        env=env, capture_output=True, text=True, timeout=300,
    )
    return {
        "started": True,
        "rc": proc.returncode,
        "output_tail": (proc.stdout + proc.stderr)[-2000:],
    }


# ---------------------------------------------------------------------------
# Background scheduler: periodic check_all()
# ---------------------------------------------------------------------------
# Without this thread, the last_check cache is never refreshed unless the
# admin clicks "Check for updates" in System > Updates. The orange badge next
# to the version in the sidebar would therefore stay cold forever.
#
# The scheduler runs in a daemon thread (dies with the process), runs
# check_all() with a period adjustable via MUROS_UPDATES_INTERVAL_HOURS
# (default 6h, i.e. 4 checks per day). The very first check happens after a
# delay of MUROS_UPDATES_INITIAL_DELAY_SEC (default 60s) to avoid running
# apt-get update right at boot when the network can be unstable.

_updates_thread_lock = threading.Lock()
_updates_thread = None  # Thread | None
_updates_log = logging.getLogger("muros.updates.scheduler")


def _updates_loop(interval_seconds: int, initial_delay: int) -> None:
    """Thread loop: check_all() every interval_seconds."""
    # Sleep first to avoid running apt-get update right at boot.
    time.sleep(initial_delay)
    while True:
        try:
            _updates_log.info("Starting the periodic update check")
            result = check_all()
            apt_n = len(result.get("apt", {}).get("packages", []))
            muros_up = result.get("muros", {}).get("upgrade_available")
            _updates_log.info(
                "Check finished: %d apt package(s), muros upgrade_available=%s",
                apt_n, muros_up,
            )
        except Exception:
            # A failed check (DNS down, mirror down) must not kill the
            # scheduler. Next attempt in interval_seconds.
            _updates_log.exception("Periodic update check failed")
        time.sleep(interval_seconds)


def ensure_updates_checker_started() -> None:
    """Start the periodic check thread if not already running. Idempotent."""
    global _updates_thread
    with _updates_thread_lock:
        if _updates_thread is not None and _updates_thread.is_alive():
            return
        try:
            interval_h = float(os.environ.get("MUROS_UPDATES_INTERVAL_HOURS", "6"))
            initial_delay = int(os.environ.get("MUROS_UPDATES_INITIAL_DELAY_SEC", "60"))
        except (TypeError, ValueError):
            interval_h, initial_delay = 6.0, 60
        interval_s = max(60, int(interval_h * 3600))  # min 1 min for tests
        _updates_thread = threading.Thread(
            target=_updates_loop,
            args=(interval_s, initial_delay),
            name="muros-updates-checker",
            daemon=True,
        )
        _updates_thread.start()
        _updates_log.info(
            "Update scheduler started (interval=%.1fh, first check in %ds)",
            interval_h, initial_delay,
        )
