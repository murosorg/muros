# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Unit tests for the nftables rule compiler.

These are pure tests: they build transient FirewallRule instances and call
the compiler helpers directly, with no database session and no kernel
apply. They lock in the IP-family behavior of the `inet` filter table.
"""
from app import models
from app.compiler import _compile_proto_ports


def _rule(**kw):
    """Build a transient FirewallRule with sane defaults for compilation."""
    defaults = dict(protocol=None, src_port=None, dst_port=None)
    defaults.update(kw)
    return models.FirewallRule(**defaults)


def test_portless_tcp_matches_both_families():
    # A "allow tcp" rule with no port must match IPv4 and IPv6 inside the
    # inet table. `ip protocol tcp` would only match IPv4.
    assert _compile_proto_ports(_rule(protocol="tcp")) == ["meta l4proto tcp"]


def test_portless_udp_matches_both_families():
    assert _compile_proto_ports(_rule(protocol="udp")) == ["meta l4proto udp"]


def test_tcp_with_dport_uses_transport_match():
    # With a port, the bare `tcp dport` keyword already matches both
    # families, so it is kept as-is.
    assert _compile_proto_ports(_rule(protocol="tcp", dst_port="443")) == ["tcp dport 443"]


def test_tcp_with_port_list_builds_set():
    assert _compile_proto_ports(_rule(protocol="tcp", dst_port="80,443")) == [
        "tcp dport { 80, 443 }"
    ]


def test_icmp_covers_v4_and_v6():
    assert _compile_proto_ports(_rule(protocol="icmp")) == [
        "meta l4proto { icmp, ipv6-icmp }"
    ]


def test_any_protocol_without_ports_has_no_selector():
    assert _compile_proto_ports(_rule(protocol="any")) == [""]


def test_any_protocol_with_dport_expands_to_tcp_and_udp():
    assert _compile_proto_ports(_rule(protocol="any", dst_port="53")) == [
        "tcp dport 53",
        "udp dport 53",
    ]
