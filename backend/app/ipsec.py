# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""IPsec via strongSwan (swanctl/vici).

MurOS relies on the Debian package `strongswan` (and its plugin
`strongswan-swanctl` for the modern interface). Tunnels are described in
`/etc/swanctl/conf.d/muros.conf` in the swanctl format, rendered from the
SQLite DB. The `strongswan-starter.service` daemon (Debian) loads the
config at startup, and `swanctl --load-all` allows a hot reload.
"""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

from app.apply import APPLY_ENABLED

log = logging.getLogger("muros.ipsec")

IPSEC_PACKAGES = ["strongswan", "strongswan-swanctl"]
# On Debian 12+, the main service is called strongswan-starter.
# On Debian 11 it was strongswan, we try both.
IPSEC_SERVICES = ["strongswan", "strongswan-starter"]

SWANCTL_CONF = Path("/etc/swanctl/conf.d/muros.conf")
SWANCTL_SECRETS = Path("/etc/swanctl/conf.d/muros.secrets")


from app.service_state import is_active as _systemd_active, which as _which  # noqa: E402


def get_or_create_global_config(db):
    """Fetch the IpsecGlobalConfig singleton, creating it if absent.

    Centralised so every caller (API, muros_boot, apply pipeline)
    observes the same default (enabled=True) when migrating from a
    release that did not have this table.
    """
    from app import models
    cfg = db.get(models.IpsecGlobalConfig, 1)
    if cfg is None:
        cfg = models.IpsecGlobalConfig(id=1, enabled=True)
        db.add(cfg)
        db.commit()
        db.refresh(cfg)
    return cfg


def _ipsec_service_active() -> tuple[bool, str | None]:
    for svc in IPSEC_SERVICES:
        if _systemd_active(svc):
            return True, svc
    return False, None


def _ipsec_service_installed() -> str | None:
    """Return the name of the systemd unit that is actually present, else None.

    Lets us distinguish "unknown service" (nothing installed) from "inactive
    service" (unit present but stopped). We look in order strongswan-starter
    then strongswan: on Debian 12+ the first one really exists, on Debian 11
    it was the second.
    """
    if not _which("systemctl"):
        return None
    for svc in ("strongswan-starter", "strongswan"):
        try:
            r = subprocess.run(
                ["systemctl", "list-unit-files", f"{svc}.service", "--no-legend"],
                text=True, capture_output=True, timeout=3,
            )
            if r.returncode == 0 and r.stdout.strip():
                return svc
        except (subprocess.SubprocessError, FileNotFoundError):
            continue
    return None


def _swanctl_version() -> str | None:
    """strongSwan version via dpkg (source of truth).

    We avoid the `swanctl --version` binary which can exit non-zero
    because of spurious plugin warnings on Debian, giving a misleading
    "version unavailable". The REAL version is the one dpkg installed:
    that is the one we display, and it is instant.
    """
    from app.service_state import pkg_version
    return pkg_version("strongswan", "strongSwan")


def _list_active_sas() -> list[dict]:
    """Return the active Security Associations via `swanctl --list-sas`.

    Raw format, minimal parsing: we keep the connection name and the state.
    Fine-grained parsing will come in phase 2 with a formal model.
    """
    if not _which("swanctl"):
        return []
    try:
        out = subprocess.check_output(
            ["swanctl", "--list-sas"], text=True, timeout=5, stderr=subprocess.STDOUT,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return []

    sas: list[dict] = []
    current: dict | None = None
    for line in out.splitlines():
        if not line:
            continue
        # IKE_SA header line: "name[1]: ESTABLISHED ..."
        if not line.startswith(" ") and ":" in line and "[" in line:
            name = line.split("[", 1)[0].strip()
            rest = line.split(":", 1)[1].strip()
            state = rest.split()[0] if rest else "unknown"
            current = {"name": name, "state": state, "details": rest[:200]}
            sas.append(current)
    return sas


def get_status() -> dict:
    """Live IPsec state: packages, service, version, active SAs.

    `installed` relies solely on swanctl, the modern interface shipped by
    strongswan-swanctl. The legacy `ipsec` binary is no longer distributed
    by default on Debian 12+; testing it would report "not installed" even
    though strongswan runs fine.
    """
    from app.service_state import service_state as _state
    installed = _which("swanctl")
    active, service_name = _ipsec_service_active()
    # If no unit is active, we still look for the installed unit
    # (strongswan-starter on Debian 12+, strongswan on Debian 11) to report
    # a clean "inactive" state instead of an "unknown" that yields
    # "Unknown strongswan service" on the UI side.
    if service_name is None:
        service_name = _ipsec_service_installed()
    return {
        "installed": installed,
        "version": _swanctl_version(),
        "service_active": active,
        "service_state": _state(service_name) if service_name else "unknown",
        "service_name": service_name,
        "active_sas": _list_active_sas(),
        "globally_enabled": _read_global_enabled(),
    }


def _read_global_enabled() -> bool:
    """Lookup the IpsecGlobalConfig singleton without forcing the caller
    to pass a Session. Returns True if the table is missing (compat with
    a freshly migrated install where the row was not created yet)."""
    try:
        from app.db import SessionLocal
        with SessionLocal() as db:
            cfg = get_or_create_global_config(db)
            return bool(cfg.enabled)
    except Exception:  # noqa: BLE001
        return True


def install_packages() -> dict:
    """Installe strongswan + strongswan-swanctl via apt. Idempotente."""
    already = _which("swanctl") and _which("ipsec")
    if already:
        return {
            "installed": True,
            "already_present": IPSEC_PACKAGES,
            "newly_installed": [],
            "output_tail": "",
        }

    if not APPLY_ENABLED:
        return {
            "installed": False,
            "already_present": [],
            "newly_installed": [],
            "output_tail": (
                f"dry-run : aurait execute 'apt-get install -y {' '.join(IPSEC_PACKAGES)}' "
                "(MUROS_APPLY off)."
            ),
        }

    if os.geteuid() != 0:
        raise RuntimeError(
            "Installation impossible: MurOS must run as root. "
            f"Install manually: apt install -y {' '.join(IPSEC_PACKAGES)}"
        )

    try:
        subprocess.check_call(["which", "apt-get"], stdout=subprocess.DEVNULL, timeout=2)
    except (subprocess.SubprocessError, FileNotFoundError) as exc:
        raise RuntimeError(
            "apt-get not found, only supported on Debian/Ubuntu."
        ) from exc

    env = {**os.environ, "DEBIAN_FRONTEND": "noninteractive", "LC_ALL": "C"}
    proc_update = subprocess.run(
        ["apt-get", "update", "-q"],
        env=env, capture_output=True, text=True, timeout=120,
    )
    if proc_update.returncode != 0:
        raise RuntimeError(
            f"apt-get update failed: {(proc_update.stderr or '').strip()[:400]}"
        )

    # For strongswan we keep --no-install-recommends, the recommends include
    # many modules we do not use (charon-cmd, libcharon-extauth-plugins).
    proc = subprocess.run(
        ["apt-get", "install", "-y", "--no-install-recommends", *IPSEC_PACKAGES],
        env=env, capture_output=True, text=True, timeout=600,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"apt-get install failed (code {proc.returncode}): "
            f"{(proc.stderr or '').strip()[:400]}"
        )

    if not (_which("swanctl") and _which("ipsec")):
        raise RuntimeError(
            f"Binaries missing after install: swanctl/ipsec. Output: {proc.stdout[-400:]}"
        )

    return {
        "installed": True,
        "already_present": [],
        "newly_installed": IPSEC_PACKAGES,
        "output_tail": proc.stdout[-800:],
    }


# --- swanctl config rendering ---

def render_swanctl_conf(connections: list, certs_by_id: dict | None = None) -> str:
    """Render the connections { ... } block of the swanctl.conf file.

    connections: list of IpsecConnection (only the enabled ones are included).
    certs_by_id: dict {id: IpsecCert} to resolve local_cert_id and
                 remote_cert_id in auth=cert mode. Optional (None = PSK only).
    """
    from app import ipsec_pki
    if certs_by_id is None:
        certs_by_id = {}

    lines = [
        "# Genere par MurOS - ne pas editer a la main.",
        "# Recharger avec : swanctl --load-all",
        "",
        "connections {",
    ]
    has_enabled = False
    for c in connections:
        if not c.enabled:
            continue
        has_enabled = True
        local_id = c.local_id or c.local_addrs
        remote_id = c.remote_id or c.remote_addrs
        auth_mode = (c.auth_mode or "psk").lower()

        # Local section (auth depends on mode).
        local_lines = ["        local {"]
        if auth_mode == "cert":
            local_cert = certs_by_id.get(c.local_cert_id) if c.local_cert_id else None
            local_lines.append("            auth = pubkey")
            if local_cert:
                local_lines.append(f"            certs = {ipsec_pki.cert_filename(local_cert)}")
            local_lines.append(f"            id = {local_id}")
        else:
            local_lines.append("            auth = psk")
            local_lines.append(f"            id = {local_id}")
        local_lines.append("        }")

        # Section remote.
        remote_lines = ["        remote {"]
        if auth_mode == "cert":
            remote_lines.append("            auth = pubkey")
            # cacerts: the muros CA validates any cert it signed.
            remote_lines.append(f"            cacerts = {ipsec_pki.CA_FILENAME}")
            # If a specific remote cert is expected, add it as an
            # extra validation via id.
            remote_cert = certs_by_id.get(c.remote_cert_id) if c.remote_cert_id else None
            if remote_cert:
                # Force the id to the remote cert CN.
                remote_lines.append(f"            id = {remote_cert.subject_cn}")
            else:
                remote_lines.append(f"            id = {remote_id}")
        else:
            remote_lines.append("            auth = psk")
            remote_lines.append(f"            id = {remote_id}")
        remote_lines.append("        }")

        lines.extend([
            f"    {c.name} {{",
            "        version = 2",
            f"        local_addrs = {c.local_addrs}",
            f"        remote_addrs = {c.remote_addrs}",
            f"        proposals = {c.ike_proposals}",
            *local_lines,
            *remote_lines,
            "        children {",
            f"            {c.name} {{",
            f"                local_ts = {c.local_ts}",
            f"                remote_ts = {c.remote_ts}",
            f"                esp_proposals = {c.esp_proposals}",
            f"                start_action = {c.start_action}",
            "                dpd_action = restart",
            "            }",
            "        }",
            "    }",
        ])
    lines.append("}")
    if not has_enabled:
        lines.append("# (no connection enabled)")
    return "\n".join(lines) + "\n"


def render_swanctl_secrets(connections: list, certs_by_id: dict | None = None) -> str:
    """Render the secrets file.

    - Mode PSK : ike-<name> { secret = "..." id-1 = ... id-2 = ... }
    - Mode cert : private-<name> { file = muros-<cert>-key.pem }
    """
    if certs_by_id is None:
        certs_by_id = {}

    lines = [
        "# Genere par MurOS - ne pas editer a la main.",
        "",
        "secrets {",
    ]
    for c in connections:
        if not c.enabled:
            continue
        auth_mode = (c.auth_mode or "psk").lower()
        if auth_mode == "cert":
            local_cert = certs_by_id.get(c.local_cert_id) if c.local_cert_id else None
            if local_cert and local_cert.is_local and local_cert.key_pem:
                # The file name is <prefix>muros-<name>-key.pem
                safe_name = local_cert.name.replace("/", "_")
                lines.extend([
                    f"    private-{c.name} {{",
                    f"        file = muros-{safe_name}-key.pem",
                    "    }",
                ])
        elif c.psk:
            local_id = c.local_id or c.local_addrs
            remote_id = c.remote_id or c.remote_addrs
            psk_escaped = c.psk.replace('\\', '\\\\').replace('"', '\\"')
            lines.extend([
                f"    ike-{c.name} {{",
                f'        secret = "{psk_escaped}"',
                f"        id-1 = {local_id}",
                f"        id-2 = {remote_id}",
                "    }",
            ])
    lines.append("}")
    return "\n".join(lines) + "\n"


# --- Apply ---

class IpsecApplyError(Exception):
    """Raised when swanctl refuses the rendered configuration.

    Caught by the Apply route and surfaced as a 409, so a bad
    connection definition does not silently leave strongSwan with
    the old config + a stale dirty flag.
    """


def write_conf(connections: list, ca=None, certs: list | None = None,
               revoked_certs: list | None = None) -> dict:
    """Materialise /etc/swanctl/conf.d/muros.conf + secrets + PKI only.

    No swanctl --load-all here, no systemd action. The running daemon
    keeps the previous config until the operator clicks Apply.
    """
    from app import ipsec_pki
    certs = certs or []
    revoked_certs = revoked_certs or []
    certs_by_id = {c.id: c for c in certs}

    conf_text = render_swanctl_conf(connections, certs_by_id)
    secrets_text = render_swanctl_secrets(connections, certs_by_id)

    if not APPLY_ENABLED:
        return {
            "message": f"dry-run : aurait ecrit {SWANCTL_CONF} et {SWANCTL_SECRETS}.",
            "conf_preview": conf_text,
        }

    SWANCTL_CONF.parent.mkdir(parents=True, exist_ok=True)
    SWANCTL_CONF.write_text(conf_text, encoding="utf-8")
    os.chmod(SWANCTL_CONF, 0o600)

    SWANCTL_SECRETS.write_text(secrets_text, encoding="utf-8")
    os.chmod(SWANCTL_SECRETS, 0o600)

    if ca is not None and ca.cert_pem:
        ipsec_pki.deploy_to_disk(ca, certs, revoked_certs)

    return {
        "message": "IPsec configuration saved.",
        "conf_path": str(SWANCTL_CONF),
    }


def reload(connections: list, ca=None, certs: list | None = None,  # noqa: D401
           revoked_certs: list | None = None, *,
           globally_enabled: bool = True) -> dict:
    """Reload swanctl with the on-disk config (or stop strongSwan).

    Called only by the explicit Apply action ; assumes write_conf has
    already been run.
    """
    return apply_config(connections, ca=ca, certs=certs,
                        revoked_certs=revoked_certs,
                        globally_enabled=globally_enabled)


def apply_config(connections: list, ca=None, certs: list | None = None,
                 revoked_certs: list | None = None, *,
                 defer_start: bool = False,
                 globally_enabled: bool = True) -> dict:
    """Write the swanctl files and hot-reload via swanctl --load-all.

    If ca and certs are provided, also deploy the PKI (CA + certs + CRL)
    into /etc/swanctl/x509ca/, x509/, private/, x509crl/.

    In dry-run: return the content without writing.

    defer_start: in boot context (muros-boot.service with
    Before=network-online.target), we cannot run
    `systemctl enable --now strongswan` because the service has
    After=network-online.target -> 15s deadlock. We then split
    enable (persistence) and start (--no-block, non-blocking).
    """
    from app import ipsec_pki
    certs = certs or []
    revoked_certs = revoked_certs or []
    certs_by_id = {c.id: c for c in certs}

    # If a connection is in cert mode, we need the PKI.
    needs_pki = any(
        (c.auth_mode or "psk").lower() == "cert"
        for c in connections if c.enabled
    )
    if needs_pki and (ca is None or not ca.cert_pem):
        raise RuntimeError(
            "A connection is in certificate mode but the MurOS CA has not "
            "been generated. Generate the CA from the PKI tab first."
        )

    conf_text = render_swanctl_conf(connections, certs_by_id)
    secrets_text = render_swanctl_secrets(connections, certs_by_id)

    if not APPLY_ENABLED:
        return {
            "message": f"dry-run : aurait ecrit {SWANCTL_CONF} et {SWANCTL_SECRETS}.",
            "conf_preview": conf_text,
        }

    # Prerequisite: swanctl present + service running. If the service is not
    # there, we still write the files (they will be read at the next startup)
    # and we warn.
    if not _which("swanctl"):
        raise RuntimeError(
            "swanctl not found. Install strongswan-swanctl first "
            "('Install now' button)."
        )

    SWANCTL_CONF.parent.mkdir(parents=True, exist_ok=True)
    SWANCTL_CONF.write_text(conf_text, encoding="utf-8")
    os.chmod(SWANCTL_CONF, 0o600)

    SWANCTL_SECRETS.write_text(secrets_text, encoding="utf-8")
    os.chmod(SWANCTL_SECRETS, 0o600)

    # Deploy the PKI if we have a CA (even without a cert connection, we
    # keep the CA ready for the future).
    if ca is not None and ca.cert_pem:
        ipsec_pki.deploy_to_disk(ca, certs, revoked_certs)

    nb_enabled = len([c for c in connections if c.enabled])
    active, svc_name = _ipsec_service_active()

    # Global toggle off : tear down the service no matter how many
    # connections are enabled in DB. Keeps the swanctl conf on disk so
    # the operator can still see/edit it, but disables the unit so it
    # does not come back at reboot.
    if not globally_enabled:
        for s in IPSEC_SERVICES:
            subprocess.run(
                ["systemctl", "disable", "--now", s],
                capture_output=True, text=True, timeout=15,
            )
        return {
            "message": (
                "IPsec server globally disabled : strongswan stopped and "
                "disabled at boot (configuration preserved)."
            ),
            "service": svc_name or "",
        }

    if nb_enabled == 0:
        # No active connection left: shut strongswan down completely.
        for s in IPSEC_SERVICES:
            subprocess.run(
                ["systemctl", "disable", "--now", s],
                capture_output=True, text=True, timeout=15,
            )
        return {
            "message": "IPsec configuration saved, no active connection: strongswan disabled.",
            "service": svc_name or "",
        }

    # enable + start (persistent + immediate). Try both service names just
    # in case (strongswan vs strongswan-starter depending on the distro).
    target_svc = svc_name or "strongswan-starter"
    if not active:
        for s in IPSEC_SERVICES:
            # enable (persistence) without --now: does not touch the runtime
            # state, depends on no target, safe in boot context.
            r_en = subprocess.run(
                ["systemctl", "enable", s],
                capture_output=True, text=True, timeout=5,
            )
            if r_en.returncode != 0:
                # service name does not exist: try the next one
                continue
            target_svc = s
            # Startup: --no-block in boot context (returns immediately,
            # systemd will run the start after muros-boot once
            # network-online.target is reached; no deadlock).
            start_cmd = ["systemctl", "start", s]
            if defer_start:
                start_cmd.insert(2, "--no-block")
            r_start = subprocess.run(
                start_cmd, capture_output=True, text=True,
                timeout=5 if defer_start else 15,
            )
            if r_start.returncode == 0:
                break

    # Hot reload of connections and secrets. In boot context with deferred
    # startup, the daemon is not running yet so the vici socket does not
    # exist: we skip --load-all, the config will be loaded by strongswan
    # itself at its startup.
    if defer_start:
        return {
            "message": (
                f"IPsec configuration written, deferred startup of {target_svc} "
                f"({nb_enabled} active connection(s))."
            ),
            "service": target_svc,
        }
    res = subprocess.run(
        ["swanctl", "--load-all"], capture_output=True, text=True, timeout=30,
    )
    if res.returncode != 0:
        raise RuntimeError(
            f"swanctl --load-all failed: {(res.stderr or res.stdout).strip()[:400]}"
        )

    return {
        "message": f"IPsec configuration reloaded ({nb_enabled} active connection(s)).",
        "service": target_svc,
        "swanctl_output": (res.stdout or res.stderr).strip()[:400],
    }
