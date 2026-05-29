"""Live nftables counters per MurOS rule.

Parses the output of ``nft -j list ruleset`` and maps each entry back
to its originating DB rule through the comment marker emitted by
``compiler.py``::

    [muros r=<id>]   for filter rules
    [muros nat=<id>] for NAT rules

The ``counter`` keyword is emitted on every rule by the compiler, so
each entry in the JSON tree carries a ``counter`` block with
``packets`` and ``bytes``. We sum across protocol-variant duplicates
(when a single DB rule produces several nft lines, e.g. tcp+udp).

This module is read-only: it never touches the ruleset, it only
inspects runtime state. Counters reset on every Apply (ruleset reload)
which is fine for the UI (the column displays activity since the last
Apply).
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from typing import Iterator


_RULE_MARKER = re.compile(r"\[muros r=(\d+)\]")
_NAT_MARKER = re.compile(r"\[muros nat=(\d+)\]")


@dataclass
class Counter:
    packets: int = 0
    bytes: int = 0

    def merge(self, packets: int, bytes_: int) -> None:
        self.packets += packets
        self.bytes += bytes_


def _iter_rules(payload: dict) -> Iterator[dict]:
    """Yield the ``rule`` sub-objects from a parsed ``nft -j`` payload."""
    for entry in payload.get("nftables", []):
        if not isinstance(entry, dict):
            continue
        rule = entry.get("rule")
        if isinstance(rule, dict):
            yield rule


def _extract_counter(rule: dict) -> tuple[int, int] | None:
    """Return (packets, bytes) for a rule entry, or None when absent.

    The ``expr`` list contains the chain of expressions; the ``counter``
    expression appears as ``{"counter": {"packets": N, "bytes": M}}``.
    """
    for expr in rule.get("expr", []) or []:
        if not isinstance(expr, dict):
            continue
        ctr = expr.get("counter")
        if isinstance(ctr, dict):
            return int(ctr.get("packets", 0) or 0), int(ctr.get("bytes", 0) or 0)
    return None


def _read_ruleset_json() -> dict:
    """Return the parsed JSON ruleset, or an empty dict on failure.

    Failure modes (nft missing, ruleset empty, permission denied) all
    map to "no counters available" rather than an error: the UI keeps
    showing the rules normally, just without per-rule packets/bytes.
    """
    try:
        proc = subprocess.run(
            ["nft", "-j", "list", "ruleset"],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {}
    if proc.returncode != 0 or not proc.stdout.strip():
        return {}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {}


def collect_counters() -> dict[str, dict[int, dict[str, int]]]:
    """Return ``{"rules": {id: {packets, bytes}}, "nat": {...}}``.

    Both maps may be empty if nft is unreachable or the ruleset has not
    been applied yet. Identical rule IDs may appear several times in
    the nft tree (one nft line per protocol variant). Counters are
    summed across variants so the UI sees a single aggregate per DB
    rule.
    """
    payload = _read_ruleset_json()
    rules: dict[int, Counter] = {}
    nat: dict[int, Counter] = {}

    for rule in _iter_rules(payload):
        comment = rule.get("comment") or ""
        ctr = _extract_counter(rule)
        if ctr is None:
            continue
        m = _RULE_MARKER.search(comment)
        if m:
            rid = int(m.group(1))
            rules.setdefault(rid, Counter()).merge(*ctr)
            continue
        m = _NAT_MARKER.search(comment)
        if m:
            nid = int(m.group(1))
            nat.setdefault(nid, Counter()).merge(*ctr)

    return {
        "rules": {rid: {"packets": c.packets, "bytes": c.bytes} for rid, c in rules.items()},
        "nat": {nid: {"packets": c.packets, "bytes": c.bytes} for nid, c in nat.items()},
    }
