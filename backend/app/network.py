# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Apply network interface configuration to the Linux kernel.

ARCHITECTURE (aligned with FortiOS, OpenWrt and VyOS):

1. Every native network manager is MASKED at postinst:
   NetworkManager, systemd-networkd, networking.service (ifupdown).
   MurOS is the only thing talking to the kernel.

2. SINGLE source of truth: the SQLite DB (tables `interfaces` and
   `routes`). MurOS NEVER writes to /etc/network/interfaces,
   /etc/systemd/network, /etc/network/interfaces.d/, or anywhere else
   in the distribution network config. No intermediate file means no
   desync is possible.

3. Apply: `ip` commands (iproute2) directly against the kernel. VLANs
   use `ip link add ... type vlan`, IPs use `ip addr add/del`, routes
   use `ip route replace/del`. It is synchronous, atomic per command,
   and is what every other firewall does.

4. Persistence across reboots is muros-boot.service (oneshot, ordered
   before network-online.target) which runs backend/scripts/muros_boot.py.
   The script reads the DB and replays everything via the same `ip`
   commands.

Every command respects MUROS_APPLY: in dry-run mode we run nothing and
return (0, "dry-run").
"""
from __future__ import annotations

import os
import re
import subprocess
import ipaddress

APPLY_ENABLED = os.environ.get("MUROS_APPLY", "false").lower() in ("1", "true", "yes")

_VALID_IFNAME = re.compile(r"^[A-Za-z0-9._-]{1,15}$")


def _run(args: list[str]) -> tuple[int, str]:
    if not APPLY_ENABLED:
        return 0, "dry-run"
    # 5s: the 'ip link add/del/set', 'ip route add/del', 'ip addr
    # add/del' commands we run here return in sub-second on a healthy
    # kernel. Past that we kill to avoid blocking a FastAPI worker.
    try:
        res = subprocess.run(args, capture_output=True, text=True, timeout=5)
    except (subprocess.SubprocessError, FileNotFoundError) as exc:
        return 1, str(exc)
    out = (res.stdout + res.stderr).strip()
    return res.returncode, out


def validate_vlan_params(name: str, parent: str | None, vlan_id: int | None) -> None:
    if not _VALID_IFNAME.match(name):
        raise ValueError(f"invalid interface name : {name!r}")
    if not parent or not _VALID_IFNAME.match(parent):
        raise ValueError("parent_interface is required for a VLAN")
    if not vlan_id or not (1 <= vlan_id <= 4094):
        raise ValueError("vlan_id doit etre entre 1 et 4094")


def create_vlan(name: str, parent: str, vlan_id: int) -> tuple[int, str]:
    """Create a VLAN interface in the kernel: ip link add link <parent> name <name> type vlan id <id>."""
    validate_vlan_params(name, parent, vlan_id)
    return _run(["ip", "link", "add", "link", parent, "name", name, "type", "vlan", "id", str(vlan_id)])


def delete_interface(name: str) -> tuple[int, str]:
    if not _VALID_IFNAME.match(name):
        raise ValueError(f"invalid name : {name!r}")
    return _run(["ip", "link", "delete", name])


def set_link_state(name: str, up: bool) -> tuple[int, str]:
    if not _VALID_IFNAME.match(name):
        raise ValueError(f"invalid name : {name!r}")
    return _run(["ip", "link", "set", name, "up" if up else "down"])


def set_mtu(name: str, mtu: int) -> tuple[int, str]:
    if not _VALID_IFNAME.match(name):
        raise ValueError(f"invalid name : {name!r}")
    if not (68 <= mtu <= 9216):
        raise ValueError("mtu outside reasonable bounds (68-9216)")
    return _run(["ip", "link", "set", name, "mtu", str(mtu)])


def flush_addresses(name: str) -> tuple[int, str]:
    if not _VALID_IFNAME.match(name):
        raise ValueError(f"invalid name : {name!r}")
    return _run(["ip", "addr", "flush", "dev", name])


# --- Multi-WAN failover routing ------------------------------------------
#
# Approach: we do NOT touch the admin's secondary routing tables. For
# every known WAN gateway we declare a dedicated table (id = 100 +
# gateway.id) holding its own default route. The global default in
# table `main` is then rewritten to point to the elected WAN (`ip
# route replace default ...`). Stateful sessions in flight break on
# failover (their src IP changes through masquerade) which is the
# expected behavior and matches pfSense / OPNsense.

_WAN_RT_TABLE_OFFSET = 100  # rt_tables id = offset + gateway.id


def wan_rt_table_id(gateway_id: int) -> int:
    return _WAN_RT_TABLE_OFFSET + gateway_id


def wan_set_table_default(gateway_id: int, iface: str, gw_ip: str) -> tuple[int, str]:
    """Install the default route in the table dedicated to this WAN.

    The table stays in place even when the WAN is inactive: it is used
    for PBR (policy "this LAN exits via THIS WAN") that the admin can
    wire manually with `ip rule`. MurOS does not install any rule by
    default, this stays opt-in.
    """
    if not _VALID_IFNAME.match(iface):
        raise ValueError(f"invalid iface : {iface!r}")
    table = str(wan_rt_table_id(gateway_id))
    # `ip route replace` is idempotent: it creates or replaces without
    # error when the entry already exists.
    return _run([
        "ip", "route", "replace", "default", "via", gw_ip,
        "dev", iface, "table", table,
    ])


def wan_clear_table(gateway_id: int) -> tuple[int, str]:
    """Flush the table dedicated to a WAN (used when the gateway is deleted)."""
    table = str(wan_rt_table_id(gateway_id))
    return _run(["ip", "route", "flush", "table", table])


def wan_set_main_default(iface: str, gw_ip: str) -> tuple[int, str]:
    """Rewrite the default route of table `main` (the global default).

    This is the failover step: every new outbound flow follows this
    gateway. Flows that are already conntrack-ed continue on their old
    interface until masquerade re-ties them (which happens almost
    instantly on TCP thanks to kernel retries).
    """
    if not _VALID_IFNAME.match(iface):
        raise ValueError(f"invalid iface : {iface!r}")
    rc1, out1 = _run([
        "ip", "route", "replace", "default", "via", gw_ip, "dev", iface,
    ])
    # On flush le route cache pour ne pas garder de vieux nexthop en RAM.
    _run(["ip", "route", "flush", "cache"])
    return rc1, out1


def wan_remove_main_default() -> tuple[int, str]:
    """Remove the global default. Called when ALL WANs are down: we
    prefer to have no default rather than a broken default that
    blackholes traffic towards an unreachable gateway.
    """
    return _run(["ip", "route", "del", "default"])


def wan_probe(iface: str, target: str, timeout_s: float = 1.0) -> bool:
    """ICMP probe via a specific interface. Returns True on reply.

    We force `ping -I <iface>` to avoid the classic trap: without
    `-I`, the kernel picks the default route, which could very well
    be the other WAN. The point is to test THIS WAN, not the default.
    """
    if not _VALID_IFNAME.match(iface):
        raise ValueError(f"invalid iface : {iface!r}")
    try:
        ipaddress.ip_address(target)
    except ValueError:
        raise ValueError(f"target must be an IP (not a hostname): {target!r}")
    rc, _ = _run([
        "ping", "-c", "1", "-W", str(int(max(1, timeout_s))),
        "-I", iface, "-q", target,
    ])
    return rc == 0


def dhcp_release(name: str) -> tuple[int, str]:
    """Release any in-flight DHCP lease and kill the dhclient daemon.

    Useful before flushing addresses on an interface that was in dhcp
    mode: without release, dhclient re-adds the IP right after the
    flush. Best-effort: if dhclient is missing, ip will not raise a
    blocking error (rc != 0 but we do not care, the effect is achieved).
    """
    if not _VALID_IFNAME.match(name):
        raise ValueError(f"invalid name : {name!r}")
    return _run(["dhclient", "-r", name])


def add_address(name: str, cidr: str) -> tuple[int, str]:
    if not _VALID_IFNAME.match(name):
        raise ValueError(f"invalid name : {name!r}")
    # Reject an IP without prefix: ipaddress.ip_interface("192.168.1.70")
    # silently answers /32, which isolates the host from its LAN (no
    # direct route to the neighbors). Require an explicit prefix, and
    # reject /32 on IPv4 (unless the admin typed it on purpose, but
    # that is almost never what they want on a management interface).
    # Same for /128 on IPv6.
    if "/" not in cidr:
        raise ValueError(
            f"address without prefix: {cidr!r}. Provide the CIDR mask, "
            "for example 192.168.1.70/24"
        )
    try:
        iface = ipaddress.ip_interface(cidr)
    except ValueError as exc:
        raise ValueError(f"invalid CIDR address : {cidr}") from exc
    if iface.version == 4 and iface.network.prefixlen == 32:
        raise ValueError(
            f"prefix /32 rejected on {cidr}: would isolate the interface from "
            "its LAN. Use the real network mask (e.g. /24)."
        )
    if iface.version == 6 and iface.network.prefixlen == 128:
        raise ValueError(
            f"prefix /128 rejected on {cidr}: would isolate the interface from "
            "its IPv6 LAN."
        )
    return _run(["ip", "addr", "add", cidr, "dev", name])


def snapshot_interface(name: str) -> dict:
    """Capture the kernel state of an interface: addresses, MTU, link
    up/down, default route through this interface if any.

    Used by rollback: we read the state before mutating, so we know
    how to roll back when the admin does not confirm.

    Dry-run mode: returns an empty snapshot flagged as such.
    """
    if not _VALID_IFNAME.match(name):
        raise ValueError(f"invalid name : {name!r}")
    snap: dict = {"name": name, "dry_run": not APPLY_ENABLED, "addresses": [], "mtu": None, "up": None, "default_via": None}
    if not APPLY_ENABLED:
        return snap
    try:
        import json
        # `ip -j addr show dev <name>` returns clean JSON
        res = subprocess.run(["ip", "-j", "addr", "show", "dev", name], capture_output=True, text=True, timeout=5)
        if res.returncode == 0 and res.stdout.strip():
            data = json.loads(res.stdout)
            if data:
                first = data[0]
                snap["mtu"] = first.get("mtu")
                snap["up"] = "UP" in (first.get("flags") or [])
                for a in first.get("addr_info", []):
                    if a.get("family") == "inet":
                        prefix = a.get("prefixlen", 32)
                        snap["addresses"].append(f"{a['local']}/{prefix}")
        # Default route through this interface
        res2 = subprocess.run(["ip", "-j", "route", "show", "default", "dev", name], capture_output=True, text=True, timeout=5)
        if res2.returncode == 0 and res2.stdout.strip():
            try:
                routes = json.loads(res2.stdout)
                if routes:
                    snap["default_via"] = routes[0].get("gateway")
            except json.JSONDecodeError:
                pass
    except (subprocess.SubprocessError, json.JSONDecodeError, FileNotFoundError):
        pass
    return snap


def restore_interface(snap: dict) -> list[str]:
    """Apply to the kernel the state captured by snapshot_interface.
    Returns the list of non-blocking errors."""
    name = snap.get("name")
    if not name or not _VALID_IFNAME.match(name):
        raise ValueError(f"invalid snapshot: name {name!r}")
    if snap.get("dry_run") or not APPLY_ENABLED:
        return ["dry-run, restore not executed"]
    errors: list[str] = []
    # Flush, then re-apply the snapshot addresses
    flush_addresses(name)
    for cidr in snap.get("addresses") or []:
        rc, msg = add_address(name, cidr)
        if rc != 0:
            errors.append(f"add {cidr}: {msg}")
    if snap.get("mtu"):
        rc, msg = set_mtu(name, snap["mtu"])
        if rc != 0:
            errors.append(f"mtu: {msg}")
    if snap.get("up") is not None:
        rc, msg = set_link_state(name, bool(snap["up"]))
        if rc != 0:
            errors.append(f"link state: {msg}")
    if snap.get("default_via"):
        rc, msg = _run(["ip", "route", "replace", "default", "via", snap["default_via"], "dev", name])
        if rc != 0:
            errors.append(f"default route: {msg}")
    return errors


def detect_competing_managers() -> list[str]:
    """Detect competing network managers running on the box.

    MurOS must be the only thing driving the network: if NetworkManager,
    systemd-networkd or ifupdown DHCP-client runs in parallel, the
    values pushed by MurOS will be overwritten. On the Debian 13
    appliance target, those services are disabled by muros-boot. On a
    developer machine (Ubuntu, Fedora, ...) they are active and the
    admin needs to know.

    Returns the list of conflicting active units (empty in a clean
    environment).
    """
    candidates = (
        "NetworkManager.service",
        "systemd-networkd.service",
        "netplan.service",
        "wicked.service",
    )
    active: list[str] = []
    for unit in candidates:
        try:
            out = subprocess.check_output(
                ["systemctl", "is-active", unit], text=True, timeout=3,
                stderr=subprocess.DEVNULL,
            ).strip()
            if out == "active":
                active.append(unit)
        except (subprocess.SubprocessError, FileNotFoundError):
            continue
    return active



def link_exists(name: str) -> bool:
    """Check whether the interface already exists at the kernel level.

    Used by muros_boot.py to decide whether a VLAN must be created via
    `ip link add ... type vlan` or is already there.
    """
    if not APPLY_ENABLED:
        return False
    try:
        res = subprocess.run(
            ["ip", "link", "show", "dev", name],
            capture_output=True, text=True, timeout=3,
        )
        return res.returncode == 0
    except (subprocess.SubprocessError, FileNotFoundError):
        return False


def apply_interface_config(
    name: str,
    *,
    ip_mode: str,
    ip_address: str | None,
    gateway: str | None,
    mtu: int | None,
    enabled: bool,
) -> list[str]:
    """Apply a complete interface configuration to the kernel.

    Sequence:
    1. set link up/down based on enabled
    2. set mtu if provided
    3. mode 'none'   : no IP manipulation
       mode 'static' : flush + ip addr add
       mode 'dhcp'   : dhclient <iface> best-effort
    4. gateway : default route through this iface if provided

    Returns the list of non-blocking error messages for UI reporting.
    Real errors (validation) raise ValueError.
    """
    if not _VALID_IFNAME.match(name):
        raise ValueError(f"invalid name : {name!r}")
    errors: list[str] = []

    # Up / down
    rc, msg = set_link_state(name, enabled)
    if rc != 0 and msg and msg != "dry-run":
        errors.append(f"link {('up' if enabled else 'down')} : {msg}")

    # MTU
    if mtu:
        rc, msg = set_mtu(name, mtu)
        if rc != 0 and msg and msg != "dry-run":
            errors.append(f"mtu : {msg}")

    # IP: always flush first. It is the only way to actually drop an
    # address installed by a previous config when the admin removes it
    # from the UI. Then re-add according to the mode.
    flush_addresses(name)
    if ip_mode == "static":
        if not ip_address:
            raise ValueError("ip_address is required in static mode")
        rc, msg = add_address(name, ip_address)
        if rc != 0 and msg and msg != "dry-run":
            errors.append(f"ip addr add : {msg}")
    elif ip_mode == "dhcp":
        rc, msg = _run(["dhclient", "-1", name])
        if rc != 0 and msg and msg != "dry-run":
            errors.append(f"dhclient : {msg}")
    # mode 'none': the interface stays up without an IP

    # Gateway: default route through this interface
    if gateway and ip_mode == "static":
        try:
            ipaddress.ip_address(gateway)
        except ValueError as exc:
            raise ValueError(f"invalid gateway : {gateway}") from exc
        # `replace` works whether the route existed or not, idempotent
        rc, msg = _run(["ip", "route", "replace", "default", "via", gateway, "dev", name])
        if rc != 0 and msg and msg != "dry-run":
            errors.append(f"route default : {msg}")

    return errors
