# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Regression tests for the per-service config generators.

These cover the subsystems that have broken silently in the past because
nothing exercised their rendering path on every change: WireGuard (VPN),
SNMP, NTP (chrony), DHCP (Kea) and DNS (Unbound). They are deliberately
pure / DB-only (no systemd, no netlink, MUROS_APPLY=0) so they run on
every push and pull request in the `backend (pytest)` CI job, and answer
the question "did this change break the VPN or SNMP config?" without a
full deployment.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest


# --------------------------------------------------------------------------
# WireGuard (VPN)
# --------------------------------------------------------------------------

def _wg_cfg(**over):
    base = dict(
        enabled=True,
        interface_name="wg0",
        address_cidr="10.10.0.1/24",
        listen_port=51820,
        private_key="cFFakePrivateKeyForRenderingTestsOnly00000=",
        public_key="cFFakePublicKeyForRenderingTestsOnly000000=",
        mtu=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _wg_peer(**over):
    base = dict(
        name="laptop",
        description="",
        public_key="peerPublicKeyForRenderingTestsOnly0000000=",
        preshared_key=None,
        allowed_ips="10.10.0.2/32",
        endpoint=None,
        persistent_keepalive=0,
        enabled=True,
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_wireguard_render_interface_section():
    from app import wireguard
    conf = wireguard.render_config(_wg_cfg(), [])
    assert "[Interface]" in conf
    assert "PrivateKey = cFFakePrivateKeyForRenderingTestsOnly00000=" in conf
    assert "Address = 10.10.0.1/24" in conf
    assert "ListenPort = 51820" in conf


def test_wireguard_render_mtu_optional():
    from app import wireguard
    assert "MTU" not in wireguard.render_config(_wg_cfg(mtu=None), [])
    assert "MTU = 1280" in wireguard.render_config(_wg_cfg(mtu=1280), [])


def test_wireguard_render_includes_enabled_peer():
    from app import wireguard
    peer = _wg_peer(
        preshared_key="pskForRenderingTestsOnly0000000000000000000=",
        endpoint="vpn.example.com:51820",
        persistent_keepalive=25,
    )
    conf = wireguard.render_config(_wg_cfg(), [peer])
    assert "[Peer]" in conf
    assert "PublicKey = peerPublicKeyForRenderingTestsOnly0000000=" in conf
    assert "AllowedIPs = 10.10.0.2/32" in conf
    assert "PresharedKey = pskForRenderingTestsOnly0000000000000000000=" in conf
    assert "Endpoint = vpn.example.com:51820" in conf
    assert "PersistentKeepalive = 25" in conf


def test_wireguard_render_skips_disabled_peer():
    from app import wireguard
    conf = wireguard.render_config(_wg_cfg(), [_wg_peer(enabled=False)])
    assert "[Peer]" not in conf
    assert "peerPublicKeyForRenderingTestsOnly" not in conf


def test_wireguard_render_requires_key_and_address():
    from app import wireguard
    with pytest.raises(ValueError):
        wireguard.render_config(_wg_cfg(private_key=""), [])
    with pytest.raises(ValueError):
        wireguard.render_config(_wg_cfg(address_cidr=""), [])


def test_wireguard_keypair_roundtrips():
    from app import wireguard
    kp = wireguard.generate_keypair()
    assert wireguard._pubkey_from_priv(kp["private_key"]) == kp["public_key"]
    import base64
    assert len(base64.standard_b64decode(wireguard.generate_psk())) == 32


# --------------------------------------------------------------------------
# SNMP
# --------------------------------------------------------------------------

def _snmp_cfg(**over):
    base = dict(
        enabled=True,
        port=161,
        community="public",
        allowed_networks="192.168.0.0/16,10.0.0.0/8",
        syscontact="admin@localhost",
        syslocation="MurOS firewall",
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_snmp_render_basic_directives():
    from app import snmp
    conf = snmp.render_conf(_snmp_cfg())
    assert "agentAddress udp:161" in conf
    assert "sysLocation MurOS firewall" in conf
    assert "sysContact admin@localhost" in conf


def test_snmp_render_one_rocommunity_per_cidr():
    from app import snmp
    conf = snmp.render_conf(_snmp_cfg())
    assert "rocommunity public 192.168.0.0/16" in conf
    assert "rocommunity public 10.0.0.0/8" in conf


def test_snmp_render_empty_networks_locks_to_loopback():
    from app import snmp
    conf = snmp.render_conf(_snmp_cfg(allowed_networks="   "))
    assert "rocommunity public 127.0.0.1/32" in conf
    assert "0.0.0.0/0" not in conf


# --------------------------------------------------------------------------
# NTP (chrony drop-in)
# --------------------------------------------------------------------------

def test_ntp_writes_servers_and_serves_lan(tmp_path, monkeypatch):
    from app import ntp
    conf = tmp_path / "muros.conf"
    monkeypatch.setattr(ntp, "MUROS_CHRONY_CONF", conf)
    ntp._write_conf(["0.pool.ntp.org", "1.pool.ntp.org"], serve_lan=True)
    text = conf.read_text()
    assert "server 0.pool.ntp.org iburst" in text
    assert "server 1.pool.ntp.org iburst" in text
    assert "allow all" in text


def test_ntp_client_only_has_no_allow(tmp_path, monkeypatch):
    from app import ntp
    conf = tmp_path / "muros.conf"
    monkeypatch.setattr(ntp, "MUROS_CHRONY_CONF", conf)
    ntp._write_conf(["0.pool.ntp.org"], serve_lan=False)
    assert "allow all" not in conf.read_text()


# --------------------------------------------------------------------------
# DHCP (Kea) -- DB backed
# --------------------------------------------------------------------------

def test_dhcp_render_is_valid_json_when_idle(tmp_db):
    from app.services import dhcp_apply
    with tmp_db() as s:
        text = dhcp_apply.render(s)
    body = "\n".join(
        ln for ln in text.splitlines() if not ln.lstrip().startswith("//")
    )
    data = json.loads(body)
    assert "Dhcp4" in data
    assert data["Dhcp4"]["subnet4"] == []


def test_dhcp_build_config_has_control_socket(tmp_db):
    from app.services import dhcp_apply
    with tmp_db() as s:
        cfg = dhcp_apply._build_config(s)
    assert cfg["Dhcp4"]["control-socket"]["socket-type"] == "unix"
    assert cfg["Dhcp4"]["lease-database"]["type"] == "memfile"


# --------------------------------------------------------------------------
# DNS (Unbound) -- DB backed
# --------------------------------------------------------------------------

def test_dns_render_empty_when_disabled(tmp_db):
    from app.services import dns_apply
    with tmp_db() as s:
        cfg = dns_apply._get_singleton(s)
        cfg.enabled = False
        s.commit()
        assert dns_apply.render(s) == ""


def test_dns_render_when_enabled_has_acl_and_allowlist(tmp_db):
    from app.services import dns_apply
    with tmp_db() as s:
        cfg = dns_apply._get_singleton(s)
        cfg.enabled = True
        s.commit()
        text = dns_apply.render(s)
    assert "access-control: 0.0.0.0/0 allow" in text
    assert "qname-minimisation: yes" in text
    assert 'local-zone: "debian.org." transparent' in text
    assert 'local-zone: "github.com." transparent' in text
