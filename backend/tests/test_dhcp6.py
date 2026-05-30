# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Tests for the Kea DHCPv6 config builder.

Exercises subnet_from_range (pure) and _build_config against an in-memory
SQLite database, so it runs in the backend pytest CI without touching
systemd or the filesystem.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.models import Base
from app.services import dhcp6_apply


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def test_subnet_from_range_valid():
    assert dhcp6_apply.subnet_from_range("2001:db8:1::100") == "2001:db8:1::/64"


def test_subnet_from_range_invalid():
    assert dhcp6_apply.subnet_from_range("not-an-ip") is None
    assert dhcp6_apply.subnet_from_range("192.168.1.10") is None


def test_build_config_disabled_is_idle(db):
    db.add(models.Dhcp6Config(id=1, enabled=False))
    db.commit()
    conf = dhcp6_apply._build_config(db)["Dhcp6"]
    assert conf["subnet6"] == []
    assert conf["interfaces-config"]["interfaces"] == []


def test_build_config_with_pool(db):
    itf = models.Interface(name="eth1", ip_address="2001:db8:1::1/64")
    db.add(itf)
    db.commit()
    db.add(models.Dhcp6Config(id=1, enabled=True, default_lease_seconds=3600))
    db.add(models.Dhcp6Pool(interface_id=itf.id, range_start="2001:db8:1::100",
                            range_end="2001:db8:1::1ff", enabled=True))
    db.commit()
    conf = dhcp6_apply._build_config(db)["Dhcp6"]
    assert conf["interfaces-config"]["interfaces"] == ["eth1"]
    sub = conf["subnet6"][0]
    assert sub["subnet"] == "2001:db8:1::/64"
    assert sub["interface"] == "eth1"
    assert sub["pools"][0]["pool"] == "2001:db8:1::100 - 2001:db8:1::1ff"
    assert sub["valid-lifetime"] == 3600
    # DNS defaults to the interface IPv6 address when none is set.
    assert {"name": "dns-servers", "data": "2001:db8:1::1"} in sub["option-data"]


def test_build_config_explicit_dns(db):
    itf = models.Interface(name="eth2", ip_address="2001:db8:2::1/64")
    db.add(itf)
    db.commit()
    db.add(models.Dhcp6Config(id=1, enabled=True))
    db.add(models.Dhcp6Pool(interface_id=itf.id, range_start="2001:db8:2::10",
                            range_end="2001:db8:2::20", dns_servers="2001:4860:4860::8888",
                            enabled=True))
    db.commit()
    sub = dhcp6_apply._build_config(db)["Dhcp6"]["subnet6"][0]
    assert {"name": "dns-servers", "data": "2001:4860:4860::8888"} in sub["option-data"]
