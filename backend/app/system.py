"""Read the system state (network interfaces, etc).

Calls go through `ip -j` (JSON output) rather than parsing text.
Calls are read-only, no system modification from this module.
"""
import json
import subprocess
from typing import TypedDict


class SystemInterface(TypedDict):
    name: str
    state: str            # UP / DOWN / UNKNOWN
    mtu: int
    mac: str | None
    addresses: list[str]  # CIDR list
    is_virtual: bool      # docker, lo, veth, br-*, etc.
    gateway: str | None   # default gateway via this iface (if any)


_VIRTUAL_PREFIXES = ("lo", "docker", "veth", "br-", "virbr", "tun", "tap", "wg", "vnet")


def _is_virtual(name: str, link_type: str | None) -> bool:
    if name.startswith(_VIRTUAL_PREFIXES):
        return True
    if link_type and link_type != "ether":
        return True
    return False


def get_default_gateway(iface_name: str) -> str | None:
    """Retourne l'IPv4 de la passerelle par defaut sortant via `iface_name`,
    ou None si l'interface n'a pas de default route.

    Utilise `ip -j route` (sortie JSON), filtre sur `dst=default` ET
    `dev=iface_name`. C'est important au premier boot d'une appliance
    firewall installee via DHCP : on doit capturer la gateway que le
    DHCP a posee, sinon muros-boot la perd au redemarrage.
    """
    try:
        out = subprocess.check_output(
            ["ip", "-j", "route", "show", "default"],
            text=True, timeout=3,
        )
        routes = json.loads(out)
    except (subprocess.SubprocessError, FileNotFoundError, json.JSONDecodeError):
        return None
    for r in routes:
        if r.get("dev") == iface_name and r.get("gateway"):
            return str(r["gateway"])
    return None


def list_system_interfaces() -> list[SystemInterface]:
    """Retourne la liste des interfaces du systeme avec leurs IPs."""
    try:
        addr_out = subprocess.check_output(
            ["ip", "-j", "addr", "show"], text=True, timeout=5,
        )
        addr_data = json.loads(addr_out)
    except (subprocess.SubprocessError, FileNotFoundError, json.JSONDecodeError):
        return []

    result: list[SystemInterface] = []
    for item in addr_data:
        name = item.get("ifname")
        if not name:
            continue
        addresses: list[str] = []
        for ai in item.get("addr_info", []):
            local = ai.get("local")
            prefix = ai.get("prefixlen")
            if local and prefix is not None:
                addresses.append(f"{local}/{prefix}")
        link_type = item.get("link_type")
        result.append(SystemInterface(
            name=name,
            state=item.get("operstate", "UNKNOWN"),
            mtu=int(item.get("mtu", 0)),
            mac=item.get("address"),
            addresses=addresses,
            is_virtual=_is_virtual(name, link_type),
            gateway=get_default_gateway(name),
        ))
    return result
