# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Unit tests for the chain/zone match clause of the nftables compiler.

These lock in the invariant the rule form relies on: the firewall itself
is a fixed endpoint on the single-ended chains, so the source interface is
meaningless on `output` and the destination interface is meaningless on
`input`. A zone left over on the wrong side (a stale selection, or a value
crafted through the API) must be ignored rather than compiled into a clause
that can never match. The `forward` chain matches both ends.

Pure tests: they build transient models and call the helper directly, with
no database session and no kernel apply.
"""
from app import models
from app.compiler import _compile_addresses_zones


def _zone(*ifnames):
    """Transient Zone carrying the given interface names."""
    return models.Zone(interfaces=[models.Interface(name=n) for n in ifnames])


def _rule(**kw):
    defaults = dict(src_zone=None, dst_zone=None)
    defaults.update(kw)
    return models.FirewallRule(**defaults)


def test_forward_matches_both_interfaces():
    clause = _compile_addresses_zones(
        _rule(chain="forward", src_zone=_zone("eth0"), dst_zone=_zone("eth1"))
    )
    assert clause == "iifname eth0 oifname eth1"


def test_input_keeps_source_interface_only():
    clause = _compile_addresses_zones(
        _rule(chain="input", src_zone=_zone("eth0"))
    )
    assert clause == "iifname eth0"


def test_output_keeps_destination_interface_only():
    clause = _compile_addresses_zones(
        _rule(chain="output", dst_zone=_zone("eth1"))
    )
    assert clause == "oifname eth1"


def test_input_ignores_stale_destination_zone():
    clause = _compile_addresses_zones(
        _rule(chain="input", src_zone=_zone("eth0"), dst_zone=_zone("eth1"))
    )
    assert clause == "iifname eth0"


def test_output_ignores_stale_source_zone():
    clause = _compile_addresses_zones(
        _rule(chain="output", src_zone=_zone("eth0"), dst_zone=_zone("eth1"))
    )
    assert clause == "oifname eth1"


def test_multi_interface_zone_builds_a_set():
    clause = _compile_addresses_zones(
        _rule(chain="forward", src_zone=_zone("eth0", "eth1"), dst_zone=_zone("eth2"))
    )
    assert clause == "iifname { eth0, eth1 } oifname eth2"


def test_no_zone_yields_empty_clause():
    assert _compile_addresses_zones(_rule(chain="forward")) == ""
