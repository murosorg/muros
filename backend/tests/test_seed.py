# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Tests for the first-boot seeding (default zones, rules, service defaults).

These pin the out-of-box firewall posture (the seed rules) and the
default-closed/secure service defaults. The system probes are stubbed so
the tests are deterministic and never touch the host.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import models, seed


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    models.Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture(autouse=True)
def _no_system_probe(monkeypatch):
    # By default, pretend the box has no interfaces so seed_if_empty only
    # exercises the zones/rules path. Individual tests can override.
    monkeypatch.setattr(seed, "list_system_interfaces", lambda: [])
    monkeypatch.setattr(seed, "get_default_gateway", lambda name: None)


def _rules(session):
    return session.query(models.FirewallRule).all()


def test_seed_creates_three_default_zones(session):
    seed.seed_if_empty(session)
    names = {z.name for z in session.query(models.Zone).all()}
    assert names == {"wan", "lan", "dmz"}


def test_seed_admin_input_rules_open_ssh_ui_icmp(session):
    seed.seed_if_empty(session)
    inp = [r for r in _rules(session) if r.chain == "input"]
    ports = {(r.protocol, r.dst_port) for r in inp}
    assert ("tcp", "22") in ports
    assert ("tcp", "80,443") in ports
    assert ("icmp", None) in ports


def test_seed_allows_lan_to_firewall_and_lan_to_any(session):
    seed.seed_if_empty(session)
    lan = session.query(models.Zone).filter_by(name="lan").one()
    inp = [r for r in _rules(session) if r.chain == "input" and r.src_zone_id == lan.id]
    fwd = [r for r in _rules(session) if r.chain == "forward" and r.src_zone_id == lan.id]
    assert len(inp) == 1                      # LAN -> firewall
    assert len(fwd) == 1 and fwd[0].action == "accept"  # LAN -> any
    assert fwd[0].dst_zone_id is None


def test_seed_has_no_explicit_catch_all_drop(session):
    # The chains rely on policy drop; there must be no seeded drop rule.
    seed.seed_if_empty(session)
    assert all(r.action == "accept" for r in _rules(session))


def test_seed_is_idempotent(session):
    seed.seed_if_empty(session)
    before = len(_rules(session))
    seed.seed_if_empty(session)  # zones already present -> no-op
    assert len(_rules(session)) == before


def test_seed_imports_physical_interface_with_live_ip(session, monkeypatch):
    monkeypatch.setattr(seed, "list_system_interfaces", lambda: [{
        "name": "eth0", "is_virtual": False, "mac": "aa:bb:cc:dd:ee:ff",
        "mtu": 1500, "state": "UP",
        "addresses": ["169.254.1.1/16", "192.168.1.50/24", "fe80::1/64"],
    }])
    monkeypatch.setattr(seed, "get_default_gateway", lambda name: "192.168.1.1")
    seed.seed_if_empty(session)
    eth0 = session.query(models.Interface).filter_by(name="eth0").one()
    # link-local and IPv6 skipped, first global IPv4 captured as static.
    assert eth0.ip_mode == "static"
    assert eth0.ip_address == "192.168.1.50/24"
    assert eth0.gateway == "192.168.1.1"


def test_seed_skips_virtual_interfaces(session, monkeypatch):
    monkeypatch.setattr(seed, "list_system_interfaces", lambda: [{
        "name": "docker0", "is_virtual": True, "mac": "", "mtu": 1500,
        "state": "UP", "addresses": ["172.17.0.1/16"],
    }])
    seed.seed_if_empty(session)
    assert session.query(models.Interface).filter_by(name="docker0").first() is None


def test_seed_root_user_creates_admin_with_ui_access(session):
    seed.seed_root_user(session)
    root = session.query(models.User).filter_by(username="root").one()
    assert root.is_admin and root.ui_access


def test_seed_root_user_reasserts_grants(session):
    session.add(models.User(username="root", password_hash="!",
                            is_admin=False, ui_access=False,
                            must_change_password=False))
    session.commit()
    seed.seed_root_user(session)
    root = session.query(models.User).filter_by(username="root").one()
    assert root.is_admin and root.ui_access


def test_seed_ssh_is_closed_by_default(session):
    seed.seed_ssh_disabled_by_default(session)
    cfg = session.get(models.SshConfig, 1)
    assert cfg is not None and cfg.admin_disabled is True


def test_seed_ssh_does_not_touch_existing_row(session):
    session.add(models.SshConfig(id=1, admin_disabled=False))
    session.commit()
    seed.seed_ssh_disabled_by_default(session)
    assert session.get(models.SshConfig, 1).admin_disabled is False


def test_seed_snmp_row_created_once(session):
    seed.seed_snmp_if_missing(session)
    assert session.get(models.SnmpConfig, 1) is not None
