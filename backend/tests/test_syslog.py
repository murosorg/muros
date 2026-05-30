# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Tests for the remote syslog forwarding config rendering and validation.

Pure (MUROS_APPLY=0): exercises the rsyslog omfwd drop-in generation and
the input validation without touching systemd, so it runs in the backend
pytest CI job on every push.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import syslog_fwd


def _cfg(**over):
    base = dict(enabled=True, host="10.0.0.5", port=514, protocol="udp",
                format="rfc5424")
    base.update(over)
    return SimpleNamespace(**base)


def test_render_udp_rfc5424():
    conf = syslog_fwd.render_conf(_cfg())
    assert 'target="10.0.0.5"' in conf
    assert 'port="514"' in conf
    assert 'protocol="udp"' in conf
    assert 'template="RSYSLOG_SyslogProtocol23Format"' in conf
    assert 'action.resumeRetryCount="-1"' in conf


def test_render_tcp_rfc3164():
    conf = syslog_fwd.render_conf(_cfg(protocol="tcp", format="rfc3164", port=601))
    assert 'protocol="tcp"' in conf
    assert 'port="601"' in conf
    assert 'template="RSYSLOG_TraditionalForwardFormat"' in conf


def test_validate_rejects_empty_host():
    with pytest.raises(ValueError):
        syslog_fwd.validate_config("", 514, "udp", "rfc5424")


def test_validate_rejects_bad_protocol():
    with pytest.raises(ValueError):
        syslog_fwd.validate_config("10.0.0.5", 514, "sctp", "rfc5424")


def test_validate_rejects_bad_format():
    with pytest.raises(ValueError):
        syslog_fwd.validate_config("10.0.0.5", 514, "udp", "json")


def test_validate_rejects_bad_port():
    with pytest.raises(ValueError):
        syslog_fwd.validate_config("10.0.0.5", 0, "udp", "rfc5424")


def test_validate_accepts_hostname():
    syslog_fwd.validate_config("siem.corp.example", 6514, "tcp", "rfc5424")
