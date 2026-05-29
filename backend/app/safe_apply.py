# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Backwards-compatibility shim for the unified rollback manager.

Historically MurOS had two parallel implementations of the commit-
confirmed pattern: ``app.apply`` (nftables) and ``app.safe_apply``
(interface IP, route, VLAN). They have been merged into
:mod:`app.rollback`. This module is preserved so existing imports
keep working until callers migrate.

New code should import directly from ``app.rollback``.
"""
from __future__ import annotations

from app.rollback import (
    DEFAULT_TIMEOUT_SECONDS,
    RollbackManager as PendingManager,
    RollbackTicket as PendingChange,
    TicketKind as PendingKind,
    TicketState as PendingState,
    manager,
)

__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "PendingChange",
    "PendingKind",
    "PendingManager",
    "PendingState",
    "manager",
]
