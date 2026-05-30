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
    """Reboot the firewall after delay_seconds.

    Le delay permet a la requete HTTP de retourner 200 avant le shutdown.
    """
    if not APPLY_ENABLED:
        return {"scheduled": False, "message": "dry-run: no action."}
    if os.geteuid() != 0:
        raise RuntimeError("Reboot impossible: MurOS must run as root.")
    # systemd-run lets us trigger in the background with a delay.
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


def list_services() -> list[dict]:
    """Liste les services MurOS-geres, filtre sur ceux installes.

    Retourne pour chacun : unit, display_name, page (lien UI), category,
    status (active/inactive/failed/unknown).
    """
    result = []
    for entry in _SERVICE_CATALOG:
        # Install check: we accept if the CLI binary exists OR if one of the
        # systemd units is known.
        installed = False
        if entry.get("binary") and _which(entry["binary"]):
            installed = True
        else:
            # Try the main unit then the alternatives
            units_to_check = [entry["unit"]] + entry.get("alt_units", [])
            for u in units_to_check:
                if _unit_exists(u):
                    installed = True
                    break
            if not installed and not entry.get("binary"):
                # MurOS-native units (muros-backend, muros-watcher):
                # always considered installed, their unit is shipped by the
                # .deb even when inactive. Detection: unit name starts with
                # "muros-".
                installed = entry["unit"].startswith("muros-")

        if not installed:
            continue

        # Pick the unit to query: try the main one then the alternatives.
        # If an alternative is "active" while the main one is not, take the
        # alternative. Typical case on Debian 12+: `strongswan.service` is a
        # missing alias that reports "inactive", while
        # `strongswan-starter.service` is the real unit.
        units_to_try = [entry["unit"]] + entry.get("alt_units", [])
        active_unit = entry["unit"]
        status = _systemctl_status(active_unit)
        for u in units_to_try[1:]:
            if status == "active":
                break
            alt_status = _systemctl_status(u)
            # Take the alt if it is active, OR if the main one is unknown
            # and the alt is known (whatever its state).
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
    """Shut down the firewall after delay_seconds."""
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

