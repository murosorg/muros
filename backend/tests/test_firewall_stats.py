# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Unit tests for firewall live-counter parsing.

The nft call is replaced by a crafted ``nft -j`` payload so the parsing,
marker matching and per-rule aggregation are exercised without nft.
"""
from app import firewall_stats


def _rule(comment, packets=None, bytes_=None):
    expr = []
    if packets is not None:
        expr.append({"counter": {"packets": packets, "bytes": bytes_}})
    return {"rule": {"comment": comment, "expr": expr}}


def _payload(*entries):
    return {"nftables": list(entries)}


def test_collect_aggregates_variants_and_splits_rules_from_nat(monkeypatch):
    payload = _payload(
        _rule("[muros r=5] allow http", 10, 100),
        _rule("[muros r=5] allow http", 5, 50),    # second protocol variant
        _rule("[muros nat=3] publish", 2, 20),
        _rule("[muros r=9] no counter"),            # no counter -> ignored
        {"rule": {"comment": "no marker", "expr": [{"counter": {"packets": 1, "bytes": 1}}]}},
        {"chain": {"name": "input"}},               # not a rule entry
    )
    monkeypatch.setattr(firewall_stats, "_read_ruleset_json", lambda: payload)
    out = firewall_stats.collect_counters()
    assert out["rules"][5] == {"packets": 15, "bytes": 150}
    assert out["nat"][3] == {"packets": 2, "bytes": 20}
    assert 9 not in out["rules"]


def test_collect_empty_when_no_ruleset(monkeypatch):
    monkeypatch.setattr(firewall_stats, "_read_ruleset_json", lambda: {})
    assert firewall_stats.collect_counters() == {"rules": {}, "nat": {}}


def test_extract_counter_returns_none_without_counter():
    assert firewall_stats._extract_counter({"expr": [{"match": {}}]}) is None


def test_extract_counter_reads_packets_and_bytes():
    rule = {"expr": [{"counter": {"packets": 7, "bytes": 70}}]}
    assert firewall_stats._extract_counter(rule) == (7, 70)


def test_iter_rules_skips_non_rule_entries():
    payload = _payload({"chain": {}}, _rule("[muros r=1]", 1, 1), "garbage")
    rules = list(firewall_stats._iter_rules(payload))
    assert len(rules) == 1
    assert rules[0]["comment"] == "[muros r=1]"
