# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Basic system identity and locale settings.

These OS knobs (hostname, timezone, locale, console keymap) are left at
neutral defaults by the unattended installer (hostname 'muros', UTC,
en_US.UTF-8, 'us' keymap). They are not firewall configuration, but an
operator who logs into a shell or the physical console usually wants to
set them. MurOS drives them through systemd's standard tools so each
change is applied live AND persisted across reboots:

    hostnamectl set-hostname <name>
    timedatectl set-timezone <area/location>
    localectl   set-locale LANG=<locale>
    localectl   set-keymap <keymap>

Writes are gated on MUROS_APPLY: in dev / tests there is no systemd (or
no privileges), so the subprocess calls are skipped while input
validation still runs. Reads are best-effort and degrade to empty
strings when the tools are unavailable.
"""
from __future__ import annotations

import os
import re
import subprocess

_APPLY = os.environ.get("MUROS_APPLY", "false").lower() in ("1", "true", "yes")

# RFC 1123 host label: letters/digits/hyphen, no leading/trailing hyphen,
# 1-63 chars. We keep the management hostname to a single label (no dots).
_HOSTNAME_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
_TIMEZONE_RE = re.compile(r"^[A-Za-z0-9]+(?:[/_+-][A-Za-z0-9]+)*$")
_LOCALE_RE = re.compile(r"^(C|POSIX)(\.[A-Za-z0-9-]+)?$|^[a-z]{2,3}_[A-Z]{2}(@[A-Za-z]+)?(\.[A-Za-z0-9-]+)?$")
_KEYMAP_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _run(args: list[str], timeout: int = 5) -> str:
    """Run a command and return stdout, or '' on any failure."""
    try:
        return subprocess.check_output(
            args, text=True, timeout=timeout, stderr=subprocess.DEVNULL,
        )
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return ""


def _set(args: list[str], timeout: int = 10) -> None:
    """Run a setter command, no-op when apply is disabled (dev/tests)."""
    if not _APPLY:
        return
    try:
        subprocess.check_call(args, timeout=timeout)
    except (subprocess.SubprocessError, FileNotFoundError, OSError) as exc:
        raise RuntimeError(f"command failed: {' '.join(args)}: {exc}") from exc


# --- Reads ---------------------------------------------------------------

def get_hostname() -> str:
    out = _run(["hostnamectl", "--static"]).strip()
    if out:
        return out
    try:
        import socket

        return socket.gethostname()
    except OSError:
        return ""


def get_timezone() -> str:
    return _run(["timedatectl", "show", "-p", "Timezone", "--value"]).strip()


def _localectl_field(label: str) -> str:
    """Extract a field (e.g. 'System Locale', 'VC Keymap') from localectl."""
    for line in _run(["localectl", "status"]).splitlines():
        stripped = line.strip()
        if stripped.startswith(label):
            return stripped.split(":", 1)[1].strip()
    return ""


def get_locale() -> str:
    # "System Locale: LANG=en_US.UTF-8" -> en_US.UTF-8
    raw = _localectl_field("System Locale")
    if not raw or raw.lower().startswith("n/a"):
        return ""
    for token in raw.split():
        if token.startswith("LANG="):
            return token.split("=", 1)[1]
    return raw


def get_keymap() -> str:
    # "VC Keymap: us"
    raw = _localectl_field("VC Keymap")
    if not raw or raw.lower().startswith("n/a"):
        return ""
    return raw


def get_settings() -> dict:
    return {
        "hostname": get_hostname(),
        "timezone": get_timezone(),
        "locale": get_locale(),
        "keymap": get_keymap(),
    }


# --- Choice lists --------------------------------------------------------

def list_timezones() -> list[str]:
    return [t.strip() for t in _run(["timedatectl", "list-timezones"]).splitlines() if t.strip()]


def list_locales() -> list[str]:
    return [t.strip() for t in _run(["localectl", "list-locales"]).splitlines() if t.strip()]


def list_keymaps() -> list[str]:
    return [t.strip() for t in _run(["localectl", "list-keymaps"]).splitlines() if t.strip()]


# --- Writes (validated) --------------------------------------------------

def set_hostname(name: str) -> None:
    name = (name or "").strip()
    if not _HOSTNAME_RE.match(name):
        raise ValueError(
            "hostname must be a single RFC 1123 label (letters, digits, "
            "hyphens; no leading/trailing hyphen; max 63 chars)"
        )
    _set(["hostnamectl", "set-hostname", name])


def set_timezone(tz: str) -> None:
    tz = (tz or "").strip()
    if not _TIMEZONE_RE.match(tz):
        raise ValueError("invalid timezone format")
    known = list_timezones()
    if known and tz not in known:
        raise ValueError(f"unknown timezone: {tz}")
    _set(["timedatectl", "set-timezone", tz])


def set_locale(locale: str) -> None:
    locale = (locale or "").strip()
    if not _LOCALE_RE.match(locale):
        raise ValueError("invalid locale format (e.g. en_US.UTF-8)")
    known = list_locales()
    if known and locale not in known:
        raise ValueError(f"unknown locale: {locale}")
    _set(["localectl", "set-locale", f"LANG={locale}"])


def set_keymap(keymap: str) -> None:
    keymap = (keymap or "").strip()
    if not _KEYMAP_RE.match(keymap):
        raise ValueError("invalid keymap format")
    known = list_keymaps()
    if known and keymap not in known:
        raise ValueError(f"unknown keymap: {keymap}")
    _set(["localectl", "set-keymap", keymap])


def apply_identity(
    hostname: str | None = None,
    timezone: str | None = None,
    locale: str | None = None,
    keymap: str | None = None,
) -> dict:
    """Apply each provided field (None = leave unchanged). Returns settings.

    Validation happens before any command runs for that field, so an
    invalid value raises ValueError and leaves the rest untouched.
    """
    if hostname is not None:
        set_hostname(hostname)
    if timezone is not None:
        set_timezone(timezone)
    if locale is not None:
        set_locale(locale)
    if keymap is not None:
        set_keymap(keymap)
    return get_settings()
