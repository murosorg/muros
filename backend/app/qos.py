# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Apply QoS / traffic shaping to the kernel via tc (iproute2).

Shaping is egress-only and uses the classic HTB (hierarchical token
bucket) qdisc with an fq_codel leaf on each class. This is the most
battle-tested combination on Linux and is exactly what OpenWrt SQM and
VyOS use under the hood.

Tree built per shaped interface <if>:

    qdisc htb 1: root, default <default_minor>
      class 1:1 htb rate <bw> ceil <bw>            (link root)
        class 1:<minor> htb rate <rate> ceil <ceil> prio <prio>
          qdisc fq_codel <minor>:                  (leaf, fights bufferbloat)
        ... one per QoS class ...
      filter ... u32 ... flowid 1:<minor>          (one per QoS rule)

A single source of truth (the SQLite DB) is compiled to a flat list of
`tc` argument vectors and replayed against the kernel. There is no
intermediate config file, so no desync is possible. tc state is volatile
(lost on reboot), so muros-boot.service replays it at every boot from the
same DB rows.

Every command respects MUROS_APPLY: in dry-run mode nothing is executed.
"""
from __future__ import annotations

import ipaddress
import logging
import os
import re
import subprocess

log = logging.getLogger("muros.qos")

APPLY_ENABLED = os.environ.get("MUROS_APPLY", "false").lower() in ("1", "true", "yes")

_VALID_IFNAME = re.compile(r"^[A-Za-z0-9._-]{1,15}$")
_PROTO_NUM = {"tcp": 6, "udp": 17}


def _run(args: list[str], *, check: bool = True) -> tuple[int, str]:
    """Run one tc command. Returns (rc, output). Honors MUROS_APPLY."""
    if not APPLY_ENABLED:
        log.debug("dry-run: %s", " ".join(args))
        return 0, "dry-run"
    try:
        res = subprocess.run(args, capture_output=True, text=True, timeout=5)
    except (subprocess.SubprocessError, FileNotFoundError) as exc:
        return 1, str(exc)
    out = (res.stdout + res.stderr).strip()
    if check and res.returncode != 0:
        log.warning("tc failed (%s): %s", res.returncode, out)
    return res.returncode, out


# --- Validation -----------------------------------------------------------

def validate_shaper(bandwidth_kbit: int) -> None:
    if bandwidth_kbit < 8 or bandwidth_kbit > 100_000_000:
        raise ValueError("bandwidth_kbit must be between 8 and 100000000")


def validate_class(rate_kbit: int, ceil_kbit: int | None, priority: int) -> None:
    if rate_kbit < 1:
        raise ValueError("rate_kbit must be >= 1")
    if ceil_kbit is not None and ceil_kbit < rate_kbit:
        raise ValueError("ceil_kbit must be >= rate_kbit")
    if not (0 <= priority <= 7):
        raise ValueError("priority must be between 0 and 7")


def validate_rule(protocol: str | None, dst_port: int | None,
                  src_address: str | None, dst_address: str | None,
                  dscp: int | None) -> None:
    if protocol is not None and protocol not in _PROTO_NUM:
        raise ValueError("protocol must be tcp or udp")
    if dst_port is not None and not (1 <= dst_port <= 65535):
        raise ValueError("dst_port must be between 1 and 65535")
    if dscp is not None and not (0 <= dscp <= 63):
        raise ValueError("dscp must be between 0 and 63")
    for addr in (src_address, dst_address):
        if addr:
            try:
                ipaddress.ip_network(addr, strict=False)
            except ValueError:
                raise ValueError(f"invalid address: {addr!r}")


# --- Compilation ----------------------------------------------------------

def compile_interface(spec: dict) -> list[list[str]]:
    """Compile one shaper spec to the ordered list of tc argument vectors.

    `spec` shape (plain dict so it is trivially testable without the DB):
        {
          "ifname": "eth0",
          "bandwidth_kbit": 95000,
          "classes": [
            {"minor": 10, "rate_kbit": 20000, "ceil_kbit": 95000,
             "priority": 0, "is_default": False},
            ...
          ],
          "rules": [
            {"minor": 10, "protocol": "udp", "dst_port": 5060,
             "src_address": None, "dst_address": None, "dscp": 46},
            ...
          ],
        }
    """
    ifname = spec["ifname"]
    if not _VALID_IFNAME.match(ifname):
        raise ValueError(f"invalid interface name: {ifname!r}")
    bw = int(spec["bandwidth_kbit"])
    classes = spec["classes"]
    rules = spec.get("rules", [])

    default_minor = next(
        (c["minor"] for c in classes if c.get("is_default")),
        classes[0]["minor"] if classes else 10,
    )

    cmds: list[list[str]] = []
    base = ["tc", "qdisc", "add", "dev", ifname]
    # Root HTB qdisc, unmatched traffic falls into the default class.
    cmds.append(base + ["root", "handle", "1:", "htb", "default", str(default_minor)])
    # Link root class capped at the full bandwidth.
    cmds.append([
        "tc", "class", "add", "dev", ifname, "parent", "1:", "classid", "1:1",
        "htb", "rate", f"{bw}kbit", "ceil", f"{bw}kbit",
    ])
    for c in classes:
        minor = int(c["minor"])
        rate = int(c["rate_kbit"])
        ceil = int(c["ceil_kbit"]) if c.get("ceil_kbit") else bw
        prio = int(c.get("priority", 3))
        cmds.append([
            "tc", "class", "add", "dev", ifname, "parent", "1:1",
            "classid", f"1:{minor}", "htb",
            "rate", f"{rate}kbit", "ceil", f"{ceil}kbit", "prio", str(prio),
        ])
        # fq_codel leaf: keeps latency low inside each class (bufferbloat).
        cmds.append([
            "tc", "qdisc", "add", "dev", ifname, "parent", f"1:{minor}",
            "handle", f"{minor}:", "fq_codel",
        ])
    # Classifiers. tc filter priority follows rule order: first match wins.
    for prio, r in enumerate(rules, start=1):
        cmds.append(_compile_filter(ifname, prio, r))
    return cmds


def _compile_filter(ifname: str, prio: int, r: dict) -> list[str]:
    minor = int(r["minor"])
    f = [
        "tc", "filter", "add", "dev", ifname, "parent", "1:",
        "protocol", "ip", "prio", str(prio), "u32",
    ]
    matched = False
    if r.get("dscp") is not None:
        # dsfield = DSCP << 2 | ECN ; mask 0xfc isolates the 6 DSCP bits.
        f += ["match", "ip", "dsfield", str(int(r["dscp"]) << 2), "0xfc"]
        matched = True
    if r.get("protocol"):
        f += ["match", "ip", "protocol", str(_PROTO_NUM[r["protocol"]]), "0xff"]
        matched = True
    if r.get("dst_port") is not None:
        f += ["match", "ip", "dport", str(int(r["dst_port"])), "0xffff"]
        matched = True
    if r.get("src_address"):
        f += ["match", "ip", "src", r["src_address"]]
        matched = True
    if r.get("dst_address"):
        f += ["match", "ip", "dst", r["dst_address"]]
        matched = True
    if not matched:
        # Match-all: steers every remaining packet into this class.
        f += ["match", "u32", "0", "0"]
    f += ["flowid", f"1:{minor}"]
    return f


# --- Kernel apply ---------------------------------------------------------

def clear_interface(ifname: str) -> None:
    """Remove the root qdisc on <if> (reverts to the kernel default)."""
    if not _VALID_IFNAME.match(ifname):
        raise ValueError(f"invalid interface name: {ifname!r}")
    # 'del root' fails harmlessly when no custom qdisc is present.
    _run(["tc", "qdisc", "del", "dev", ifname, "root"], check=False)


def apply_interface(spec: dict) -> None:
    """Tear down then rebuild the qdisc tree for one interface."""
    clear_interface(spec["ifname"])
    for cmd in compile_interface(spec):
        rc, out = _run(cmd)
        if rc != 0:
            raise RuntimeError(f"tc command failed: {' '.join(cmd)} -> {out}")


def specs_from_db(db) -> list[dict]:
    """Build the list of enabled shaper specs from the DB."""
    from app import models
    specs: list[dict] = []
    shapers = db.query(models.QosShaper).filter(
        models.QosShaper.enabled.is_(True)
    ).all()
    for sh in shapers:
        if sh.interface is None:
            continue
        classes = sorted(sh.classes, key=lambda c: c.minor)
        if not classes:
            # A shaper with no class would build an empty tree; skip it.
            continue
        rules: list[dict] = []
        for c in classes:
            for r in sorted(c.rules, key=lambda x: x.position):
                if not r.enabled:
                    continue
                rules.append({
                    "minor": c.minor, "protocol": r.protocol,
                    "dst_port": r.dst_port, "src_address": r.src_address,
                    "dst_address": r.dst_address, "dscp": r.dscp,
                })
        specs.append({
            "ifname": sh.interface.name,
            "bandwidth_kbit": sh.bandwidth_kbit,
            "classes": [
                {"minor": c.minor, "rate_kbit": c.rate_kbit,
                 "ceil_kbit": c.ceil_kbit, "priority": c.priority,
                 "is_default": c.is_default}
                for c in classes
            ],
            "rules": rules,
        })
    return specs


def apply_all(db) -> dict:
    """Apply QoS for every shaper, clearing interfaces with none/disabled.

    Returns a small summary used by the API and the boot replay log.
    """
    from app import models
    # Clear every interface that currently has a shaper row but is
    # disabled (or has no class), so toggling a shaper off actually
    # removes the qdisc instead of leaving a stale tree behind.
    all_shapers = db.query(models.QosShaper).all()
    enabled_names = set()
    specs = specs_from_db(db)
    for spec in specs:
        apply_interface(spec)
        enabled_names.add(spec["ifname"])
    cleared = []
    for sh in all_shapers:
        if sh.interface and sh.interface.name not in enabled_names:
            clear_interface(sh.interface.name)
            cleared.append(sh.interface.name)
    return {"applied": sorted(enabled_names), "cleared": sorted(cleared)}
