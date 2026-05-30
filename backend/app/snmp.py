# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""SNMP : exposition des metriques systeme via snmpd standard Debian.

MurOS n'invente pas de MIB custom. On configure le daemon snmpd Debian
standard avec une community v2c en lecture seule, restreinte par CIDR.
Les OIDs exposes sont ceux fournis nativement par snmpd :
  - HOST-RESOURCES-MIB (CPU, RAM, processes)
  - IF-MIB (interfaces reseau, trafic)
  - UCD-SNMP-MIB (load average, disk)
  - SYSTEM-MIB (uptime, contact, location)

Le fichier de conf est ecrit dans /etc/snmp/snmpd.conf.d/muros.conf
(drop-in standard supporte par snmpd Debian).
"""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

from app.apply import APPLY_ENABLED

log = logging.getLogger("muros.snmp")

SNMP_PACKAGES = ["snmpd", "snmp"]
SNMP_SERVICE = "snmpd"
SNMP_CONF = Path("/etc/snmp/snmpd.conf.d/muros.conf")
SNMP_MAIN_CONF = Path("/etc/snmp/snmpd.conf")
INCLUDE_DIR_DIRECTIVE = "includeDir /etc/snmp/snmpd.conf.d"


def _prepare_main_conf() -> bool:
    """Prepare /etc/snmp/snmpd.conf pour cohabiter avec notre drop-in :

    1. Ajoute 'includeDir /etc/snmp/snmpd.conf.d' s'il manque (snmpd ne lit
       pas le dossier .d. par defaut, contrairement a sshd/systemd).
    2. Commente toute directive 'agentAddress' / 'agentaddress' active pour
       eviter le conflit de port quand notre drop-in en pose une nouvelle
       (Error opening specified endpoint: udp:161).
    3. Backup .muros-bak la premiere fois.

    Retourne True si on a modifie le fichier.
    """
    if not APPLY_ENABLED:
        return False
    if not SNMP_MAIN_CONF.exists():
        return False
    try:
        content = SNMP_MAIN_CONF.read_text(encoding="utf-8")
    except OSError:
        return False

    lines = content.splitlines()
    new_lines = list(lines)
    changed = False
    has_includedir = False

    for i, raw in enumerate(lines):
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        kw = s.split()[0].lower() if s.split() else ""
        # Commente toute agentAddress / agentaddress active
        if kw in ("agentaddress",):
            new_lines[i] = "# " + raw + "  # commente par MurOS (conflit avec drop-in)"
            changed = True
        elif kw == "includedir" and "snmpd.conf.d" in s.lower():
            has_includedir = True

    if not has_includedir:
        new_lines.append("")
        new_lines.append("# Ajoute par MurOS pour charger /etc/snmp/snmpd.conf.d/*.conf")
        new_lines.append(INCLUDE_DIR_DIRECTIVE)
        changed = True

    if not changed:
        return False

    # Backup une seule fois
    bak = SNMP_MAIN_CONF.with_suffix(SNMP_MAIN_CONF.suffix + ".muros-bak")
    try:
        if not bak.exists():
            import shutil
            shutil.copy2(SNMP_MAIN_CONF, bak)
    except OSError:
        pass

    SNMP_MAIN_CONF.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return True


# Alias retro-compat pour les appels existants
_ensure_includedir_in_main_conf = _prepare_main_conf


from app.service_state import is_active as _systemd_active, which as _which  # noqa: E402


def _snmpd_version() -> str | None:
    """Version snmpd via dpkg."""
    from app.service_state import pkg_version
    return pkg_version("snmpd")


def get_status() -> dict:
    """Etat live SNMP : paquets installes, service, version."""
    from app.service_state import service_state as _state
    installed_snmpd = _which("snmpd")
    installed_snmp = _which("snmpget") or _which("snmpwalk")
    return {
        "installed": installed_snmpd,
        "snmpd_installed": installed_snmpd,
        "snmp_tools_installed": installed_snmp,
        "service_active": _systemd_active(SNMP_SERVICE),
        "service_state": _state(SNMP_SERVICE),
        "version": _snmpd_version(),
    }


def install_packages() -> dict:
    """Installe snmpd + snmp (outils client snmpget/snmpwalk) via apt."""
    already = _which("snmpd")
    if already:
        return {
            "installed": True,
            "already_present": SNMP_PACKAGES,
            "newly_installed": [],
            "output_tail": "",
        }

    if not APPLY_ENABLED:
        return {
            "installed": False,
            "already_present": [],
            "newly_installed": [],
            "output_tail": (
                f"dry-run : aurait execute 'apt-get install -y {' '.join(SNMP_PACKAGES)}' "
                "(MUROS_APPLY off)."
            ),
        }

    if os.geteuid() != 0:
        raise RuntimeError(
            "Installation impossible : MurOS doit tourner en root. "
            f"Installer manuellement : apt install -y {' '.join(SNMP_PACKAGES)}"
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
            f"apt-get update a echoue : {(proc_update.stderr or '').strip()[:400]}"
        )

    proc = subprocess.run(
        ["apt-get", "install", "-y", "--no-install-recommends", *SNMP_PACKAGES],
        env=env, capture_output=True, text=True, timeout=600,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"apt-get install a echoue (code {proc.returncode}) : "
            f"{(proc.stderr or '').strip()[:400]}"
        )

    if not _which("snmpd"):
        raise RuntimeError(
            f"Binaire snmpd absent apres install. Sortie : {proc.stdout[-400:]}"
        )

    return {
        "installed": True,
        "already_present": [],
        "newly_installed": SNMP_PACKAGES,
        "output_tail": proc.stdout[-800:],
    }


def render_conf(cfg) -> str:
    """Rend le drop-in /etc/snmp/snmpd.conf.d/muros.conf.

    Format : syntaxe snmpd.conf standard. Le drop-in est lu APRES la conf
    principale, donc on peut overrider ce qui est defini par defaut.
    """
    networks = [n.strip() for n in cfg.allowed_networks.split(",") if n.strip()]
    if not networks:
        # Guard: without an allowed CIDR, we bind 127.0.0.1 only.
        networks = ["127.0.0.1/32"]

    lines = [
        "# Genere par MurOS - ne pas editer a la main.",
        "# Drop-in charge par includeDir ajoute par MurOS dans /etc/snmp/snmpd.conf",
        "",
        f"agentAddress udp:{cfg.port}",
        "",
        f"sysLocation {cfg.syslocation}",
        f"sysContact {cfg.syscontact}",
        "sysServices 76",
        "",
        "# Read-only SNMPv2c community, restricted by CIDR",
    ]
    for net in networks:
        lines.append(f"rocommunity {cfg.community} {net}")
    lines.extend([
        "",
        "# Views: expose the full mib-2 (system, interfaces, host-resources, etc.)",
        "view systemview included .1.3.6.1.2.1",
        "view systemview included .1.3.6.1.4.1.2021",
        "",
        "# Process critiques surveilles",
        "proc muros-backend",
        "proc nginx",
        "",
    ])
    return "\n".join(lines) + "\n"


def write_conf(cfg) -> dict:
    """Materialise /etc/snmp/snmpd.conf.d/muros.conf only.

    No systemd action: the live snmpd keeps the previous config until
    the operator clicks Apply. When `cfg.enabled` is False we drop the
    drop-in file so a future Apply ends up stopping the daemon.
    """
    if not cfg.enabled:
        if APPLY_ENABLED and SNMP_CONF.exists():
            SNMP_CONF.unlink()
        return {"message": "SNMP configuration saved (disabled).", "service": SNMP_SERVICE}

    text = render_conf(cfg)

    if not APPLY_ENABLED:
        return {
            "message": f"dry-run : aurait ecrit {SNMP_CONF} ({len(text)} octets).",
            "service": SNMP_SERVICE,
            "conf_preview": text,
        }

    if not _which("snmpd"):
        raise RuntimeError(
            "snmpd not found. Click 'Install now' first."
        )

    SNMP_CONF.parent.mkdir(parents=True, exist_ok=True)
    SNMP_CONF.write_text(text, encoding="utf-8")
    os.chmod(SNMP_CONF, 0o644)
    # snmpd does not read .d by default, we add includeDir to the main file
    _ensure_includedir_in_main_conf()
    return {
        "message": f"snmpd configuration saved on port {cfg.port}.",
        "service": SNMP_SERVICE,
    }


def reload(cfg) -> dict:
    """Restart snmpd (or stop it) to pick up the on-disk config.

    Called only on explicit Apply, after write_conf() has been run.
    """
    if not APPLY_ENABLED:
        return {"message": "dry-run : reload skipped.", "service": SNMP_SERVICE}
    if not cfg.enabled:
        subprocess.run(
            ["systemctl", "disable", "--now", SNMP_SERVICE],
            capture_output=True, timeout=15,
        )
        return {"message": "SNMP service stopped.", "service": SNMP_SERVICE}

    subprocess.run(
        ["systemctl", "enable", "--now", SNMP_SERVICE],
        capture_output=True, timeout=15,
    )
    r = subprocess.run(
        ["systemctl", "restart", SNMP_SERVICE],
        capture_output=True, text=True, timeout=15,
    )
    if r.returncode != 0:
        raise RuntimeError(
            f"Restart {SNMP_SERVICE} a echoue : {(r.stderr or '').strip()[:400]}"
        )
    return {
        "message": f"snmpd reconfigure et redemarre sur port {cfg.port}.",
        "service": SNMP_SERVICE,
    }


def apply_config(cfg) -> dict:
    """Backwards-compatible: write_conf then reload in one shot."""
    write_conf(cfg)
    return reload(cfg)
