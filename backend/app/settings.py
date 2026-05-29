# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Typed accessors for the ``system_settings`` key/value table.

Kept intentionally small: cross-cutting knobs that do not deserve
their own config table go here. Feature-specific settings stay in
their dedicated tables (HttpConfig, SshConfig, DhcpConfig, ...).
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Final

from app.db import SessionLocal
from app import models

log = logging.getLogger("muros.settings")

# Allowed values for the apply confirmation timeout, in seconds. The
# UI exposes exactly this list so we do not end up with weird values
# in DB (e.g. a 1s timeout that defeats the whole point).
APPLY_CONFIRM_TIMEOUT_CHOICES: Final[tuple[int, ...]] = (10, 30, 60, 120, 300)
APPLY_CONFIRM_TIMEOUT_DEFAULT: Final[int] = 60
APPLY_CONFIRM_TIMEOUT_KEY: Final[str] = "apply_confirm_timeout"


def _get(key: str) -> str | None:
    with SessionLocal() as db:
        row = db.get(models.SystemSetting, key)
        return row.value if row else None


def _set(key: str, value: str) -> None:
    with SessionLocal() as db:
        row = db.get(models.SystemSetting, key)
        if row is None:
            row = models.SystemSetting(key=key, value=value)
            db.add(row)
        else:
            row.value = value
            row.updated_at = datetime.utcnow()
        db.commit()


def get_apply_confirm_timeout() -> int:
    """Return the configured apply confirmation timeout, in seconds.

    Falls back to :data:`APPLY_CONFIRM_TIMEOUT_DEFAULT` when the row
    is missing, when the stored value is not a number, or when it
    does not belong to :data:`APPLY_CONFIRM_TIMEOUT_CHOICES`. The
    fallback is defensive: a corrupted setting must never lock the
    rollback timer at 0s (instant rollback) or at some absurd value.
    """
    try:
        raw = _get(APPLY_CONFIRM_TIMEOUT_KEY)
    except Exception as exc:  # noqa: BLE001
        # The DB may not be reachable when this is called very early
        # at process start. In that case use the hard-coded default.
        log.warning("could not read apply_confirm_timeout: %s", exc)
        return APPLY_CONFIRM_TIMEOUT_DEFAULT
    if raw is None:
        return APPLY_CONFIRM_TIMEOUT_DEFAULT
    try:
        value = int(raw)
    except ValueError:
        return APPLY_CONFIRM_TIMEOUT_DEFAULT
    if value not in APPLY_CONFIRM_TIMEOUT_CHOICES:
        return APPLY_CONFIRM_TIMEOUT_DEFAULT
    return value


def set_apply_confirm_timeout(value: int) -> int:
    """Persist the apply confirmation timeout. Raises on invalid input."""
    if value not in APPLY_CONFIRM_TIMEOUT_CHOICES:
        raise ValueError(
            f"apply_confirm_timeout must be one of {APPLY_CONFIRM_TIMEOUT_CHOICES}, "
            f"got {value}"
        )
    _set(APPLY_CONFIRM_TIMEOUT_KEY, str(value))
    return value
