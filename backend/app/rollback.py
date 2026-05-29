# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Unified rollback manager.

Single source of truth for the commit-confirmed pattern used everywhere
in MurOS:

  1. capture a snapshot of the current state
  2. apply the change
  3. arm a countdown timer
  4. on operator confirm: discard the snapshot
  5. on timeout (or manual rollback): replay the snapshot

Two flavours coexist in the same manager:

* **Ephemeral tickets** carry a Python closure as their
  :attr:`RollbackTicket.rollback_fn`. They die with the process and
  are appropriate for state that disappears with the backend too
  (e.g. an nft apply: if the backend crashes mid-apply, the in-kernel
  ruleset still exists and the operator will just notice on next
  start; an automated rollback after crash is not desirable here).

* **Persistent tickets** are mirrored to the
  :class:`app.models.RollbackTicketRow` table and reference a named
  handler that is registered at module load. After a backend restart,
  :func:`RollbackManager.restore_from_db` recreates the in-memory
  ticket, rearms the timer with the remaining lifetime and replays
  the handler at expiry. This is required for SSH/HTTP/TLS rollback
  (locking out the admin via a bad change is exactly the scenario
  where the backend may also crash and the rollback must still fire).
"""
from __future__ import annotations

import json
import logging
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

log = logging.getLogger("muros.rollback")

DEFAULT_TIMEOUT_SECONDS = 60
GC_RETENTION = timedelta(minutes=5)

TicketState = Literal["pending", "committed", "rolled_back", "rollback_failed"]
TicketKind = Literal[
    "nftables", "interface", "route", "vlan",
    "http", "ssh", "tls",
]

# Handler signature for persistent tickets: takes the deserialised
# detail dict captured at register time, performs the revert, raises
# on failure.
HandlerFn = Callable[[dict], None]


def _utcnow_naive() -> datetime:
    """Naive UTC, matching the rest of the MurOS DB columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass
class RollbackTicket:
    id: str
    kind: TicketKind
    description: str
    rollback_fn: Callable[[], None]
    started_at: datetime
    expires_at: datetime
    timeout_seconds: int
    state: TicketState = "pending"
    message: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)
    # Set when the ticket is mirrored in DB. Empty for ephemeral ones.
    handler_name: str | None = None
    persistent: bool = False

    def to_public(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "description": self.description,
            "started_at": self.started_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "timeout_seconds": self.timeout_seconds,
            "state": self.state,
            "message": self.message,
            "detail": self.detail,
            "persistent": self.persistent,
        }


class RollbackManager:
    """Process-wide registry of pending rollback tickets."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tickets: dict[str, RollbackTicket] = {}
        self._timers: dict[str, threading.Timer] = {}
        self._handlers: dict[str, HandlerFn] = {}

    # -- handler registry ------------------------------------------------

    def register_handler(self, name: str, fn: HandlerFn) -> None:
        """Register a named replay handler for persistent tickets.

        Must be called at module load by each feature that creates
        persistent tickets (http_config, ssh_config, tls, network,
        routing). Re-registration is allowed and silently overrides
        the previous mapping (the test suite relies on this).
        """
        self._handlers[name] = fn
        log.debug("handler registered: %s", name)

    # -- registration ----------------------------------------------------

    def register(
        self,
        kind: TicketKind,
        description: str,
        rollback_fn: Callable[[], None] | None = None,
        timeout: int | None = None,
        detail: dict | None = None,
        exclusive_kind: bool = False,
        persistent: bool = False,
        handler_name: str | None = None,
    ) -> RollbackTicket:
        """Register a new rollback ticket and arm its timer.

        ``rollback_fn`` is the in-process callable that runs at
        expiry or on manual rollback. For persistent tickets,
        ``handler_name`` must be the name of a previously registered
        handler that knows how to revert from ``detail`` alone, so the
        action can also be replayed after a backend restart.

        When ``timeout`` is ``None`` the manager resolves it from the
        ``apply_confirm_timeout`` system setting; this lets the
        operator change the value from the UI without a process
        restart.
        """
        if persistent and not handler_name:
            raise ValueError("persistent tickets require a handler_name")
        if persistent and handler_name not in self._handlers:
            raise ValueError(
                f"unknown rollback handler: {handler_name!r}. "
                f"Call register_handler({handler_name!r}, fn) at module "
                f"load before registering a persistent ticket."
            )
        if rollback_fn is None and not persistent:
            raise ValueError(
                "ephemeral tickets require an explicit rollback_fn"
            )

        if timeout is None:
            try:
                from app import settings as _settings
                timeout = _settings.get_apply_confirm_timeout()
            except Exception:  # noqa: BLE001
                timeout = DEFAULT_TIMEOUT_SECONDS

        with self._lock:
            if exclusive_kind:
                for t in self._tickets.values():
                    if t.state == "pending" and t.kind == kind:
                        raise RuntimeError(
                            f"a {kind} change is already pending, confirm or "
                            f"rollback it first"
                        )
            now = _utcnow_naive()
            tid = uuid.uuid4().hex[:12]
            payload = detail or {}

            if persistent:
                # Wrap the actual replay so the rollback_fn signature
                # remains () -> None for the in-memory side.
                handler = self._handlers[handler_name]
                def _replay(_handler=handler, _payload=payload):
                    _handler(_payload)
                effective_fn: Callable[[], None] = rollback_fn or _replay
            else:
                effective_fn = rollback_fn  # type: ignore[assignment]

            ticket = RollbackTicket(
                id=tid,
                kind=kind,
                description=description,
                rollback_fn=effective_fn,
                started_at=now,
                expires_at=now + timedelta(seconds=timeout),
                timeout_seconds=timeout,
                detail=payload,
                handler_name=handler_name,
                persistent=persistent,
            )
            self._tickets[tid] = ticket

            if persistent:
                self._persist(ticket)

            timer = threading.Timer(timeout, self._auto_rollback, args=[tid])
            timer.daemon = True
            self._timers[tid] = timer
            timer.start()
            log.info(
                "ticket registered: %s (%s, persistent=%s) %ds",
                tid, kind, persistent, timeout,
            )
            return ticket

    # -- queries ---------------------------------------------------------

    def list_pending(self) -> list[dict]:
        with self._lock:
            self._gc_old()
            return [t.to_public() for t in self._tickets.values()]

    def get(self, tid: str) -> RollbackTicket | None:
        return self._tickets.get(tid)

    def active_of_kind(self, kind: TicketKind) -> RollbackTicket | None:
        for t in self._tickets.values():
            if t.state == "pending" and t.kind == kind:
                return t
        return None

    # -- state transitions -----------------------------------------------

    def confirm(self, tid: str) -> RollbackTicket:
        with self._lock:
            ticket = self._tickets.get(tid)
            if not ticket:
                raise KeyError(tid)
            if ticket.state != "pending":
                raise RuntimeError(f"ticket already {ticket.state}")
            timer = self._timers.pop(tid, None)
            if timer:
                timer.cancel()
            ticket.state = "committed"
            ticket.message = "Confirmed by the operator"
            if ticket.persistent:
                self._update_db_state(ticket)
            log.info("ticket confirmed: %s", tid)
            return ticket

    def rollback(self, tid: str, automatic: bool = False) -> RollbackTicket:
        with self._lock:
            ticket = self._tickets.get(tid)
            if not ticket:
                raise KeyError(tid)
            if ticket.state != "pending":
                return ticket
            timer = self._timers.pop(tid, None)
            if timer:
                timer.cancel()
            try:
                ticket.rollback_fn()
                ticket.state = "rolled_back"
                ticket.message = (
                    "Automatic rollback (timeout)" if automatic else "Manual rollback"
                )
                log.info("ticket rolled back: %s (automatic=%s)", tid, automatic)
            except Exception as exc:  # noqa: BLE001
                ticket.state = "rollback_failed"
                ticket.message = f"Rollback failed: {exc}"
                log.exception("rollback failed for %s", tid)
            if ticket.persistent:
                self._update_db_state(ticket)
            return ticket

    # -- persistence -----------------------------------------------------

    def _persist(self, ticket: RollbackTicket) -> None:
        """Mirror a fresh persistent ticket into DB."""
        try:
            from app.db import SessionLocal
            from app import models
            with SessionLocal() as db:
                row = models.RollbackTicketRow(
                    id=ticket.id,
                    kind=ticket.kind,
                    description=ticket.description,
                    handler_name=ticket.handler_name,
                    detail_json=json.dumps(ticket.detail),
                    started_at=ticket.started_at,
                    expires_at=ticket.expires_at,
                    timeout_seconds=ticket.timeout_seconds,
                    state=ticket.state,
                    message=ticket.message,
                )
                db.add(row)
                db.commit()
        except Exception:  # noqa: BLE001
            log.exception("could not persist ticket %s", ticket.id)

    def _update_db_state(self, ticket: RollbackTicket) -> None:
        try:
            from app.db import SessionLocal
            from app import models
            with SessionLocal() as db:
                row = db.get(models.RollbackTicketRow, ticket.id)
                if row is None:
                    return
                row.state = ticket.state
                row.message = ticket.message
                db.commit()
        except Exception:  # noqa: BLE001
            log.exception("could not update DB state for %s", ticket.id)

    def restore_from_db(self) -> int:
        """Reload pending persistent tickets after a backend restart.

        Called from the FastAPI lifespan once handlers have been
        registered (handler registration must come first, otherwise
        replay would fail with KeyError). Tickets whose expiry is
        already in the past trigger an immediate rollback. The
        remaining ones get a new timer armed for the remaining time.

        Returns the number of tickets restored.
        """
        try:
            from app.db import SessionLocal
            from app import models
        except Exception:  # noqa: BLE001
            return 0
        restored = 0
        with SessionLocal() as db:
            rows = (
                db.query(models.RollbackTicketRow)
                .filter(models.RollbackTicketRow.state == "pending")
                .all()
            )
            for row in rows:
                if row.handler_name not in self._handlers:
                    log.warning(
                        "orphan rollback ticket %s: handler %r not "
                        "registered, leaving as pending for manual review",
                        row.id, row.handler_name,
                    )
                    continue
                detail = {}
                try:
                    detail = json.loads(row.detail_json or "{}")
                except Exception:  # noqa: BLE001
                    log.warning("corrupt detail_json on ticket %s", row.id)
                handler = self._handlers[row.handler_name]
                def _replay(_handler=handler, _payload=detail):
                    _handler(_payload)
                ticket = RollbackTicket(
                    id=row.id,
                    kind=row.kind,  # type: ignore[arg-type]
                    description=row.description,
                    rollback_fn=_replay,
                    started_at=row.started_at,
                    expires_at=row.expires_at,
                    timeout_seconds=row.timeout_seconds,
                    state=row.state,  # type: ignore[arg-type]
                    message=row.message,
                    detail=detail,
                    handler_name=row.handler_name,
                    persistent=True,
                )
                with self._lock:
                    self._tickets[ticket.id] = ticket
                remaining = (row.expires_at - _utcnow_naive()).total_seconds()
                if remaining <= 0:
                    log.warning(
                        "ticket %s already expired at restore, rolling back now",
                        row.id,
                    )
                    self.rollback(row.id, automatic=True)
                else:
                    timer = threading.Timer(
                        remaining, self._auto_rollback, args=[row.id],
                    )
                    timer.daemon = True
                    with self._lock:
                        self._timers[row.id] = timer
                    timer.start()
                    log.info(
                        "ticket %s restored, %.1fs remaining", row.id, remaining,
                    )
                restored += 1
        return restored

    # -- internals -------------------------------------------------------

    def _auto_rollback(self, tid: str) -> None:
        try:
            self.rollback(tid, automatic=True)
        except KeyError:
            pass

    def _gc_old(self) -> None:
        cutoff = _utcnow_naive() - GC_RETENTION
        stale = [
            tid for tid, t in self._tickets.items()
            if t.state != "pending" and t.expires_at < cutoff
        ]
        for tid in stale:
            self._tickets.pop(tid, None)


manager = RollbackManager()
