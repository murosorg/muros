# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Haute disponibilite (HA) actif/passif.

Deux firewalls partagent une (ou plusieurs) IP virtuelle (VIP) via VRRP.
L'etat actif est elu sur la priorite VRRP, l'autre noeud reste backup.
Les sessions actives sont synchronisees par conntrackd pour eviter de
couper les connexions lors d'une bascule.

MurOS s'appuie sur deux paquets Debian : keepalived (VRRP) et
conntrackd (sync conntrack). On genere :
- /etc/keepalived/keepalived.conf
- /etc/conntrackd/conntrackd.conf

Puis on (re)demarre les services. La conf survit naturellement au reboot
(les services systemd sont enabled une fois pour toutes, et la conf est
repoussee depuis la DB par muros_boot.py par securite).
"""
from __future__ import annotations

import ipaddress
import os
import subprocess
from pathlib import Path

from app.apply import APPLY_ENABLED

KEEPALIVED_CONF = Path(os.environ.get(
    "MUROS_KEEPALIVED_CONF", "/etc/keepalived/keepalived.conf",
))
CONNTRACKD_CONF = Path(os.environ.get(
    "MUROS_CONNTRACKD_CONF", "/etc/conntrackd/conntrackd.conf",
))
NOTIFY_SCRIPT = Path(os.environ.get(
    "MUROS_HA_NOTIFY", "/usr/lib/muros/ha-notify.sh",
))

# Priorites : primary = 150, secondary = 100. Ecart de 50 garantit que
# le primary reprend la main quand il revient (sauf si nopreempt actif).
PRIO_PRIMARY = 150
PRIO_SECONDARY = 100


def _validate_cidr(cidr: str) -> str:
    """Valide et normalise une VIP au format IP/prefix."""
    s = cidr.strip()
    if "/" not in s:
        # Tolere une IP nue, on suppose /32.
        ipaddress.ip_address(s)
        return s + "/32"
    iface = ipaddress.ip_interface(s)
    return str(iface)


def _validate_ip(addr: str) -> str:
    ipaddress.ip_address(addr.strip())
    return addr.strip()


def _validate_vrid(vrid: int) -> int:
    if not 1 <= vrid <= 255:
        raise ValueError(f"VRID hors plage (1-255) : {vrid}")
    return vrid


# --- Generation keepalived.conf ---

def render_keepalived(config: dict, vips: list[dict], hostname: str) -> str:
    """Genere le contenu de /etc/keepalived/keepalived.conf.

    `config` : {role: 'primary'|'secondary', preempt: bool, ...}
    `vips`   : liste de {vrid, interface, vip_cidr, auth_pass, description, enabled}
    """
    role = config.get("role", "primary")
    base_prio = PRIO_PRIMARY if role == "primary" else PRIO_SECONDARY
    preempt = config.get("preempt", True)

    lines: list[str] = []
    lines.append("# Genere par MurOS, ne pas editer a la main")
    lines.append("")
    lines.append("global_defs {")
    lines.append(f"    router_id MUROS-{hostname}")
    lines.append("    enable_script_security")
    lines.append("    script_user root")
    lines.append("}")
    lines.append("")

    for vip in vips:
        if not vip.get("enabled", True):
            continue
        vrid = vip["vrid"]
        iface = vip["interface"]
        cidr = vip["vip_cidr"]
        # Le passwd est tronque a 8 caracteres par keepalived (auth_type PASS).
        auth = (vip.get("auth_pass") or "muros")[:8]
        prio = vip.get("priority") or base_prio
        desc = vip.get("description") or f"VI_{vrid}"

        lines.append(f"vrrp_instance VI_{vrid} {{")
        lines.append(f"    # {desc}")
        # On force BACKUP partout : l'election se fait sur la priorite, c'est
        # plus simple a raisonner que MASTER/BACKUP figes.
        lines.append("    state BACKUP")
        lines.append(f"    interface {iface}")
        lines.append(f"    virtual_router_id {vrid}")
        lines.append(f"    priority {prio}")
        lines.append("    advert_int 1")
        if not preempt:
            lines.append("    nopreempt")
        lines.append("    authentication {")
        lines.append("        auth_type PASS")
        lines.append(f"        auth_pass {auth}")
        lines.append("    }")
        lines.append("    virtual_ipaddress {")
        lines.append(f"        {cidr}")
        lines.append("    }")
        if NOTIFY_SCRIPT.parent.is_dir() or APPLY_ENABLED:
            lines.append(f'    notify "{NOTIFY_SCRIPT}"')
        lines.append("}")
        lines.append("")

    return "\n".join(lines) + "\n"


# --- Generation conntrackd.conf ---

def render_conntrackd(config: dict, local_addr: str) -> str:
    """Genere le contenu de /etc/conntrackd/conntrackd.conf.

    `config` : doit contenir peer_address et sync_interface.
    `local_addr` : IP locale sur sync_interface (ecouteur conntrackd).
    """
    peer = _validate_ip(config["peer_address"])
    sync_iface = config["sync_interface"].strip()
    local = _validate_ip(local_addr)

    body = f"""# Genere par MurOS, ne pas editer a la main
Sync {{
    Mode FTFW {{
        ResendQueueSize 131072
        ACKWindowSize 300
        DisableExternalCache Off
    }}
    UDP {{
        IPv4_address {local}
        IPv4_Destination_Address {peer}
        Port 3780
        Interface {sync_iface}
        SndSocketBuffer 1249280
        RcvSocketBuffer 1249280
        Checksum on
    }}
}}

General {{
    Nice -20
    HashSize 32768
    HashLimit 131072
    LogFile off
    Syslog on
    LockFile /var/lock/conntrack.lock
    UNIX {{
        Path /var/run/conntrackd.ctl
        Backlog 20
    }}
    NetlinkBufferSize 2097152
    NetlinkBufferSizeMaxGrowth 8388608
    Filter From Userspace {{
        Protocol Accept {{
            TCP
            UDP
            ICMP
        }}
        Address Ignore {{
            IPv4_address 127.0.0.1
        }}
    }}
}}
"""
    return body


# --- Apply ---

def apply_config(config: dict, vips: list[dict], *,
                 defer_start: bool = False) -> dict:
    """Ecrit les confs keepalived + conntrackd et (re)demarre les services.

    Retourne un dict avec dry_run, applied, message.

    defer_start: contexte boot, evite le deadlock avec
    network-online.target en separant enable / start --no-block.
    """
    import platform
    hostname = platform.node() or "muros"

    # Determine l'IP locale sur la sync interface pour conntrackd.
    local_addr = _detect_local_ip(config["sync_interface"])

    ka = render_keepalived(config, vips, hostname)
    cd = render_conntrackd(config, local_addr) if config.get("conntrack_sync", True) else ""

    if not APPLY_ENABLED:
        return {
            "applied": False,
            "dry_run": True,
            "message": (
                f"dry-run : conf keepalived ({len(ka)} chars) et conntrackd "
                f"({len(cd)} chars) preparees mais pas ecrites (MUROS_APPLY off)."
            ),
        }

    # Si HA disabled : on stoppe les services et on degage les fichiers de conf.
    if not config.get("enabled", False):
        _stop_services()
        for p in (KEEPALIVED_CONF, CONNTRACKD_CONF):
            if p.exists():
                p.unlink()
        return {
            "applied": True,
            "dry_run": False,
            "message": "HA desactivee, services arretes.",
        }

    KEEPALIVED_CONF.parent.mkdir(parents=True, exist_ok=True)
    KEEPALIVED_CONF.write_text(ka)

    if cd:
        CONNTRACKD_CONF.parent.mkdir(parents=True, exist_ok=True)
        CONNTRACKD_CONF.write_text(cd)

    # Tests de validite avant restart
    try:
        subprocess.check_output(
            ["keepalived", "-t", "-f", str(KEEPALIVED_CONF)],
            stderr=subprocess.STDOUT, timeout=5,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"keepalived a refuse la conf : {exc.output.decode('utf-8', 'ignore')}"
        ) from exc
    except FileNotFoundError:
        raise RuntimeError("keepalived non installe sur ce systeme")

    # Restart conntrackd d'abord (pour qu'il soit pret quand keepalived bascule).
    if cd:
        _restart("conntrackd", defer_start=defer_start)
    _reload_or_restart("keepalived", defer_start=defer_start)

    return {
        "applied": True,
        "dry_run": False,
        "message": "HA appliquee, keepalived rechargee et conntrackd redemarree.",
    }


def _detect_local_ip(iface: str) -> str:
    """Recupere la premiere IPv4 attachee a l'interface."""
    # Garde-fou : iface vide ou whitespace -> on ne lance pas `ip` sur
    # un device "" (sinon stderr "Device \"\" does not exist." dans le
    # journal). On raise direct un message clair.
    iface = (iface or "").strip()
    if not iface:
        raise RuntimeError(
            "interface de sync non definie : configurer HA depuis l'UI "
            "(onglet Haute Dispo) avant d'activer la sync conntrackd."
        )
    try:
        out = subprocess.check_output(
            ["ip", "-o", "-4", "addr", "show", "dev", iface],
            text=True, timeout=3, stderr=subprocess.DEVNULL,
        )
        for line in out.splitlines():
            parts = line.split()
            for i, p in enumerate(parts):
                if p == "inet" and i + 1 < len(parts):
                    return parts[i + 1].split("/")[0]
    except (subprocess.SubprocessError, FileNotFoundError):
        pass
    raise RuntimeError(
        f"aucune IPv4 trouvee sur l'interface de sync {iface!r}, "
        "impossible de generer conntrackd.conf"
    )


def _reload_or_restart(service: str, *, defer_start: bool = False) -> None:
    if defer_start:
        # Au boot, on se contente d'enable : systemd demarrera l'unit
        # apres muros-boot une fois network-online.target atteinte. Un
        # reload/restart explicite ici n'apporte rien et risquerait un
        # double demarrage.
        _restart(service, defer_start=True)
        return
    try:
        subprocess.check_call(["systemctl", "reload", service], timeout=10)
    except (subprocess.SubprocessError, FileNotFoundError):
        _restart(service)


def _restart(service: str, *, defer_start: bool = False) -> None:
    try:
        # enable (sans --now) : persistance, ne touche pas au runtime,
        # ne depend d'aucune target -> safe en contexte boot.
        subprocess.check_call(["systemctl", "enable", service], timeout=5)
        if defer_start:
            # En contexte boot, --no-block enqueue le restart : la
            # commande rend la main tout de suite, systemd executera le
            # demarrage apres muros-boot. Sans --no-block, on attend
            # network-online.target qui attend muros-boot -> deadlock.
            subprocess.check_call(
                ["systemctl", "--no-block", "restart", service], timeout=5,
            )
        else:
            subprocess.check_call(["systemctl", "restart", service], timeout=15)
    except (subprocess.SubprocessError, FileNotFoundError):
        pass


def _stop_services() -> None:
    for svc in ("keepalived", "conntrackd"):
        try:
            subprocess.check_call(["systemctl", "disable", "--now", svc], timeout=15)
        except (subprocess.SubprocessError, FileNotFoundError):
            pass


# --- Installation des paquets Debian ---

def install_packages() -> dict:
    """Installe les paquets necessaires a la HA via apt.

    Idempotent : si keepalived et conntrackd sont deja la, ne fait rien.
    Refuse l'execution si MurOS ne tourne pas en root, ou si apt n'est pas
    disponible (cas d'un dev sur un autre OS).

    Retourne un dict avec :
      - installed (bool) : True si l'operation a abouti
      - already_present (list[str]) : paquets deja installes avant l'appel
      - newly_installed (list[str]) : paquets fraichement installes
      - output_tail (str) : derniers caracteres de la sortie apt (pour debug)
    """
    pkgs = ["keepalived", "conntrackd"]
    already = [p for p in pkgs if _which(p)]
    missing = [p for p in pkgs if p not in already]

    if not missing:
        return {
            "installed": True,
            "already_present": already,
            "newly_installed": [],
            "output_tail": "",
        }

    if not APPLY_ENABLED:
        return {
            "installed": False,
            "already_present": already,
            "newly_installed": [],
            "output_tail": (
                f"dry-run : aurait execute 'apt-get install -y {' '.join(missing)}' "
                "(MUROS_APPLY off)."
            ),
        }

    if os.geteuid() != 0:
        raise RuntimeError(
            "Installation des paquets impossible : MurOS doit tourner en root "
            "pour utiliser apt-get. Lancez le service en tant que root ou "
            "installez les paquets manuellement : "
            f"apt install -y {' '.join(missing)}"
        )

    try:
        subprocess.check_call(
            ["which", "apt-get"], stdout=subprocess.DEVNULL, timeout=2,
        )
    except (subprocess.SubprocessError, FileNotFoundError) as exc:
        raise RuntimeError(
            "apt-get not found on this system. Automatic installation "
            "n'est supportee que sur Debian/Ubuntu."
        ) from exc

    env = {**os.environ, "DEBIAN_FRONTEND": "noninteractive", "LC_ALL": "C"}

    # apt-get update d'abord, sinon installation peut echouer sur un cache vieux.
    proc_update = subprocess.run(
        ["apt-get", "update", "-q"],
        env=env, capture_output=True, text=True, timeout=120,
    )
    if proc_update.returncode != 0:
        raise RuntimeError(
            f"apt-get update a echoue (code {proc_update.returncode}) : "
            f"{(proc_update.stderr or '').strip()[:400]}"
        )

    proc = subprocess.run(
        ["apt-get", "install", "-y", "--no-install-recommends", *missing],
        env=env, capture_output=True, text=True, timeout=600,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"apt-get install a echoue (code {proc.returncode}) : "
            f"{(proc.stderr or '').strip()[:400]}"
        )

    # Verifie que c'est bien la maintenant.
    still_missing = [p for p in missing if not _which(p)]
    if still_missing:
        raise RuntimeError(
            f"Paquets toujours absents apres install : {', '.join(still_missing)}. "
            f"Sortie apt : {proc.stdout[-400:]}"
        )

    return {
        "installed": True,
        "already_present": already,
        "newly_installed": missing,
        "output_tail": proc.stdout[-800:],
    }


# --- Status (lecture seule) ---

def _keepalived_version() -> str | None:
    from app.service_state import pkg_version
    return pkg_version("keepalived")


def _conntrackd_version() -> str | None:
    from app.service_state import pkg_version
    return pkg_version("conntrackd")


def get_status() -> dict:
    """Etat live de la HA : services + role VRRP + stats conntrackd."""
    from app.service_state import service_state as _state
    return {
        "keepalived_active": _systemd_active("keepalived"),
        "conntrackd_active": _systemd_active("conntrackd"),
        "keepalived_state": _state("keepalived.service"),
        "conntrackd_state": _state("conntrackd.service"),
        "keepalived_installed": _which("keepalived"),
        "conntrackd_installed": _which("conntrackd"),
        "keepalived_version": _keepalived_version(),
        "conntrackd_version": _conntrackd_version(),
        "vrrp_instances": _read_vrrp_state(),
        "conntrack_stats": _read_conntrackd_stats(),
    }


from app.service_state import is_active as _systemd_active, which as _which  # noqa: E402


def _read_vrrp_state() -> list[dict]:
    """keepalived peut dumper son etat en envoyant SIGUSR1 (cree /tmp/keepalived.data).
    On evite d'envoyer un signal a chaque requete, on parse les journaux a la place.
    Fallback : on regarde quelles VIP sont attachees au noyau via `ip -4 addr`.
    """
    state: list[dict] = []
    try:
        out = subprocess.check_output(
            ["journalctl", "-u", "keepalived", "-n", "50", "--no-pager", "-o", "cat"],
            text=True, timeout=5,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return state

    # On parse les transitions "Entering MASTER STATE" / "Entering BACKUP STATE" / "Entering FAULT STATE"
    # par instance VI_<vrid>.
    last: dict[str, str] = {}
    for line in out.splitlines():
        if "Entering" not in line:
            continue
        # ex : "(VI_50) Entering MASTER STATE"
        m_state = None
        if "MASTER STATE" in line:
            m_state = "MASTER"
        elif "BACKUP STATE" in line:
            m_state = "BACKUP"
        elif "FAULT STATE" in line:
            m_state = "FAULT"
        if not m_state:
            continue
        if "(" in line and ")" in line:
            name = line.split("(", 1)[1].split(")", 1)[0]
            last[name] = m_state

    for name, st in last.items():
        state.append({"name": name, "state": st})
    return state


def _read_conntrackd_stats() -> dict:
    """Lit `conntrackd -s` pour exposer les compteurs de sync."""
    if not _which("conntrackd"):
        return {}
    try:
        out = subprocess.check_output(
            ["conntrackd", "-s"], text=True, timeout=3, stderr=subprocess.STDOUT,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return {}
    stats: dict = {}
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("current active connections:"):
            stats["active"] = _to_int(line.split(":", 1)[1])
        elif line.startswith("connections created:"):
            stats["created"] = _to_int(line.split(":", 1)[1].split()[0])
        elif line.startswith("connections updated:"):
            stats["updated"] = _to_int(line.split(":", 1)[1].split()[0])
        elif line.startswith("connections destroyed:"):
            stats["destroyed"] = _to_int(line.split(":", 1)[1].split()[0])
    return stats


def _to_int(s: str) -> int:
    try:
        return int(s.strip())
    except (ValueError, TypeError):
        return 0
