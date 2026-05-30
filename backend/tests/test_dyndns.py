# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Tests for the dynamic DNS update URL builder and response classifier.

Pure: no network. Exercises build_update (dyndns2 query + Basic auth,
custom URL placeholder substitution) and classify_response (dyndns2 /
DuckDNS / HTTP fallback), so it runs in the backend pytest CI on push.
"""
from __future__ import annotations

import base64
from types import SimpleNamespace

from app import dyndns


def _entry(**over):
    base = dict(provider="noip", server="dynupdate.no-ip.com", hostname="vpn.example.com",
                username="user", password="secret", custom_url=None)
    base.update(over)
    return SimpleNamespace(**base)


def test_build_dyndns2_url_and_auth():
    url, headers = dyndns.build_update(_entry(), "203.0.113.7")
    assert url == ("https://dynupdate.no-ip.com/nic/update"
                   "?hostname=vpn.example.com&myip=203.0.113.7")
    expected = "Basic " + base64.b64encode(b"user:secret").decode()
    assert headers["Authorization"] == expected


def test_build_dyndns2_strips_trailing_slash():
    url, _ = dyndns.build_update(_entry(server="www.ovh.com/"), "1.2.3.4")
    assert url.startswith("https://www.ovh.com/nic/update?")


def test_build_custom_url_substitution():
    e = _entry(provider="custom", custom_url="https://duckdns.org/update?domains={hostname}&ip={ip}",
               hostname="home")
    url, headers = dyndns.build_update(e, "9.9.9.9")
    assert url == "https://duckdns.org/update?domains=home&ip=9.9.9.9"
    assert headers == {}


def test_classify_good():
    assert dyndns.classify_response(200, "good 203.0.113.7") == ("good", None)


def test_classify_nochg():
    assert dyndns.classify_response(200, "nochg 203.0.113.7") == ("nochg", None)


def test_classify_duckdns_ok():
    assert dyndns.classify_response(200, "OK") == ("good", None)


def test_classify_badauth():
    status, err = dyndns.classify_response(200, "badauth")
    assert status == "error" and err


def test_classify_connection_failure():
    status, err = dyndns.classify_response(0, "timed out")
    assert status == "error" and "timed out" in err


def test_classify_http_5xx():
    status, err = dyndns.classify_response(500, "boom")
    assert status == "error" and "500" in err
