# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Actions systeme : reboot, shutdown, mise a l'heure du firewall.

Utilise systemctl pour eviter le shutdown -h direct (plus propre,
laisse les services s'arreter proprement).
"""
from __future__ import annotations

import logging
import os
import subprocess

from app.apply import APPLY_ENABLED

log = logging.getLogger("muros.system_actions")


def reboot(delay_seconds: int = 5) -> dict:
    """Reboot du firewall apres delay_seconds.

    Le delay permet a la requete HTTP de retourner 200 avant le shutdown.
    """
    if not APPLY_ENABLED:
        return {"scheduled": False, "message": "dry-run : aucune action."}
    if os.geteuid() != 0:
        raise RuntimeError("Reboot impossible: MurOS must run as root.")
    # systemd-run nous permet de declencher en background avec un delai.
    subprocess.Popen([
        "systemd-run", "--on-active=" + str(delay_seconds), "--unit=muros-reboot",
        "systemctl", "reboot",
    ])
    log.warning("Reboot planifie dans %ds via systemd-run.", delay_seconds)
    return {"scheduled": True, "message": f"Reboot planifie dans {delay_seconds}s."}


from app.service_state import (  # noqa: E402
    service_state as _systemctl_status,
    which as _which,
)


def _unit_exists(unit: str) -> bool:
    """Verifie qu'une unit systemd est connue (installee ou native)."""
    try:
        r = subprocess.run(
            ["systemctl", "list-unit-files", unit, "--no-legend"],
            text=True, capture_output=True, timeout=3,
        )
        return r.returncode == 0 and bool(r.stdout.strip())
    except (subprocess.SubprocessError, FileNotFoundError):
        return False


# Definition des services MurOS-geres, avec leur logique d'installation et
# la page UI a pointer.
#
# Ordering rationale (Monitoring page):
#   The list reads top-to-bottom from "always running by default" to
#   "stays dormant until the admin configures the feature". This way
#   anything red in the top half is a real alarm, while red items
#   in the bottom half just mean a feature is not yet enabled.
#
#   1. Always-on stack: muros-backend (the UI API), nginx (serves
#      the UI), ssh (mgmt fallback), fail2ban (protects the mgmt
#      plane), snmpd (default observability endpoint).
#   2. MurOS optional daemons: MurOS Watcher (alert dispatch) and
#      MurOS Wan Monitor (multi-WAN failover engine). Both are
#      MurOS-native but only start once the admin configures the
#      corresponding feature, so they belong to the optional half.
#   3. LAN services published to clients: Kea (DHCP), unbound
#      (recursive DNS).
#   4. High availability: keepalived (VRRP) + conntrackd (sync).
#   5. VPN: strongswan (IPsec) + wg-quick@wg0 (WireGuard).
#
# The `category` field is kept for backward compatibility (still
# returned by the API) but the UI no longer groups by it.
#   The `default_on` field flags services enabled out of the box by the
#   package postinst (see packaging/debian/postinst). The Monitoring page
#   uses it to put the install-time services in the left column and the
#   on-demand ones (SSH off by default, HA, VPN, MurOS feature daemons) on
#   the right.
_SERVICE_CATALOG = [
    # 1. Always-on stack.
    {"unit": "muros-backend", "display": "MurOS Backend", "page": "/system", "category": "muros", "default_on": True},
    {"unit": "nginx", "display": "Nginx (UI)", "page": "/tls", "binary": "nginx", "category": "core", "default_on": True},
    {"unit": "ssh", "alt_units": ["sshd"], "display": "SSH", "page": "/ssh", "binary": "sshd", "category": "core", "default_on": False},
    {"unit": "fail2ban", "display": "Fail2ban", "page": "/logs", "binary": "fail2ban-server", "category": "core", "default_on": True},
    {"unit": "snmpd", "display": "SNMP", "page": "/snmp", "binary": "snmpd", "category": "core", "default_on": True},

    # 2. MurOS optional daemons (start when a feature is configured).
    {"unit": "muros-watcher", "display": "MurOS Watcher", "page": "/notifications", "category": "muros", "default_on": False},
    {"unit": "muros-wan-monitor", "display": "MurOS Wan Monitor", "page": "/wan", "category": "muros", "default_on": False},

    # 3. LAN services published by the firewall (enabled by default).
    {"unit": "chrony", "alt_units": ["chronyd"], "display": "NTP (chrony)", "page": "/services/ntp",
     "binary": "chronyd", "category": "core", "default_on": True},
    {"unit": "kea-dhcp4-server", "display": "DHCP server (Kea)", "page": "/services/dhcp",
     "binary": "kea-dhcp4", "category": "opt", "default_on": True},
    {"unit": "unbound", "display": "DNS recursive (Unbound)", "page": "/services/dns",
     "binary": "unbound", "category": "opt", "default_on": True},

    # 4. High availability.
    {"unit": "keepalived", "display": "Keepalived (VRRP)", "page": "/ha", "binary": "keepalived", "category": "opt", "default_on": False},
    {"unit": "conntrackd", "display": "Conntrackd (sync)", "page": "/ha", "binary": "conntrackd", "category": "opt", "default_on": False},

    # 5. VPN.
    {"unit": "strongswan", "alt_units": ["strongswan-starter"], "display": "StrongSwan (IPsec)",
     "page": "/vpn/ipsec", "binary": "swanctl", "category": "opt", "default_on": False},
    {"unit": "wg-quick@wg0", "display": "WireGuard", "page": "/vpn/wireguard",
     "binary": "wg", "category": "opt", "default_on": False},
]


def list_listen_addresses() -> list[dict]:
    """Liste les IPs locales utilisables comme adresse d'ecoute.

    Retourne pour chaque IP : label (affichable), address, interface, loopback.
    """
    addresses: list[dict] = [
        {"label": "All interfaces (0.0.0.0)", "address": "0.0.0.0",
         "interface": "*", "loopback": False},
    ]
    try:
        import json as _json
        r = subprocess.run(
            ["ip", "-j", "addr", "show"],
            capture_output=True, text=True, timeout=3,
        )
        if r.returncode == 0 and r.stdout.strip():
            data = _json.loads(r.stdout)
            for iface in data:
                name = iface.get("ifname", "")
                if name == "lo":
                    addresses.append({
                        "label": "127.0.0.1 (loopback)", "address": "127.0.0.1",
                        "interface": "lo", "loopback": True,
                    })
                    continue
                for addr_info in iface.get("addr_info", []):
                    if addr_info.get("family") != "inet":
                        continue
                    local = addr_info.get("local")
                    if not local:
                        continue
                    addresses.append({
                        "label": f"{local} ({name})",
                        "address": local,
                        "interface": name,
                        "loopback": False,
                    })
    except (subprocess.SubprocessError, FileNotFoundError, ValueError):
        pass
    return addresses


def list_services() -> list[dict]:
    """Liste les services MurOS-geres, filtre sur ceux installes.

    Retourne pour chacun : unit, display_name, page (lien UI), category,
    status (active/inactive/failed/unknown).
    """
    result = []
    for entry in _SERVICE_CATALOG:
        # Verif installation : on accepte si le binary CLI existe OU si une
        # des units systemd est connue.
        installed = False
        if entry.get("binary") and _which(entry["binary"]):
            installed = True
        else:
            # Essaye unit principal puis alternatifs
            units_to_check = [entry["unit"]] + entry.get("alt_units", [])
            for u in units_to_check:
                if _unit_exists(u):
                    installed = True
                    break
            if not installed and not entry.get("binary"):
                # Unites MurOS-natives (muros-backend, muros-watcher) :
                # toujours considerees installees, leur unit est livree
                # par le .deb meme si inactive. Detection : unit name
                # commence par "muros-".
                installed = entry["unit"].startswith("muros-")

        if not installed:
            continue

        # Choisir l'unit a interroger : on essaie le principal puis les
        # alternatifs. Si un alternatif est "active" alors que le principal
        # ne l'est pas, on prend l'alternatif. Cas typique sur Debian 12+ :
        # `strongswan.service` est un alias absent qui rapporte "inactive",
        # alors que `strongswan-starter.service` est l'unit reelle.
        units_to_try = [entry["unit"]] + entry.get("alt_units", [])
        active_unit = entry["unit"]
        status = _systemctl_status(active_unit)
        for u in units_to_try[1:]:
            if status == "active":
                break
            alt_status = _systemctl_status(u)
            # On prend l'alt si lui est active, OU si le principal est unknown
            # et l'alt connu (peu importe son etat).
            if alt_status == "active" or (status == "unknown" and alt_status != "unknown"):
                active_unit = u
                status = alt_status

        result.append({
            "unit": active_unit,
            "display_name": entry["display"],
            "page": entry["page"],
            "category": entry["category"],
            "status": status,
            "default_on": entry.get("default_on", False),
        })
    return result


def shutdown(delay_seconds: int = 5) -> dict:
    """Shutdown (arret) du firewall apres delay_seconds."""
    if not APPLY_ENABLED:
        return {"scheduled": False, "message": "dry-run : aucune action."}
    if os.geteuid() != 0:
        raise RuntimeError("Shutdown impossible: MurOS must run as root.")
    subprocess.Popen([
        "systemd-run", "--on-active=" + str(delay_seconds), "--unit=muros-shutdown",
        "systemctl", "poweroff",
    ])
    log.warning("Shutdown planifie dans %ds via systemd-run.", delay_seconds)
    return {"scheduled": True, "message": f"Shutdown planifie dans {delay_seconds}s."}
