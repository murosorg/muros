# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Tests for ipsec.start_service / stop_service helpers.

These helpers wrap systemctl start/stop and try both possible unit
names (strongswan-starter on Debian 12+, strongswan on Debian 11). The
tests stub subprocess.run + the systemctl path probe so they pass on
hosts without strongswan installed.
"""
from __future__ import annotations

import pytest


class _FakeProc:
    def __init__(self, rc: int, stdout: str = "", stderr: str = ""):
        self.returncode = rc
        self.stdout = stdout
        self.stderr = stderr


def _patch_systemctl(monkeypatch, runner):
    """Pretend systemctl is in PATH, route subprocess.run to `runner`."""
    from app import ipsec
    monkeypatch.setattr(ipsec, "_which", lambda _cmd: True)
    monkeypatch.setattr(ipsec.subprocess, "run", runner)


def test_start_service_first_unit_succeeds(monkeypatch):
    from app import ipsec
    calls: list[list[str]] = []

    def fake_run(argv, **_kw):
        calls.append(argv)
        return _FakeProc(0)

    _patch_systemctl(monkeypatch, fake_run)
    r = ipsec.start_service()
    assert r["service"] == "strongswan-starter"
    assert "started" in r["message"]
    assert calls == [["systemctl", "start", "strongswan-starter"]]


def test_start_service_falls_back_to_second_unit(monkeypatch):
    from app import ipsec

    def fake_run(argv, **_kw):
        if argv[-1] == "strongswan-starter":
            return _FakeProc(5, stderr="Unit strongswan-starter.service not found.")
        return _FakeProc(0)

    _patch_systemctl(monkeypatch, fake_run)
    r = ipsec.start_service()
    assert r["service"] == "strongswan"


def test_start_service_raises_when_no_unit_responds(monkeypatch):
    from app import ipsec

    def fake_run(_argv, **_kw):
        return _FakeProc(5, stderr="boom")

    _patch_systemctl(monkeypatch, fake_run)
    with pytest.raises(RuntimeError, match="Could not start strongswan"):
        ipsec.start_service()


def test_start_service_raises_when_systemctl_missing(monkeypatch):
    from app import ipsec
    monkeypatch.setattr(ipsec, "_which", lambda _cmd: False)
    with pytest.raises(RuntimeError, match="systemctl is not available"):
        ipsec.start_service()


def test_stop_service_stops_every_known_unit(monkeypatch):
    from app import ipsec
    calls: list[list[str]] = []

    def fake_run(argv, **_kw):
        calls.append(argv)
        return _FakeProc(0)

    _patch_systemctl(monkeypatch, fake_run)
    r = ipsec.stop_service()
    assert "strongswan" in r["message"]
    assert any(a[-1] == "strongswan" for a in calls)
    assert any(a[-1] == "strongswan-starter" for a in calls)


def test_stop_service_raises_when_no_unit_responds(monkeypatch):
    from app import ipsec
    monkeypatch.setattr(ipsec, "_which", lambda _cmd: True)
    monkeypatch.setattr(
        ipsec.subprocess, "run",
        lambda *_a, **_kw: _FakeProc(5, stderr="nope"),
    )
    with pytest.raises(RuntimeError, match="Could not stop strongswan"):
        ipsec.stop_service()
