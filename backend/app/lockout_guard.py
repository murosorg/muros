# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Pre-apply management-lockout guard for the firewall ruleset.

The commit-confirm modal proves the operator's *current* session still
works after an apply, but a stateful firewall keeps that session alive
through ``ct state established,related accept`` regardless of the filter
rules. So deleting the rule that allows *new* management connections
(same IP, same port) can be confirmed without warning, and only locks the
operator out at the next reconnect.

This module statically evaluates the pending input chain against a
synthetic NEW TCP connection from the operator's source to the web UI and
SSH ports. If no accept path exists, the apply flow surfaces a blocking
warning so the operator acknowledges the risk explicitly.

The evaluation mirrors :mod:`app.compiler` semantics for the input chain:
loopback and established/related are baseline accepts (not relevant to a
NEW external connection), then user rules are walked in (position, id)
order, first match wins, default policy is drop.
"""
from __future__ import annotations

import ipaddress
import logging

from sqlalchemy.orm import Session

from app import models

log = logging.getLogger(__name__)

IpAddr = ipaddress.IPv4Address | ipaddress.IPv6Address


def _parse_ip(value: str | None) -> IpAddr | None:
    if not value:
        return None
    try:
        return ipaddress.ip_address(value.strip())
    except ValueError:
        return None


def _ip_in_value(ip: IpAddr, value: str) -> bool:
    """True if ip is covered by an address selector (ip / cidr / a-b range)."""
    value = (value or "").strip()
    if not value:
        return False
    try:
        if "/" in value:
            net = ipaddress.ip_network(value, strict=False)
            return ip.version == net.version and ip in net
        if "-" in value:
            lo_s, hi_s = value.split("-", 1)
            lo = ipaddress.ip_address(lo_s.strip())
            hi = ipaddress.ip_address(hi_s.strip())
            return ip.version == lo.version == hi.version and lo <= ip <= hi
        addr = ipaddress.ip_address(value)
        return ip.version == addr.version and ip == addr
    except ValueError:
        return False


def _port_in_spec(spec: str | None, port: int) -> bool:
    """True if ``port`` falls in a port spec like '22', '22,80', '1000-2000'."""
    if not spec:
        return False
    for token in spec.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            if "-" in token:
                lo_s, hi_s = token.split("-", 1)
                if int(lo_s) <= port <= int(hi_s):
                    return True
            elif int(token) == port:
                return True
        except ValueError:
            continue
    return False


def _addr_values(group, single: str | None) -> list[str]:
    if group is not None and getattr(group, "entries", None):
        return [e.value for e in group.entries]
    return [single] if single else []


def _rule_allows_tcp_port(rule: models.FirewallRule, port: int) -> bool:
    """True if the rule's proto/port selector matches a TCP packet to ``port``."""
    sg = rule.service_group
    if sg is not None and sg.ports:
        for p in sg.ports:
            if p.protocol == "tcp" and _port_in_spec(p.port, port):
                return True
        return False
    proto = (rule.protocol or "").lower()
    if proto in ("udp", "icmp"):
        return False
    # tcp, any, or unset: a dst_port narrows it, otherwise all ports match.
    if rule.dst_port:
        return _port_in_spec(rule.dst_port, port)
    return True


def _rule_matches_input(
    rule: models.FirewallRule,
    ingress_iface: models.Interface,
    src_ip: IpAddr,
    firewall_ips: list[IpAddr],
    port: int,
) -> bool:
    # A dst_zone on an input rule compiles to oifname, which never matches
    # host-bound traffic; such a rule is dead for the input hook.
    if rule.dst_zone is not None:
        return False
    # src_zone: None = any; otherwise the ingress interface must belong to
    # the zone. An empty zone matches nothing (compiler guard).
    if rule.src_zone is not None:
        zone_ifaces = [i.name for i in rule.src_zone.interfaces]
        if not zone_ifaces or ingress_iface.name not in zone_ifaces:
            return False
    src_vals = _addr_values(rule.src_address_group, rule.src_address)
    if src_vals and not any(_ip_in_value(src_ip, v) for v in src_vals):
        return False
    dst_vals = _addr_values(rule.dst_address_group, rule.dst_address)
    if dst_vals and not any(
        any(_ip_in_value(fip, v) for fip in firewall_ips) for v in dst_vals
    ):
        return False
    return _rule_allows_tcp_port(rule, port)


def _input_reaches(
    db: Session,
    ingress_iface: models.Interface,
    src_ip: IpAddr,
    firewall_ips: list[IpAddr],
    port: int,
) -> bool:
    rules = (
        db.query(models.FirewallRule)
        .filter(models.FirewallRule.enabled.is_(True))
        .filter(models.FirewallRule.chain == "input")
        .order_by(models.FirewallRule.position, models.FirewallRule.id)
        .all()
    )
    for r in rules:
        if _rule_matches_input(r, ingress_iface, src_ip, firewall_ips, port):
            return (r.action or "").lower() == "accept"
    # No matching rule -> input policy drop.
    return False


def _resolve_ingress(db: Session, src_ip: IpAddr):
    """Return (ingress_iface, firewall_ips).

    ingress is the directly connected interface whose subnet contains
    src_ip (longest prefix wins), or None when the source is not on any
    directly connected subnet (routed / remote admin).
    """
    ifaces = (
        db.query(models.Interface)
        .filter(models.Interface.ip_mode == "static")
        .all()
    )
    firewall_ips: list[IpAddr] = []
    ingress = None
    best_prefix = -1
    for iface in ifaces:
        if not iface.ip_address:
            continue
        try:
            net = ipaddress.ip_network(iface.ip_address, strict=False)
            host = ipaddress.ip_address(iface.ip_address.split("/", 1)[0])
        except ValueError:
            continue
        firewall_ips.append(host)
        if (
            src_ip.version == net.version
            and src_ip in net
            and net.prefixlen > best_prefix
        ):
            ingress = iface
            best_prefix = net.prefixlen
    return ingress, firewall_ips


def analyze(db: Session, src_ip_str: str | None) -> dict:
    """Evaluate whether the pending input chain would still accept NEW
    management connections (web UI, SSH) from ``src_ip_str``.

    Returns a report dict. ``evaluated`` is False (and ``blocked`` False)
    when the guard cannot reason reliably (unknown / loopback source, or a
    source that is not on a directly connected subnet); in that case we
    never raise a false alarm.
    """
    report: dict = {
        "evaluated": False,
        "blocked": False,
        "source_ip": src_ip_str,
        "source_zone": None,
        "ports": [],
        "message": None,
    }
    src_ip = _parse_ip(src_ip_str)
    if src_ip is None or src_ip.is_loopback:
        return report

    ingress, firewall_ips = _resolve_ingress(db, src_ip)
    if ingress is None:
        return report

    report["evaluated"] = True
    report["source_zone"] = ingress.zone.name if ingress.zone else None

    targets: list[tuple[int, str]] = []
    http = db.get(models.HttpConfig, 1)
    ui_port = http.port_https if http else 443
    targets.append((ui_port, "Web UI"))
    ssh = db.get(models.SshConfig, 1)
    if ssh is not None and ssh.enabled and not ssh.admin_disabled:
        targets.append((ssh.port, "SSH"))

    blocked_any = False
    for port, label in targets:
        reachable = _input_reaches(db, ingress, src_ip, firewall_ips, port)
        report["ports"].append(
            {"port": port, "service": label, "reachable": reachable}
        )
        if not reachable:
            blocked_any = True

    report["blocked"] = blocked_any
    if blocked_any:
        names = ", ".join(
            f"{p['service']} (tcp/{p['port']})"
            for p in report["ports"]
            if not p["reachable"]
        )
        zone = report["source_zone"] or ingress.name
        report["message"] = (
            f"This ruleset would block NEW management connections to {names} "
            f"from your address {src_ip_str} (zone {zone}). You are only still "
            f"connected through an existing session kept alive by conntrack, "
            f"so the confirmation cannot detect this. Applying may lock you "
            f"out at the next reconnect."
        )
    return report
