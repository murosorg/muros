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

_PKG_RE = re.compile(r"^([^/]+)/[^ ]+ ([^ ]+) [^ ]+ \[upgradable from: ([^\]]+)\]")

# Prefixe des paquets MurOS, geres par un canal de MAJ distinct (GitHub
# Releases pour l'instant, repo apt plus tard). On les exclut du flux MAJ
# systeme pour que l'admin distingue clairement les deux types de mises
# a jour.
MUROS_PACKAGE_PREFIX = "muros"

# URL de l'API GitHub qui renvoie la derniere release MurOS. Surchargeable
# par env si on bascule plus tard sur un repo apt MurOS ou un miroir.
MUROS_RELEASE_API_URL = os.environ.get(
    "MUROS_RELEASE_API_URL",
    "https://api.github.com/repos/murosorg/muros/releases/latest",
)

# List endpoint used in priority over /latest. GitHub's /latest tracks
# the most recently *published* release, which is not necessarily the
# highest semver when several tagged builds finish out of order in
# parallel CI jobs (a common case for back-to-back rc tags). Fetching
# the list and picking the highest semver tag avoids proposing a
# downgrade like "rc96 -> rc94".
MUROS_RELEASE_LIST_URL = os.environ.get(
    "MUROS_RELEASE_LIST_URL",
    "https://api.github.com/repos/murosorg/muros/releases?per_page=20",
)

# Repo slug used by the HTML-redirect fallback when the REST API is
# rate-limited. The unauthenticated GitHub API is capped at 60 req/h
# per IP and trivially returns 403 in shared-IP / NAT environments;
# the HTML endpoint /releases/latest is a 302 redirect to
# /releases/tag/<TAG>, served from the CDN with no rate limit.
MUROS_RELEASE_REPO = os.environ.get("MUROS_RELEASE_REPO", "murosorg/muros")

# Cache local des .deb telecharges avant install. Stocke a cote de la DB
# pour partager le meme device (eviter un cross-fs move).
MUROS_DEB_CACHE = Path(os.environ.get(
    "MUROS_DEB_CACHE", "/var/cache/muros/upgrade",
))


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


def _fetch_latest_release_via_html() -> dict | None:
    """Fallback resolver when api.github.com is rate-limited (403/429).

    We HEAD `https://github.com/<repo>/releases/latest`: GitHub answers
    with a 302 redirect to `/releases/tag/<TAG>`. We extract <TAG> from
    the final URL and reconstruct the asset URLs by convention (same
    naming scheme produced by our `build-deb.yml` workflow). Returns a
    dict shaped like the GitHub REST payload so the rest of the code
    keeps working unchanged.
    """
    import urllib.request
    import urllib.error

    url = f"https://github.com/{MUROS_RELEASE_REPO}/releases/latest"
    try:
        # HEAD request; urllib follows the 302 automatically and exposes
        # the final URL via `resp.geturl()`.
        req = urllib.request.Request(
            url, method="HEAD", headers={"User-Agent": "muros-updater"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            final_url = resp.geturl() or ""
    except (urllib.error.URLError, OSError):
        return None

    marker = "/releases/tag/"
    if marker not in final_url:
        return None
    tag = final_url.rsplit(marker, 1)[1].split("/", 1)[0]
    if not tag.startswith("v"):
        return None
    version = tag.lstrip("v")
    base = f"https://github.com/{MUROS_RELEASE_REPO}/releases/download/{tag}"
    deb_name = f"muros_{version}_all.deb"
    return {
        "tag_name": tag,
        "published_at": None,
        "body": "",
        "assets": [
            {"name": deb_name, "browser_download_url": f"{base}/{deb_name}"},
            {"name": deb_name + ".sha256",
             "browser_download_url": f"{base}/{deb_name}.sha256"},
        ],
    }


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


def _fetch_release_list() -> list[dict] | None:
    """Fetch the recent releases as a list and return the raw payload.

    Used in priority over /latest because /latest tracks publish time,
    not semver. Returns None on network / parse / rate-limit failure so
    the caller can fall back to the single-release path.
    """
    import json
    import urllib.request
    import urllib.error

    req = urllib.request.Request(
        MUROS_RELEASE_LIST_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "muros-updater",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError,
            json.JSONDecodeError, OSError):
        return None
    if not isinstance(payload, list):
        return None
    # Exclude drafts and prereleases flagged as such on GitHub.
    return [
        r for r in payload
        if isinstance(r, dict)
        and not r.get("draft")
        and not r.get("prerelease")
    ]


def _fetch_latest_release() -> dict | None:
    """Resolve the candidate release for MurOS upgrades.

    Strategy:
    1. Fetch the recent release list and pick the highest semver tag.
       This is robust against parallel CI jobs that publish out of
       order (e.g. rc94 published after rc96 because its workflow ran
       longer; /latest would then point to rc94 and silently propose a
       downgrade).
    2. If the list endpoint fails (network, rate limit, parse error),
       fall back to /releases/latest.
    3. If that also fails, fall back to the HTML 302 redirect.
    """
    import json
    import urllib.request
    import urllib.error

    # 1) Preferred path: list + semver pick.
    releases = _fetch_release_list()
    data: dict | None = None
    if releases:
        # Highest semver wins. Ties are broken by published_at descending.
        releases.sort(
            key=lambda r: (
                _parse_version_tuple((r.get("tag_name") or "").lstrip("v")),
                r.get("published_at") or "",
            ),
            reverse=True,
        )
        data = releases[0]

    # 2) Fall back to /releases/latest when the list endpoint is unavailable.
    if data is None:
        req = urllib.request.Request(
            MUROS_RELEASE_API_URL,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "muros-updater",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 429):
                data = _fetch_latest_release_via_html()
            else:
                return None
        except (urllib.error.URLError, json.JSONDecodeError, OSError):
            data = _fetch_latest_release_via_html()
    if not data:
        return None

    tag = data.get("tag_name") or ""
    version = tag.lstrip("v")
    deb_url: str | None = None
    sha_url: str | None = None
    for asset in data.get("assets") or []:
        name = asset.get("name") or ""
        if name.endswith(".deb") and name.startswith("muros"):
            deb_url = asset.get("browser_download_url")
        elif name.endswith(".sha256") and name.startswith("muros"):
            sha_url = asset.get("browser_download_url")
    return {
        "tag": tag,
        "version": version,
        "deb_url": deb_url,
        "sha256_url": sha_url,
        "published_at": data.get("published_at"),
        "notes": (data.get("body") or "")[:4000],
    }


def get_muros_status() -> dict:
    """Etat des paquets MurOS : version installee + version disponible.

    La version installee est lue via dpkg-query. La version disponible
    est recuperee depuis l'API GitHub Releases (publique, 60 req/h).
    Le checksum SHA-256 et le changelog sont ramenes en meme temps pour
    affichage dans l'UI.
    """
    state = _load_state()
    pending = [p for p in state.get("packages", []) if _is_muros_pkg(p["name"])]

    installed = _dpkg_installed_version(MUROS_PACKAGE_PREFIX)
    release = _fetch_latest_release()
    candidate = release["version"] if release else None
    # Only offer the upgrade when the candidate is strictly newer than
    # the installed version. This protects against transient resolver
    # glitches (CI race, mirror lag, hand-crafted tag) that would
    # otherwise silently propose a downgrade.
    upgrade_available = bool(
        installed
        and candidate
        and _parse_version_tuple(candidate) > _parse_version_tuple(installed)
    )

    return {
        "apt_available": _apt_available(),
        "installed": installed,
        "candidate": candidate,
        "upgrade_available": upgrade_available,
        "pending_packages": pending,
        "last_check_at": state.get("last_check_at"),
        "deb_url": release["deb_url"] if release else None,
        "release_notes": release["notes"] if release else None,
        "release_published_at": release["published_at"] if release else None,
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
    # Force un re-fetch GitHub en remettant le get_muros_status apres :
    # il interroge l'API GitHub a chaque appel donc on est garantis d'avoir
    # la derniere info. On synchronise last_check_at sur le meme stamp.
    muros_result = get_muros_status()
    return {
        "apt": apt_result,
        "muros": muros_result,
        "last_check_at": apt_result.get("last_check_at"),
    }


def check_updates() -> dict:
    """Lance `apt-get update` + `apt list --upgradable`."""
    env = {**os.environ, "DEBIAN_FRONTEND": "noninteractive", "LC_ALL": "C"}
    # Timeout agressif : sur un firewall en prod, si apt-get update n'a
    # pas repondu en 25s, c'est que DNS est HS ou un mirror down. Mieux
    # vaut echouer vite avec un message clair que de bloquer un worker
    # FastAPI 2 minutes (ce qui finit par saturer tout le thread pool).
    try:
        update = subprocess.run(
            ["apt-get", "update", "-q"],
            env=env, capture_output=True, text=True, timeout=25,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            "apt-get update n'a pas repondu en 25s. Verifiez que la "
            "resolution DNS fonctionne (Systeme > DNS) et que les "
            "mirrors Debian sont joignables."
        )
    if update.returncode != 0:
        stderr = (update.stderr or "").strip()
        if os.geteuid() != 0:
            raise RuntimeError(
                "apt-get update a echoue : MurOS doit tourner avec les droits root "
                "pour gerer les mises a jour. "
                f"Sortie : {stderr[:400] or 'aucune'}"
            )
        raise RuntimeError(f"apt-get update code {update.returncode} : {stderr[:400]}")
    try:
        listing = subprocess.run(
            ["apt", "list", "--upgradable"],
            env=env, capture_output=True, text=True, timeout=12,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("apt list --upgradable n'a pas repondu en 12s.")
    if listing.returncode != 0:
        raise RuntimeError(f"apt list --upgradable a echoue : {(listing.stderr or '').strip()[:400]}")
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
        raise RuntimeError(f"apt-get install a echoue : {exc}") from exc
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


def _download(url: str, dest: Path, timeout: int = 120) -> None:
    """Telecharge `url` vers `dest`. Crash si reponse non-200."""
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": "muros-updater"})
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status} en telechargeant {url}")
        with dest.open("wb") as fh:
            while True:
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                fh.write(chunk)


def _sha256_of(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def install_muros() -> dict:
    """Installe la derniere version du paquet `muros` depuis GitHub Releases.

    Etapes :
      1. Snapshot pre-upgrade (DB + nftables.conf) via backups.create_backup
      2. Recupere la derniere release via l'API GitHub
      3. Telecharge le .deb et son fichier .sha256
      4. Verifie le checksum
      5. apt install -y ./muros_X.Y.Z_all.deb (apt gere deps + postinst)

    Pas de repo apt requis : tout passe par github.com.
    """
    if not _apt_available():
        raise RuntimeError("apt n'est pas disponible : impossible de mettre a jour MurOS.")

    release = _fetch_latest_release()
    if not release or not release.get("deb_url"):
        raise RuntimeError(
            "Aucune release MurOS publiee detectee sur GitHub. Verifier "
            f"MUROS_RELEASE_API_URL ({MUROS_RELEASE_API_URL})."
        )

    # 1. Snapshot pre-upgrade : DB + nftables.conf, archive horodatee.
    # Label porte la version installee AVANT upgrade pour que l'admin
    # identifie d'un coup d'oeil ce vers quoi le restore le ramene.
    current_pkg_version = _dpkg_installed_version(MUROS_PACKAGE_PREFIX) or "unknown"
    snap_label = f"pre-upgrade-{current_pkg_version}"
    try:
        from app import backups
        snap = backups.create_backup(label=snap_label)
    except Exception as exc:  # noqa: BLE001
        snap = {"name": None, "error": str(exc)}

    # 2 & 3. Telecharge le .deb dans le cache local
    MUROS_DEB_CACHE.mkdir(parents=True, exist_ok=True)
    deb_name = release["deb_url"].rsplit("/", 1)[-1]
    deb_path = MUROS_DEB_CACHE / deb_name
    try:
        _download(release["deb_url"], deb_path, timeout=300)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Echec du telechargement du .deb : {exc}") from exc

    # 4. Verifie le checksum SHA-256 si publie a cote du .deb
    if release.get("sha256_url"):
        try:
            sha_path = MUROS_DEB_CACHE / (deb_name + ".sha256")
            _download(release["sha256_url"], sha_path, timeout=30)
            expected = sha_path.read_text().strip().split()[0].lower()
            actual = _sha256_of(deb_path).lower()
            if expected != actual:
                deb_path.unlink(missing_ok=True)
                raise RuntimeError(
                    f"Invalid SHA-256 checksum: expected {expected}, got {actual}"
                )
        except RuntimeError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"Echec verification SHA-256 : {exc}") from exc

    # 5. apt install ./muros.deb : on ne peut PAS faire un subprocess.run
    # bloquant ici car le postinst du nouveau .deb va `systemctl restart
    # muros-backend.service`, ce qui envoie SIGTERM au backend (et donc a
    # l'apt-get spawn par le backend). apt-get meurt en code -15 et dpkg
    # laisse le paquet en etat "half-configured".
    #
    # Solution : lancer apt-get dans une unit transient systemd detachee
    # (`systemd-run --no-block --unit=muros-self-upgrade`). L'unit survit
    # au restart de muros-backend, apt fait son boulot jusqu'au bout, et
    # l'UI poll /api/updates/muros/progress pour suivre l'avancement et
    # se reconnecter quand le nouveau backend repond.
    progress_log = STATE_DIR / "muros-upgrade.log"
    progress_log.parent.mkdir(parents=True, exist_ok=True)
    progress_log.write_text(
        f"# {datetime.now(timezone.utc).isoformat()} : upgrade vers {deb_path.name}\n"
    )

    if shutil.which("systemd-run") is None or os.geteuid() != 0:
        # Fallback dev / non-root : on tente quand meme l'apt en synchrone
        # (et tant pis si SIGTERM nous coupe ; au moins en dev sans
        # muros-backend.service le scenario n'existe pas).
        env = {**os.environ, "DEBIAN_FRONTEND": "noninteractive", "LC_ALL": "C"}
        cmd = ["apt-get", "-y", "install", str(deb_path)]
        try:
            proc = subprocess.run(
                cmd, env=env, capture_output=True, text=True, timeout=600,
            )
        except (subprocess.SubprocessError, FileNotFoundError) as exc:
            raise RuntimeError(f"apt-get install ./muros.deb a echoue : {exc}") from exc
        if proc.returncode != 0:
            raise RuntimeError(
                f"apt-get install code {proc.returncode}: {proc.stderr[:400]}"
            )
        progress_log.write_text(
            progress_log.read_text() + (proc.stdout or "") + "\n# done\n"
        )
        started_detached = False
    else:
        # Lance dans une unit transient detachee. `--collect` nettoie la
        # unit au exit, `--no-block` rend la main immediatement, on
        # redirige stdout/stderr dans le fichier de log pour suivi UI.
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
            "apt-get", "-y", "install", str(deb_path),
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        except (subprocess.SubprocessError, FileNotFoundError) as exc:
            raise RuntimeError(
                f"systemd-run a echoue pour l'upgrade MurOS : {exc}"
            ) from exc
        if proc.returncode != 0:
            raise RuntimeError(
                f"systemd-run a refuse de lancer l'upgrade (code {proc.returncode}) : "
                f"{(proc.stderr or proc.stdout or '').strip()[:400]}"
            )
        started_detached = True

    # Reset entry MurOS du cache local de paquets en attente
    state = _load_state()
    state["packages"] = [p for p in state.get("packages", []) if not _is_muros_pkg(p["name"])]
    state["last_check_at"] = datetime.now(timezone.utc).isoformat()
    _save_state(state)

    return {
        "installed": True,
        "snapshot": snap,
        "output_tail": (
            "Mise a jour lancee en arriere-plan via systemd-run "
            "(unit muros-self-upgrade.service). Le backend va redemarrer "
            "automatiquement a la fin du postinst, l'UI se reconnectera "
            "toute seule. Suivre la progression dans "
            "/var/lib/muros/muros-upgrade.log."
        ) if started_detached else "Upgrade applique en mode synchrone (dev).",
    }


def get_muros_install_progress() -> dict:
    """Retourne l'etat de l'upgrade auto-declenchee de MurOS.

    Lit a la fois la unit systemd transient `muros-self-upgrade.service`
    (active / done / failed) et le tail du fichier de log pour
    visualisation cote UI.
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
                # La unit a deja ete collectee (apres --collect) : on se
                # rabat sur le tail du log pour deviner si ca s'est bien
                # passe.
                if log_tail and "# done" in log_tail:
                    state = "done"
                elif "Setting up muros" in log_tail or "Unpacking muros" in log_tail:
                    # On a vu apt manipuler le paquet, le service tournait,
                    # il est sorti propre.
                    state = "done"
                else:
                    state = "idle"
        except (subprocess.SubprocessError, FileNotFoundError):
            state = "unknown"

    # Dpkg etat reel du paquet, source de verite ultime apres l'upgrade.
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
    """Reconfigure les paquets dpkg laisses en etat incoherent.

    Cas typique : un `apt install muros.deb` lance depuis muros-backend
    a ete tue par le restart du service en plein postinst. dpkg laisse
    muros en `half-configured`, plus rien ne marche tant qu'on n'a pas
    fait `dpkg --configure -a`. Cette fonction lance la reparation et
    renvoie la sortie pour affichage UI.
    """
    if not _apt_available():
        raise RuntimeError("apt/dpkg indisponibles : reparation impossible.")
    if os.geteuid() != 0:
        raise RuntimeError(
            "Reparation impossible : MurOS doit tourner en root. "
            "Lancer manuellement : sudo dpkg --configure -a"
        )

    env = {**os.environ, "DEBIAN_FRONTEND": "noninteractive", "LC_ALL": "C"}
    log_path = STATE_DIR / "muros-upgrade.log"

    # Strategie de reparation, du moins agressif au plus agressif :
    #   1. dpkg --configure -a            (paquet "half-configured")
    #   2. dpkg --remove --force-remove-reinstreq muros
    #                                     (paquet "ReinstReq" sans archive)
    #   3. dpkg --purge --force-all muros (dernier recours)
    # Puis nettoyage des reliquats /var/lib/dpkg/info/muros.* qui peuvent
    # bloquer la reinstall.
    # On enchaine en bash one-liner pour que la suite soit logguee de
    # facon coherente dans le meme fichier de log.
    repair_script = (
        "set +e\n"
        "echo '--- repair muros dpkg ---'; date -Is\n"
        "dpkg-query -W -f='etat avant: ${Status}\\n' muros 2>/dev/null || true\n"
        "echo '[1] dpkg --configure -a'\n"
        "dpkg --configure -a\n"
        "STATUS=$(dpkg-query -W -f='${Status}' muros 2>/dev/null || echo absent)\n"
        "echo \"etat apres 1: $STATUS\"\n"
        "case \"$STATUS\" in\n"
        "  *reinstreq*|*half-*|*unpacked*|*triggers-pending*|*failed-config*)\n"
        "    echo '[2] dpkg --remove --force-remove-reinstreq muros'\n"
        "    dpkg --remove --force-remove-reinstreq muros\n"
        "    STATUS=$(dpkg-query -W -f='${Status}' muros 2>/dev/null || echo absent)\n"
        "    echo \"etat apres 2: $STATUS\"\n"
        "    ;;\n"
        "esac\n"
        "case \"$STATUS\" in\n"
        "  *reinstreq*|*half-*|*unpacked*|*triggers-pending*|*failed-config*)\n"
        "    echo '[3] dpkg --purge --force-all muros'\n"
        "    dpkg --purge --force-all muros\n"
        "    rm -f /var/lib/dpkg/info/muros.*\n"
        "    ;;\n"
        "esac\n"
        "dpkg-query -W -f='etat final: ${Status}\\n' muros 2>/dev/null || echo 'muros absent (OK)'\n"
        "echo '--- repair done ---'\n"
    )

    if shutil.which("systemd-run") and shutil.which("dpkg") and shutil.which("bash"):
        # On utilise bash via systemd-run pour pouvoir enchainer le case
        # / le test conditionnel proprement, et survivre au restart de
        # muros-backend si jamais il est declenche par une etape.
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
            raise RuntimeError(f"systemd-run a echoue : {exc}") from exc
        if proc.returncode != 0:
            raise RuntimeError(
                f"Echec lancement systemd-run (code {proc.returncode}) : "
                f"{(proc.stderr or proc.stdout or '').strip()[:400]}"
            )
        return {
            "started": True,
            "message": (
                "Reparation lancee en arriere-plan : configure -a, puis "
                "force-remove-reinstreq si necessaire, puis --purge --force-all "
                "en dernier recours. Suivre /var/lib/muros/muros-upgrade.log."
            ),
        }

    # Fallback sans systemd-run : synchrone.
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
# Scheduler background : check_all() periodique
# ---------------------------------------------------------------------------
# Sans ce thread, le cache last_check n'est jamais rafraichi sauf si l'admin
# clique "Verifier les MAJ" dans System > Mises a jour. Le badge orange a
# cote de la version dans la sidebar resterait donc froid a vie.
#
# Le scheduler tourne en daemon thread (meurt avec le process), lance
# check_all() avec une periode reglable via MUROS_UPDATES_INTERVAL_HOURS
# (defaut 6h, soit 4 checks par jour). Le tout premier check a lieu apres
# un delai de MUROS_UPDATES_INITIAL_DELAY_SEC (defaut 60s) pour eviter de
# lancer apt-get update juste au boot quand le reseau peut etre instable.

_updates_thread_lock = threading.Lock()
_updates_thread = None  # Thread | None
_updates_log = logging.getLogger("muros.updates.scheduler")


def _updates_loop(interval_seconds: int, initial_delay: int) -> None:
    """Boucle thread : check_all() toutes les interval_seconds."""
    # On dort d'abord pour ne pas lancer apt-get update juste au boot.
    time.sleep(initial_delay)
    while True:
        try:
            _updates_log.info("Lancement du check periodique des MAJ")
            result = check_all()
            apt_n = len(result.get("apt", {}).get("packages", []))
            muros_up = result.get("muros", {}).get("upgrade_available")
            _updates_log.info(
                "Check termine : %d paquet(s) apt, muros upgrade_available=%s",
                apt_n, muros_up,
            )
        except Exception:
            # Un check rate (DNS HS, mirror down) ne doit pas tuer le
            # scheduler. Prochaine tentative dans interval_seconds.
            _updates_log.exception("Echec du check periodique des MAJ")
        time.sleep(interval_seconds)


def ensure_updates_checker_started() -> None:
    """Demarre le thread de check periodique si pas deja lance. Idempotent."""
    global _updates_thread
    with _updates_thread_lock:
        if _updates_thread is not None and _updates_thread.is_alive():
            return
        try:
            interval_h = float(os.environ.get("MUROS_UPDATES_INTERVAL_HOURS", "6"))
            initial_delay = int(os.environ.get("MUROS_UPDATES_INITIAL_DELAY_SEC", "60"))
        except (TypeError, ValueError):
            interval_h, initial_delay = 6.0, 60
        interval_s = max(60, int(interval_h * 3600))  # min 1 min pour les tests
        _updates_thread = threading.Thread(
            target=_updates_loop,
            args=(interval_s, initial_delay),
            name="muros-updates-checker",
            daemon=True,
        )
        _updates_thread.start()
        _updates_log.info(
            "Scheduler MAJ demarre (interval=%.1fh, premier check dans %ds)",
            interval_h, initial_delay,
        )
