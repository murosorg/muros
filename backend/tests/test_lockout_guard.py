"""Tests for the management-lockout guard (app.lockout_guard).

The guard statically evaluates the pending input chain against a NEW
connection from the operator's source to the web UI and SSH ports, so
the apply flow can warn before a ruleset that only the live (conntrack-
kept) session can still reach is committed.
"""
import pytest


def _reset(s):
    from app import models
    for m in (models.FirewallRule, models.Interface, models.Zone):
        for row in s.query(m).all():
            s.delete(row)
    s.commit()


@pytest.fixture(autouse=True)
def _clean_scenario_tables(tmp_db):
    """Keep the session-scoped DB clean for the next test file.

    The test DB is shared across the whole session, so the rows these
    tests create (zones, interfaces, rules) would otherwise leak into
    later files. Interfaces default to dirty=True, which would inflate
    the network "pending" counter asserted in test_network.py. Reset the
    affected tables after every test so each file starts from a clean
    slate.
    """
    yield
    with tmp_db() as s:
        _reset(s)


def _scenario(s):
    """A two-zone box: lan 10.0.0.1/24, wan 203.0.113.2/30."""
    from app import models
    _reset(s)
    lan = models.Zone(name="lan")
    wan = models.Zone(name="wan")
    s.add_all([lan, wan])
    s.flush()
    s.add(models.Interface(name="eth1", ip_mode="static",
                           ip_address="10.0.0.1/24", zone=lan))
    s.add(models.Interface(name="eth0", ip_mode="static",
                           ip_address="203.0.113.2/30", zone=wan))
    if not s.get(models.HttpConfig, 1):
        s.add(models.HttpConfig(id=1))
    if not s.get(models.SshConfig, 1):
        s.add(models.SshConfig(id=1))
    s.commit()
    return lan, wan


def test_blocked_when_no_input_accept_rule(tmp_db):
    from app import lockout_guard
    with tmp_db() as s:
        _scenario(s)
        r = lockout_guard.analyze(s, "10.0.0.50")
    assert r["evaluated"] is True
    assert r["blocked"] is True
    assert r["source_zone"] == "lan"
    assert r["message"]


def test_not_blocked_when_rule_allows_ui_and_ssh(tmp_db):
    from app import lockout_guard, models
    with tmp_db() as s:
        lan, _ = _scenario(s)
        s.add(models.FirewallRule(chain="input", action="accept",
                                  src_zone=lan, protocol="tcp",
                                  dst_port="22,443", position=10,
                                  enabled=True))
        s.commit()
        r = lockout_guard.analyze(s, "10.0.0.50")
    assert r["evaluated"] is True
    assert r["blocked"] is False


def test_ssh_blocked_when_only_ui_allowed(tmp_db):
    from app import lockout_guard, models
    with tmp_db() as s:
        lan, _ = _scenario(s)
        s.add(models.FirewallRule(chain="input", action="accept",
                                  src_zone=lan, protocol="tcp",
                                  dst_port="443", position=10,
                                  enabled=True))
        s.commit()
        r = lockout_guard.analyze(s, "10.0.0.50")
    assert r["blocked"] is True
    blocked_ports = [p["port"] for p in r["ports"] if not p["reachable"]]
    assert blocked_ports == [22]


def test_skipped_for_routed_source(tmp_db):
    from app import lockout_guard
    with tmp_db() as s:
        _scenario(s)
        r = lockout_guard.analyze(s, "8.8.8.8")
    # Source not on any directly connected subnet: no reliable zone, so
    # the guard does not raise a false alarm.
    assert r["evaluated"] is False
    assert r["blocked"] is False


def test_skipped_for_loopback(tmp_db):
    from app import lockout_guard
    with tmp_db() as s:
        _scenario(s)
        r = lockout_guard.analyze(s, "127.0.0.1")
    assert r["evaluated"] is False
    assert r["blocked"] is False


def test_skipped_for_unknown_source(tmp_db):
    from app import lockout_guard
    with tmp_db() as s:
        _scenario(s)
        r = lockout_guard.analyze(s, None)
    assert r["evaluated"] is False
    assert r["blocked"] is False


def test_lan_rule_does_not_help_wan_admin(tmp_db):
    from app import lockout_guard, models
    with tmp_db() as s:
        lan, _ = _scenario(s)
        s.add(models.FirewallRule(chain="input", action="accept",
                                  src_zone=lan, protocol="tcp",
                                  dst_port="22,443", position=10,
                                  enabled=True))
        s.commit()
        # Admin coming from the WAN subnet: the lan-scoped rule must not
        # match, so management is reported as blocked from there.
        r = lockout_guard.analyze(s, "203.0.113.1")
    assert r["evaluated"] is True
    assert r["source_zone"] == "wan"
    assert r["blocked"] is True


def test_disabled_rule_is_ignored(tmp_db):
    from app import lockout_guard, models
    with tmp_db() as s:
        lan, _ = _scenario(s)
        s.add(models.FirewallRule(chain="input", action="accept",
                                  src_zone=lan, protocol="tcp",
                                  dst_port="22,443", position=10,
                                  enabled=False))
        s.commit()
        r = lockout_guard.analyze(s, "10.0.0.50")
    assert r["blocked"] is True


def test_drop_rule_before_accept_blocks(tmp_db):
    from app import lockout_guard, models
    with tmp_db() as s:
        lan, _ = _scenario(s)
        # A drop at a lower position wins over a later accept.
        s.add(models.FirewallRule(chain="input", action="drop",
                                  src_zone=lan, protocol="tcp",
                                  dst_port="443", position=10,
                                  enabled=True))
        s.add(models.FirewallRule(chain="input", action="accept",
                                  src_zone=lan, protocol="tcp",
                                  dst_port="443", position=20,
                                  enabled=True))
        s.commit()
        r = lockout_guard.analyze(s, "10.0.0.50")
    ui = next(p for p in r["ports"] if p["service"] == "Web UI")
    assert ui["reachable"] is False
