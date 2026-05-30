# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Tests for the WireGuard `wg show dump` runtime parser.

Pure string parsing (no kernel, no subprocess): exercises the mapping from
`wg show <if> dump` output to the per-peer runtime dict used by the live
peer-status endpoint.
"""
from __future__ import annotations

from app import wireguard

# Real-world `wg show wg0 dump` layout: first line is the interface
# (private_key, public_key, listen_port, fwmark), following lines are peers
# (public_key, preshared_key, endpoint, allowed_ips, latest_handshake,
#  rx_bytes, tx_bytes, persistent_keepalive). Fields are tab-separated.
_DUMP = (
    "PRIVkeyAAA\tSRVpubBBB\t51820\toff\n"
    "PEERpub1\t(none)\t203.0.113.5:51820\t10.10.0.2/32\t1700000000\t4096\t8192\t25\n"
    "PEERpub2\tPSKxyz\t(none)\t10.10.0.3/32\t0\t0\t0\t0\n"
)


def test_parse_wg_dump_skips_interface_line_and_maps_peers():
    parsed = wireguard._parse_wg_dump(_DUMP, "wg0")
    assert set(parsed) == {"PEERpub1", "PEERpub2"}
    assert "SRVpubBBB" not in parsed


def test_parse_wg_dump_connected_peer_fields():
    parsed = wireguard._parse_wg_dump(_DUMP, "wg0")
    p1 = parsed["PEERpub1"]
    assert p1["interface"] == "wg0"
    assert p1["endpoint"] == "203.0.113.5:51820"
    assert p1["latest_handshake"] == 1700000000
    assert p1["rx_bytes"] == 4096
    assert p1["tx_bytes"] == 8192


def test_parse_wg_dump_never_handshaked_peer():
    parsed = wireguard._parse_wg_dump(_DUMP, "wg0")
    p2 = parsed["PEERpub2"]
    # '(none)' endpoint normalises to None; never-handshaked -> 0.
    assert p2["endpoint"] is None
    assert p2["latest_handshake"] == 0
    assert p2["rx_bytes"] == 0
    assert p2["tx_bytes"] == 0


def test_parse_wg_dump_empty_and_malformed_lines():
    assert wireguard._parse_wg_dump("", "wg0") == {}
    # Interface-only output (no peers) yields no entries.
    assert wireguard._parse_wg_dump("PRIV\tPUB\t51820\toff\n", "wg0") == {}
    # Truncated peer line (too few fields) is skipped, not crashing.
    short = "PRIV\tPUB\t51820\toff\nPEERpub\t(none)\t1.2.3.4:51820\n"
    assert wireguard._parse_wg_dump(short, "wg0") == {}


from types import SimpleNamespace  # noqa: E402


def _cfg(client_dns=""):
    return SimpleNamespace(
        public_key="SRVpub", listen_port=51820,
        public_endpoint="vpn.example.com", client_dns=client_dns,
    )


def _peer():
    return SimpleNamespace(
        name="laptop", allowed_ips="10.10.0.2/32", preshared_key=None,
        client_allowed_ips="", endpoint=None, persistent_keepalive=0,
    )


def test_client_config_pushes_dns_when_set():
    text = wireguard.render_peer_client_config(_cfg("10.10.0.1, 1.1.1.1"), _peer(), "PRIV")
    assert "DNS = 10.10.0.1, 1.1.1.1" in text
    # The DNS line belongs to the [Interface] section, before [Peer].
    assert text.index("DNS = ") < text.index("[Peer]")


def test_client_config_no_dns_line_when_unset():
    text = wireguard.render_peer_client_config(_cfg(""), _peer(), "PRIV")
    assert "DNS = " not in text
