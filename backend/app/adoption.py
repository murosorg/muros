# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Kernel-state adoption at install time.

When the muros package is installed on a machine already in production
(e.g. a Debian 13 box that has been running for 6 months), the kernel
already holds a valid network configuration: IPs on interfaces (DHCP
or static), default routes, MTU, etc.

The postinst removes NetworkManager / systemd-networkd. Without
capturing the existing state, muros-boot would replay an empty DB at
the next reboot and the admin would lose the network (sometimes before
they even had a chance to log into the UI).

Clean solution: on the first run of muros-boot.service, we snapshot
the current kernel state into the DB with dirty=False (nothing to
re-apply, the kernel already has it). From then on, the MurOS DB is
officially the source of truth.

Adoption marker: /var/lib/muros/.adopted. If present, the snapshot is
skipped. Idempotent: can be forced with --force to re-adopt (useful in
recovery).
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path

from sqlalchemy.orm import Session

from app import models

log = logging.getLogger("muros.adoption")

ADOPTED_MARKER = Path(os.environ.get(
    "MUROS_ADOPTED_MARKER", "/var/lib/muros/.adopted",
))

# Interfaces we always ignore (neither adopted nor managed).
_IGNORED_PREFIXES = (
    "lo",
    "docker", "br-", "veth", "virbr", "tun", "tap",
    # Tunnels managed by dedicated MurOS pages (WireGuard, IPsec,
    # generic IP tunnels). They show up as kernel netdevs but are not
    # "physical links" and must not appear in /network.
    "wg", "ipsec", "xfrm", "gre", "gretap", "sit", "ip6tnl", "ppp",
)


def _ip_link_show() -> list[dict]:
    """Return parsed `ip -j -d link show`, or an empty list on error."""
    try:
        out = subprocess.check_output(
            ["ip", "-j", "-d", "link", "show"], text=True, timeout=5,
        )
        return json.loads(out)
    except (subprocess.SubprocessError, FileNotFoundError, json.JSONDecodeError) as exc:
        log.warning("ip link show failed: %s", exc)
        return []


def _ip_addr_show() -> dict[str, list[str]]:
    """Return {iface: [cidr1, cidr2...]} for global IPv4 addresses."""
    out: dict[str, list[str]] = {}
    try:
        raw = subprocess.check_output(
            ["ip", "-j", "-4", "addr", "show"], text=True, timeout=5,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return out
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return out
    for entry in data:
        name = entry.get("ifname")
        if not name:
            continue
        addrs: list[str] = []
        for info in entry.get("addr_info", []):
            ip = info.get("local")
            plen = info.get("prefixlen")
            scope = info.get("scope")
            if not ip or plen is None or scope != "global":
                continue
            addrs.append(f"{ip}/{plen}")
        if addrs:
            out[name] = addrs
    return out


def _ip_route_show() -> list[dict]:
    """Return parsed `ip -j -4 route show`."""
    try:
        raw = subprocess.check_output(
            ["ip", "-j", "-4", "route", "show"], text=True, timeout=5,
        )
        return json.loads(raw)
    except (subprocess.SubprocessError, FileNotFoundError, json.JSONDecodeError) as exc:
        log.warning("ip route show failed: %s", exc)
        return []


def _is_relevant_iface(name: str, link_info: dict) -> bool:
    """Filter: ignore loopback, docker, virtual bridges, etc."""
    if not name or name == "lo":
        return False
    if any(name.startswith(p) for p in _IGNORED_PREFIXES):
        return False
    # Keep VLANs (kind=vlan), bonds, physical ethernet, wireless
    # (though wifi should generally not be on a firewall).
    kind = link_info.get("linkinfo", {}).get("info_kind")
    if kind in ("docker", "bridge", "veth", "tun"):
        return False
    return True


def _adopt_interfaces(db: Session) -> int:
    """Create or update Interface rows from the kernel.

    Returns the number of interfaces touched.
    """
    links = _ip_link_show()
    addrs = _ip_addr_show()
    touched = 0
    for link in links:
        name = link.get("ifname")
        if not _is_relevant_iface(name, link):
            continue
        mtu = link.get("mtu") or 1500
        state = link.get("operstate", "UNKNOWN")
        enabled = state.upper() in ("UP", "UNKNOWN")
        live_addrs = addrs.get(name, [])
        # Take the first global IPv4 as the primary.
        primary_cidr = live_addrs[0] if live_addrs else None

        # VLAN ?
        info = link.get("linkinfo", {})
        is_vlan = info.get("info_kind") == "vlan"
        vlan_id = info.get("info_data", {}).get("id") if is_vlan else None
        parent = link.get("link") if is_vlan else None

        existing = db.query(models.Interface).filter_by(name=name).one_or_none()
        if existing is None:
            iface = models.Interface(
                name=name,
                type="vlan" if is_vlan else "physical",
                parent_interface=parent,
                vlan_id=vlan_id,
                ip_mode="static" if primary_cidr else "none",
                ip_address=primary_cidr,
                gateway=None,  # filled by _adopt_routes
                mtu=mtu,
                enabled=enabled,
                dirty=False,
            )
            db.add(iface)
            log.info("Adopted interface %s (mode=%s, ip=%s)", name, iface.ip_mode, primary_cidr or "-")
        elif primary_cidr and existing.ip_mode == "none":
            # Upgrade case: the iface exists in DB with ip_mode=none but
            # the kernel has an IP. Freeze the IP rather than losing it.
            existing.ip_mode = "static"
            existing.ip_address = primary_cidr
            if not existing.mtu:
                existing.mtu = mtu
            existing.enabled = enabled
            existing.dirty = False
            log.info("Upgraded interface %s: ip_mode none -> static (%s)", name, primary_cidr)
        else:
            continue
        touched += 1
    db.commit()
    return touched


def _adopt_routes(db: Session) -> int:
    """Create StaticRoute rows for the kernel's non-connected routes.

    We capture:
    - The default route(s) (`destination=default`) with their gateway
    - Non-default static routes (manual or DHCP option 121) if they do
      not have the link scope (the 'link' routes are auto-derived from
      the interface IPs, no need to persist them).

    We also update Interface.gateway on the interface carrying the
    default route (useful for the later static mode).
    """
    routes = _ip_route_show()
    touched = 0
    iface_by_name = {i.name: i for i in db.query(models.Interface).all()}
    for r in routes:
        dst = r.get("dst") or ""
        gw = r.get("gateway")
        dev = r.get("dev")
        scope = r.get("scope")
        protocol = r.get("protocol")
        # Auto connected routes (scope=link, protocol=kernel): ignored,
        # they are implicit once the IP is set on the interface.
        if scope == "link" and protocol == "kernel":
            continue
        # Semantic fallback: a route without a gateway (next-hop) is
        # necessarily a connected route (link-scoped) even when scope/protocol
        # are not filled by ip-route (DHCP/manual case on some distros).
        # MurOS only stores next-hop routes; connected ones are auto-derived
        # from the interface IP.
        if not gw and dst != "default":
            continue
        # Normalize 'default' (left as-is by ip route).
        if not dst:
            continue
        # If default with a gateway: record the gateway on the matching
        # interface (useful in static mode). We do NOT create a StaticRoute
        # for the default: it is already materialized by Interface.gateway
        # in _restore_interfaces. Creating an extra StaticRoute would produce
        # a duplicate default route in the kernel (one with metric 0 via the
        # iface, one with the metric captured from the kernel, typically 1002
        # when dhclient had set it
        # set at install).
        if dst == "default":
            if gw and dev and dev in iface_by_name:
                iface = iface_by_name[dev]
                # Capture the kernel default gateway on the interface row
                # regardless of ip_mode. The gateway describes the kernel
                # state and must surface in the UI (Network and Routing
                # pages) even when the interface is DHCP-driven or in
                # "none" mode. Without this, an admin re-importing kernel
                # config sees an empty Routing page despite having a
                # working default route.
                if not iface.gateway:
                    iface.gateway = gw
                    touched += 1
                    log.info("Adopted gateway %s on interface %s", gw, dev)
            continue
        # Create the StaticRoute if missing.
        exists = (
            db.query(models.StaticRoute)
            .filter_by(destination=dst, gateway=gw)
            .one_or_none()
        )
        if exists is not None:
            continue
        route = models.StaticRoute(
            destination=dst,
            gateway=gw,
            interface_id=iface_by_name[dev].id if dev in iface_by_name else None,
            metric=r.get("metric") or 0,
            enabled=True,
            comment="Adopted at install",
            dirty=False,
        )
        db.add(route)
        touched += 1
        log.info("Adopted route %s via %s dev %s", dst, gw or "-", dev or "-")
    db.commit()
    return touched


def should_adopt(db: Session) -> bool:
    """True if adoption has not happened yet.

    Criterion: marker absent AND DB with no registered interface. If an
    interface already exists (upgrade case from an earlier MurOS install),
    we do not run the full adoption - only the ip_mode=none + active IP
    safety net handled in _adopt_interfaces.
    """
    if ADOPTED_MARKER.exists():
        return False
    has_iface = db.query(models.Interface).first() is not None
    return not has_iface


def adopt_kernel_state(db: Session, force: bool = False) -> dict:
    """Capture the kernel network state into the DB. Idempotent via marker.

    Returns {interfaces_touched, routes_touched, skipped}.
    """
    if not force and not should_adopt(db):
        log.info("Adoption already done (marker %s present), skip", ADOPTED_MARKER)
        return {"interfaces_touched": 0, "routes_touched": 0, "skipped": True}
    log.info("=== Adopting kernel network config ===")
    n_iface = _adopt_interfaces(db)
    n_route = _adopt_routes(db)
    try:
        ADOPTED_MARKER.parent.mkdir(parents=True, exist_ok=True)
        ADOPTED_MARKER.touch()
    except OSError as exc:
        log.warning("Could not create marker %s: %s", ADOPTED_MARKER, exc)
    log.info("Adoption done: %d interfaces, %d routes", n_iface, n_route)
    return {"interfaces_touched": n_iface, "routes_touched": n_route, "skipped": False}
