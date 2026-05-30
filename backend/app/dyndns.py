# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Dynamic DNS client: keep a hostname pointed at the firewall public IP.

Many SMBs sit on a WAN with a dynamic public IP. This module keeps one or
more DNS hostnames in sync with the current egress IP, so remote access
(VPN endpoint, published service) keeps resolving after the ISP rotates
the address.

Two update modes, both plain HTTPS GET (no extra package):
  - dyndns2: the de-facto standard used by No-IP, DynDNS, Dynu, OVH, ...
    GET https://<server>/nic/update?hostname=H&myip=IP with HTTP Basic
    auth. Response starts with 'good' / 'nochg' on success.
  - custom: the provider gives a ready-made update URL (DuckDNS, etc.);
    we substitute {ip} and {hostname} placeholders and GET it.

The public IP is discovered from outside via a small set of echo
endpoints (what the world actually sees), which is more correct than the
WAN interface IP when the firewall is behind ISP CGNAT/PPPoE.

A daemon-less background thread (mirrors the updates checker) refreshes
every N minutes and on demand; there is no on-disk config file and thus
no systemd unit to manage.
"""
from __future__ import annotations

import base64
import ipaddress
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urlparse

log = logging.getLogger("muros.dyndns")

# Public-IP echo endpoints, tried in order until one returns a valid IPv4.
_IP_ECHO_URLS = (
    "https://api.ipify.org",
    "https://ipv4.icanhazip.com",
    "https://ifconfig.me/ip",
)

# dyndns2 server presets surfaced in the UI. 'custom' uses custom_url.
PROVIDER_PRESETS = {
    "noip":   {"label": "No-IP",   "server": "dynupdate.no-ip.com", "mode": "dyndns2"},
    "dyndns": {"label": "DynDNS",  "server": "members.dyndns.org",  "mode": "dyndns2"},
    "dynu":   {"label": "Dynu",    "server": "api.dynu.com",        "mode": "dyndns2"},
    "ovh":    {"label": "OVH",     "server": "www.ovh.com",         "mode": "dyndns2"},
    "custom": {"label": "Custom URL", "server": "",                "mode": "custom"},
}

_USER_AGENT = "MurOS-dyndns/1.0"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _http_get(url: str, headers: dict | None = None, timeout: int = 10) -> tuple[int, str]:
    """GET a URL. Returns (status_code, body). Never raises on HTTP errors.

    Only http/https schemes are allowed. The custom-provider URL is
    operator-supplied, so this stops urllib from honouring file:// (local
    file disclosure) or other handlers (ftp://, gopher://, ...).
    """
    scheme = urlparse(url).scheme.lower()
    if scheme not in ("http", "https"):
        return 0, f"refused URL scheme '{scheme or 'none'}' (only http/https allowed)"
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (https only)
            return resp.status, resp.read().decode("utf-8", "replace").strip()
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace").strip()
        except Exception:  # noqa: BLE001
            pass
        return e.code, body
    except (urllib.error.URLError, OSError, ValueError) as e:
        return 0, str(e)


def detect_public_ip() -> str | None:
    """Return the current public IPv4 as seen from the internet, or None."""
    for url in _IP_ECHO_URLS:
        status, body = _http_get(url, timeout=6)
        if status == 200 and body:
            candidate = body.split()[0].strip()
            try:
                ip = ipaddress.ip_address(candidate)
                if ip.version == 4:
                    return str(ip)
            except ValueError:
                continue
    return None


def build_update(entry, ip: str) -> tuple[str, dict]:
    """Build the (url, headers) for one update. Pure, so it is unit-tested.

    dyndns2: hostname/myip query + HTTP Basic auth header.
    custom : substitute {ip} / {hostname} in the operator-provided URL.
    """
    if entry.provider == "custom":
        url = (entry.custom_url or "").replace("{ip}", ip).replace(
            "{hostname}", entry.hostname or "")
        return url, {}
    server = (entry.server or "").strip().rstrip("/")
    url = f"https://{server}/nic/update?hostname={entry.hostname}&myip={ip}"
    headers = {}
    if entry.username:
        token = base64.b64encode(
            f"{entry.username}:{entry.password or ''}".encode()
        ).decode()
        headers["Authorization"] = f"Basic {token}"
    return url, headers


def classify_response(status: int, body: str) -> tuple[str, str | None]:
    """Map an update response to (status, error). Pure / unit-tested.

    Returns status in {good, nochg, error}. dyndns2 returns a text code
    ('good', 'nochg', 'nohost', 'badauth', 'abuse', '911', ...); custom
    providers (DuckDNS 'OK'/'KO') are handled too, with a generic 2xx
    fallback.
    """
    low = (body or "").lower()
    if status == 0:
        return "error", body or "connection failed"
    if low.startswith("good"):
        return "good", None
    if low.startswith("nochg"):
        return "nochg", None
    if low.startswith("ok"):  # DuckDNS
        return "good", None
    for bad in ("badauth", "nohost", "notfqdn", "abuse", "!donator", "911", "dnserr", "ko"):
        if bad in low:
            return "error", body or bad
    if 200 <= status < 300:
        return "good", None
    return "error", f"HTTP {status}: {body[:120]}"


def update_entry(db, entry, ip: str) -> dict:
    """Push one update and persist the outcome on the entry row."""
    url, headers = build_update(entry, ip)
    if not url:
        entry.last_status = "error"
        entry.last_error = "empty update URL (check provider/custom_url)"
        db.commit()
        return {"hostname": entry.hostname, "status": "error", "error": entry.last_error}
    status, body = _http_get(url, headers=headers)
    result, err = classify_response(status, body)
    entry.last_status = result
    entry.last_error = err
    if result in ("good", "nochg"):
        entry.last_ip = ip
        entry.last_update_at = _utcnow()
    db.commit()
    log.info("DynDNS %s -> %s (%s)", entry.hostname, result, err or ip)
    return {"hostname": entry.hostname, "status": result, "ip": ip, "error": err}


def run_updates(db, force: bool = False) -> dict:
    """Refresh every enabled entry whose IP changed (or all when forced)."""
    from app import models
    entries = db.query(models.DynDnsEntry).filter(
        models.DynDnsEntry.enabled.is_(True)
    ).all()
    if not entries:
        return {"ip": None, "results": [], "reason": "no enabled entry"}
    ip = detect_public_ip()
    if not ip:
        return {"ip": None, "results": [], "reason": "public IP detection failed"}
    results = []
    for e in entries:
        if not force and e.last_ip == ip and e.last_status in ("good", "nochg"):
            results.append({"hostname": e.hostname, "status": "nochg", "ip": ip})
            continue
        results.append(update_entry(db, e, ip))
    return {"ip": ip, "results": results}


# --- Background scheduler (daemon-less, mirrors updates checker) ---

_thread = None  # threading.Thread | None
_thread_lock = threading.Lock()


def _loop(interval_seconds: int, initial_delay: int) -> None:
    from app.db import SessionLocal
    time.sleep(initial_delay)
    while True:
        try:
            with SessionLocal() as db:
                res = run_updates(db, force=False)
            if res.get("results"):
                log.info("DynDNS cycle: ip=%s, %d entr(y/ies)",
                         res.get("ip"), len(res["results"]))
        except Exception:
            log.exception("DynDNS periodic update failed")
        time.sleep(interval_seconds)


def ensure_scheduler_started() -> None:
    """Start the periodic DynDNS thread if not running. Idempotent."""
    global _thread
    with _thread_lock:
        if _thread is not None and _thread.is_alive():
            return
        try:
            interval_min = float(os.environ.get("MUROS_DYNDNS_INTERVAL_MIN", "5"))
            initial_delay = int(os.environ.get("MUROS_DYNDNS_INITIAL_DELAY_SEC", "30"))
        except (TypeError, ValueError):
            interval_min, initial_delay = 5.0, 30
        interval_s = max(30, int(interval_min * 60))
        _thread = threading.Thread(
            target=_loop, args=(interval_s, initial_delay),
            name="muros-dyndns", daemon=True,
        )
        _thread.start()
        log.info("DynDNS scheduler started (interval=%.1fmin, first run in %ds)",
                 interval_min, initial_delay)
