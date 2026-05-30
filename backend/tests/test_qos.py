# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Tests for the QoS / traffic-shaping compiler and DB spec builder.

Pure / DB-only (MUROS_APPLY=0): they exercise the tc command generation
and the DB-to-spec mapping without touching the kernel, so they run in
the backend pytest CI job on every push.
"""
from __future__ import annotations

import pytest

from app import qos


def _spec(**over):
    base = dict(
        ifname="eth0",
        bandwidth_kbit=95000,
        classes=[
            {"minor": 10, "rate_kbit": 9500, "ceil_kbit": None,
             "priority": 3, "is_default": True},
            {"minor": 11, "rate_kbit": 20000, "ceil_kbit": 95000,
             "priority": 0, "is_default": False},
        ],
        rules=[],
    )
    base.update(over)
    return base


def test_compile_builds_root_htb_with_default_class():
    cmds = qos.compile_interface(_spec())
    joined = [" ".join(c) for c in cmds]
    # Root qdisc points at the default class minor (10).
    assert joined[0] == "tc qdisc add dev eth0 root handle 1: htb default 10"
    # Link root class is capped at the full bandwidth.
    assert "tc class add dev eth0 parent 1: classid 1:1 htb rate 95000kbit ceil 95000kbit" in joined


def test_compile_class_without_ceil_defaults_to_bandwidth():
    cmds = qos.compile_interface(_spec())
    joined = [" ".join(c) for c in cmds]
    # Class 10 has ceil_kbit=None -> falls back to the shaper bandwidth.
    assert "tc class add dev eth0 parent 1:1 classid 1:10 htb rate 9500kbit ceil 95000kbit prio 3" in joined
    # Class 11 keeps its explicit ceil.
    assert "tc class add dev eth0 parent 1:1 classid 1:11 htb rate 20000kbit ceil 95000kbit prio 0" in joined


def test_compile_adds_fq_codel_leaf_per_class():
    cmds = qos.compile_interface(_spec())
    joined = [" ".join(c) for c in cmds]
    assert "tc qdisc add dev eth0 parent 1:10 handle 10: fq_codel" in joined
    assert "tc qdisc add dev eth0 parent 1:11 handle 11: fq_codel" in joined


def test_filter_dscp_and_port_match():
    spec = _spec(rules=[
        {"minor": 11, "protocol": "udp", "dst_port": 5060,
         "src_address": None, "dst_address": None, "dscp": 46},
    ])
    cmds = qos.compile_interface(spec)
    flt = " ".join(cmds[-1])
    # DSCP 46 (EF) -> dsfield 184 (46<<2) masked 0xfc.
    assert "match ip dsfield 184 0xfc" in flt
    assert "match ip protocol 17 0xff" in flt
    assert "match ip dport 5060 0xffff" in flt
    assert flt.endswith("flowid 1:11")


def test_filter_without_criteria_is_match_all():
    spec = _spec(rules=[
        {"minor": 10, "protocol": None, "dst_port": None,
         "src_address": None, "dst_address": None, "dscp": None},
    ])
    cmds = qos.compile_interface(spec)
    assert "match u32 0 0" in " ".join(cmds[-1])


def test_filter_address_match():
    spec = _spec(rules=[
        {"minor": 11, "protocol": "tcp", "dst_port": None,
         "src_address": "192.168.1.0/24", "dst_address": "8.8.8.8", "dscp": None},
    ])
    flt = " ".join(qos.compile_interface(spec)[-1])
    assert "match ip src 192.168.1.0/24" in flt
    assert "match ip dst 8.8.8.8" in flt
    assert "match ip protocol 6 0xff" in flt


def test_compile_rejects_bad_ifname():
    with pytest.raises(ValueError):
        qos.compile_interface(_spec(ifname="eth0; rm -rf /"))


def test_validate_class_ceil_below_rate():
    with pytest.raises(ValueError):
        qos.validate_class(rate_kbit=1000, ceil_kbit=500, priority=0)


def test_validate_rule_bad_dscp():
    with pytest.raises(ValueError):
        qos.validate_rule("udp", 5060, None, None, 99)


def test_specs_from_db_skips_disabled_and_classless(tmp_db):
    from app import models
    db = tmp_db()
    try:
        iface = models.Interface(name="eth0", type="ethernet", ip_mode="none")
        db.add(iface)
        db.flush()
        # Enabled shaper with one class + one enabled rule.
        sh = models.QosShaper(interface_id=iface.id, enabled=True, bandwidth_kbit=50000)
        db.add(sh)
        db.flush()
        cls = models.QosClass(shaper_id=sh.id, name="Voice", minor=20,
                              priority=0, rate_kbit=10000, is_default=True)
        db.add(cls)
        db.flush()
        db.add(models.QosRule(class_id=cls.id, protocol="udp", dst_port=5060,
                              dscp=46, enabled=True, position=0))
        # Disabled shaper on a second interface: must be ignored.
        iface2 = models.Interface(name="eth1", type="ethernet", ip_mode="none")
        db.add(iface2)
        db.flush()
        db.add(models.QosShaper(interface_id=iface2.id, enabled=False, bandwidth_kbit=1000))
        db.commit()
        specs = qos.specs_from_db(db)
        assert len(specs) == 1
        assert specs[0]["ifname"] == "eth0"
        assert specs[0]["classes"][0]["minor"] == 20
        assert specs[0]["rules"][0]["dst_port"] == 5060
    finally:
        db.close()
