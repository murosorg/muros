# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Tests for basic system identity/locale settings (sysconfig).

Subprocess writes are gated on MUROS_APPLY, so with apply off the setters
only validate input and never touch the host. The choice lists are
monkeypatched to keep the tests deterministic and offline.
"""
import pytest

from app import sysconfig


@pytest.fixture(autouse=True)
def _stub_choices(monkeypatch):
    monkeypatch.setattr(sysconfig, "list_timezones", lambda: ["Europe/Paris", "Etc/UTC"])
    monkeypatch.setattr(sysconfig, "list_locales", lambda: ["en_US.UTF-8", "fr_FR.UTF-8"])
    monkeypatch.setattr(sysconfig, "list_keymaps", lambda: ["us", "fr"])


def test_set_hostname_accepts_valid_label():
    sysconfig.set_hostname("muros-fw1")  # no raise


@pytest.mark.parametrize("bad", ["", "-bad", "bad-", "a.b", "with space", "x" * 64])
def test_set_hostname_rejects_invalid(bad):
    with pytest.raises(ValueError):
        sysconfig.set_hostname(bad)


def test_set_timezone_accepts_known():
    sysconfig.set_timezone("Europe/Paris")


def test_set_timezone_rejects_unknown():
    with pytest.raises(ValueError):
        sysconfig.set_timezone("Mars/Olympus")


def test_set_timezone_rejects_bad_format():
    with pytest.raises(ValueError):
        sysconfig.set_timezone("../etc/passwd")


def test_set_locale_accepts_known():
    sysconfig.set_locale("fr_FR.UTF-8")


def test_set_locale_rejects_unknown():
    with pytest.raises(ValueError):
        sysconfig.set_locale("zz_ZZ.UTF-8")


def test_set_keymap_accepts_known():
    sysconfig.set_keymap("fr")


def test_set_keymap_rejects_unknown():
    with pytest.raises(ValueError):
        sysconfig.set_keymap("azerty123!")


def test_apply_identity_validates_before_applying(monkeypatch):
    calls = []
    monkeypatch.setattr(sysconfig, "_set", lambda args, timeout=10: calls.append(args))
    monkeypatch.setattr(sysconfig, "get_settings", lambda: {"hostname": "x"})
    # bad timezone must raise and hostname (applied first) is the only call
    with pytest.raises(ValueError):
        sysconfig.apply_identity(hostname="gw", timezone="Nope/Nope")
    assert calls == [["hostnamectl", "set-hostname", "gw"]]


def test_apply_identity_skips_none_fields(monkeypatch):
    calls = []
    monkeypatch.setattr(sysconfig, "_set", lambda args, timeout=10: calls.append(args))
    monkeypatch.setattr(sysconfig, "get_settings", lambda: {})
    sysconfig.apply_identity(keymap="fr")
    assert calls == [["localectl", "set-keymap", "fr"]]


def test_hosts_rewrite_replaces_existing_127_0_1_1():
    content = "127.0.0.1\tlocalhost\n127.0.1.1\toldname\n::1\tlocalhost\n"
    out = sysconfig._rewrite_hosts_127_0_1_1(content, "newname")
    assert "127.0.1.1\tnewname" in out
    assert "oldname" not in out
    # other lines are preserved verbatim
    assert "127.0.0.1\tlocalhost" in out
    assert "::1\tlocalhost" in out


def test_hosts_rewrite_appends_when_missing():
    content = "127.0.0.1\tlocalhost\n"
    out = sysconfig._rewrite_hosts_127_0_1_1(content, "gw")
    assert out.rstrip().endswith("127.0.1.1\tgw")
    assert "127.0.0.1\tlocalhost" in out


def test_set_hostname_propagates(monkeypatch):
    # With apply off, _propagate_hostname is a no-op; force the apply path
    # and assert it touches /etc/hosts and regenerates a self-signed cert.
    monkeypatch.setattr(sysconfig, "_APPLY", True)
    monkeypatch.setattr(sysconfig, "_set", lambda args, timeout=10: None)
    written = {}

    class _FakeHosts:
        def exists(self):
            return True

        def read_text(self):
            return "127.0.1.1\told\n"

        def write_text(self, data):
            written["hosts"] = data

    monkeypatch.setattr(sysconfig, "ETC_HOSTS", _FakeHosts())

    regen = {"called": False}
    fake_tls = type("T", (), {
        "get_status": staticmethod(lambda: {"is_self_signed": True}),
        "regenerate_snakeoil": staticmethod(lambda: regen.__setitem__("called", True)),
    })
    monkeypatch.setitem(__import__("sys").modules, "app.tls", fake_tls)

    sysconfig.set_hostname("fw-edge")
    assert "127.0.1.1\tfw-edge" in written["hosts"]
    assert regen["called"] is True


def test_set_hostname_keeps_uploaded_cert(monkeypatch):
    monkeypatch.setattr(sysconfig, "_APPLY", True)
    monkeypatch.setattr(sysconfig, "_set", lambda args, timeout=10: None)
    monkeypatch.setattr(sysconfig, "ETC_HOSTS", type("P", (), {"exists": lambda s: False})())
    regen = {"called": False}
    fake_tls = type("T", (), {
        "get_status": staticmethod(lambda: {"is_self_signed": False}),
        "regenerate_snakeoil": staticmethod(lambda: regen.__setitem__("called", True)),
    })
    monkeypatch.setitem(__import__("sys").modules, "app.tls", fake_tls)
    sysconfig.set_hostname("fw-edge")
    assert regen["called"] is False  # custom cert left untouched
