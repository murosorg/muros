"""Configuration NTP via systemd-timesyncd.

Sur Debian 13 (Trixie), systemd-timesyncd est le client NTP par defaut. On
ne touche plus a chrony (paquet optionnel), on s'appuie uniquement sur le
service systemd natif.

Lecture :
- `timedatectl show` : etat de la synchro (NTP=, NTPSynchronized=, Timezone=)
- `timedatectl timesync-status` : detail de la source active

Ecriture :
- MurOS pose un drop-in /etc/systemd/timesyncd.conf.d/muros.conf qui ne
  contient que la directive `NTP=`. Le reste de la conf systemd reste
  intact.
- Apres ecriture : `systemctl restart systemd-timesyncd`.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

MUROS_TIMESYNCD_CONF = Path(os.environ.get(
    "MUROS_TIMESYNCD_CONF", "/etc/systemd/timesyncd.conf.d/muros.conf",
))

DEFAULT_SERVERS = [
    "0.debian.pool.ntp.org",
    "1.debian.pool.ntp.org",
    "2.debian.pool.ntp.org",
    "3.debian.pool.ntp.org",
]


from app.service_state import which as _which  # noqa: E402


def get_backend() -> str:
    """Retourne 'timesyncd' si la commande timedatectl est presente.

    L'unit systemd peut etre inactive ; c'est au `get_status` de le signaler.
    Sur Ubuntu 24.04, chrony peut etre installe a la place mais MurOS cible
    timesyncd uniquement (Debian 13 par defaut). Si timedatectl absent, on
    rend 'none' et l'UI affiche un message explicite.
    """
    if _which("timedatectl"):
        return "timesyncd"
    return "none"


def get_status() -> dict:
    """Etat de la synchro systemd-timesyncd."""
    if get_backend() != "timesyncd":
        return {"available": False, "backend": "none"}
    try:
        td = subprocess.check_output(["timedatectl", "show"], text=True, timeout=5)
    except (subprocess.SubprocessError, FileNotFoundError):
        return {"available": False, "backend": "timesyncd"}
    info: dict = {}
    for line in td.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            info[k.strip()] = v.strip()

    # Le serveur courant vit dans `timesync-status`, pas dans `show`.
    server = ""
    try:
        ts = subprocess.check_output(
            ["timedatectl", "timesync-status"], text=True, timeout=5,
        )
        for line in ts.splitlines():
            line = line.strip()
            if line.startswith("Server:"):
                server = line.split(":", 1)[1].strip().split()[0]
                break
    except (subprocess.SubprocessError, FileNotFoundError):
        pass

    ntp_active = info.get("NTP") == "yes"
    synced = info.get("NTPSynchronized") == "yes"
    if synced:
        leap = "Normal"
    elif ntp_active:
        leap = "En cours"
    else:
        leap = "Service NTP inactif"
    return {
        "available": True,
        "backend": "timesyncd",
        "ref_name": server,
        "stratum": 0,
        "leap_status": leap,
        "ntp_synchronized": synced,
        "ntp_active": ntp_active,
        "timezone": info.get("Timezone", ""),
    }


def get_servers() -> list[str]:
    """Lit la liste actuelle de serveurs depuis le drop-in MurOS."""
    if not MUROS_TIMESYNCD_CONF.is_file():
        return list(DEFAULT_SERVERS)
    for line in MUROS_TIMESYNCD_CONF.read_text().splitlines():
        s = line.strip()
        if s.startswith("NTP="):
            return s[4:].split()
    return list(DEFAULT_SERVERS)


def get_config_path() -> str:
    return str(MUROS_TIMESYNCD_CONF)


def set_servers(servers: list[str]) -> None:
    """Ecrit le drop-in timesyncd et redemarre le service."""
    cleaned = [s.strip() for s in servers if s.strip()]
    if not cleaned:
        raise ValueError("au moins un serveur NTP est requis")
    if get_backend() != "timesyncd":
        raise RuntimeError(
            "systemd-timesyncd indisponible sur ce systeme (commande "
            "timedatectl manquante)."
        )
    MUROS_TIMESYNCD_CONF.parent.mkdir(parents=True, exist_ok=True)
    body = (
        "# Genere par MurOS, ne pas editer a la main\n"
        "[Time]\n"
        "NTP=" + " ".join(cleaned) + "\n"
    )
    MUROS_TIMESYNCD_CONF.write_text(body)
    _restart("systemd-timesyncd")


def _restart(service: str) -> None:
    try:
        subprocess.check_call(["systemctl", "restart", service], timeout=10)
    except (subprocess.SubprocessError, FileNotFoundError):
        # Silencieux : en dev (MUROS_APPLY off), pas de systemctl ou pas de droits.
        pass
