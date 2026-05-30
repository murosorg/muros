# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Deferred apply for HTTP / SSH / TLS / interface / route changes.

This module owns the persistence layer (the ``pending_apply`` DB table
that has been around since rc1) but delegates the timer and the
rollback dispatch to the unified :mod:`app.rollback` manager. The
watcher thread that used to live here is gone: the manager runs a
single ``threading.Timer`` per ticket, which is rearmed at startup by
:func:`restore_pending_on_startup`.

The public API (``create_pending``, ``confirm``, ``rollback_now``,
``list_pending``) is preserved with the same shape so existing routes
(tls_ssh, diag_http, system_ops) do not have to change. Tickets are
still addressed by their integer DB id.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from app.db import SessionLocal
from app import models, rollback

log = logging.getLogger("muros.pending_apply")

# Kept as a module-level constant for backwards compatibility with
# tests that import it. The unified manager (and the system setting it
# resolves at runtime) is the actual source of truth at register time.
DEFAULT_TIMEOUT_SECONDS = 60

_APPLY_TYPES = ("http", "ssh", "tls", "interface", "route")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# --- replay handlers ---
#
# These functions perform the actual revert when a ticket expires or
# is rolled back manually. They take a deserialised ``detail`` dict
# (as captured at register time) and apply the old config back. They
# are registered with the rollback manager at module import so
# :func:`RollbackManager.restore_from_db` can replay them after a
# backend restart.

def _handler_http(detail: dict) -> None:
    from app import nginx_config
    with SessionLocal() as db:
        cfg = db.get(models.HttpConfig, 1)
        if not cfg:
            return
        for field_name, value in detail.items():
            if hasattr(cfg, field_name):
                setattr(cfg, field_name, value)
        db.commit()
        nginx_config.apply_config(cfg)


def _handler_ssh(detail: dict) -> None:
    from app import ssh_config
    with SessionLocal() as db:
        cfg = db.get(models.SshConfig, 1)
        if not cfg:
            return
        for field_name, value in detail.items():
            if hasattr(cfg, field_name):
                setattr(cfg, field_name, value)
        db.commit()
        ssh_config.apply_config(cfg)


def _handler_tls(detail: dict) -> None:
    from app import tls
    cert_pem = detail.get("cert_pem", "")
    key_pem = detail.get("key_pem", "")
    if cert_pem and key_pem:
        tls.upload_cert(cert_pem, key_pem)


def _handler_interface(detail: dict) -> None:
    from app import network
    iface_id = detail.get("interface_id")
    if iface_id is None:
        return
    with SessionLocal() as db:
        iface = db.get(models.Interface, iface_id)
        if iface is None:
            return
        for field_name, value in detail.get("fields", {}).items():
            if hasattr(iface, field_name):
                setattr(iface, field_name, value)
        db.commit()
        network.apply_interface_config(iface)


def _handler_route(detail: dict) -> None:
    from app import routing
    route_id = detail.get("route_id")
    if route_id is None:
        return
    with SessionLocal() as db:
        route = db.get(models.StaticRoute, route_id)
        if route is None:
            return
        for field_name, value in detail.get("fields", {}).items():
            if hasattr(route, field_name):
                setattr(route, field_name, value)
        db.commit()
        routing.apply_route(route, "add")


_HANDLERS = {
    "http": _handler_http,
    "ssh": _handler_ssh,
    "tls": _handler_tls,
    "interface": _handler_interface,
    "route": _handler_route,
}


def _register_handlers_once() -> None:
    for name, fn in _HANDLERS.items():
        rollback.manager.register_handler(name, fn)


# Register handlers as soon as this module is imported so the unified
# manager can replay pending tickets even before pending_apply is
# explicitly touched by the API.
_register_handlers_once()


# --- Public API ---

def create_pending(
    apply_type: str,
    old_config: dict,
    new_config_summary: str | None = None,
    timeout_seconds: int | None = None,
) -> models.PendingApply:
    """Create a pending_apply row and arm its rollback timer.

    The DB row is persisted so its id can be returned to the client
    and used for the confirm/rollback endpoints. The actual revert
    logic is dispatched through the unified rollback manager (one
    timer per ticket) which calls back into
    :func:`_handler_<apply_type>` at expiry.
    """
    if apply_type not in _APPLY_TYPES:
        raise ValueError(f"unknown apply_type: {apply_type}")

    with SessionLocal() as db:
        # Drop any prior pending row for the same apply_type: stacking
        # rollbacks is never what we want; the freshest snapshot wins.
        db.query(models.PendingApply).filter(
            models.PendingApply.apply_type == apply_type,
            models.PendingApply.status == "pending",
        ).update({"status": "replaced"}, synchronize_session=False)
        now = _utcnow()
        # The exact timeout used for the DB row mirrors what the
        # manager will use, so the UI countdown matches reality.
        effective_timeout = timeout_seconds
        if effective_timeout is None:
            try:
                from app import settings as _settings
                effective_timeout = _settings.get_apply_confirm_timeout()
            except Exception:  # noqa: BLE001
                effective_timeout = DEFAULT_TIMEOUT_SECONDS
        entry = models.PendingApply(
            apply_type=apply_type,
            created_at=now,
            expires_at=now + timedelta(seconds=effective_timeout),
            timeout_seconds=effective_timeout,
            old_config_json=json.dumps(old_config),
            new_config_summary=new_config_summary,
            status="pending",
        )
        db.add(entry)
        db.commit()
        db.refresh(entry)
        db.expunge(entry)

    _arm_manager_ticket(entry.id, apply_type, old_config,
                        new_config_summary or f"{apply_type} apply",
                        effective_timeout)
    return entry


def confirm(pending_id: int) -> models.PendingApply:
    """Mark the row confirmed and cancel the rollback timer."""
    with SessionLocal() as db:
        entry = db.get(models.PendingApply, pending_id)
        if entry is None:
            raise ValueError(f"PendingApply {pending_id} not found.")
        if entry.status != "pending":
            raise ValueError(f"PendingApply {pending_id} already {entry.status}.")
        entry.status = "confirmed"
        entry.confirmed_at = _utcnow()
        db.commit()
        db.refresh(entry)
        db.expunge(entry)

    _cancel_manager_ticket(pending_id)
    return entry


def rollback_now(pending_id: int) -> models.PendingApply:
    """Trigger the rollback right now (manual cancel)."""
    with SessionLocal() as db:
        entry = db.get(models.PendingApply, pending_id)
        if entry is None:
            raise ValueError(f"PendingApply {pending_id} not found.")
        if entry.status != "pending":
            raise ValueError(f"PendingApply {pending_id} already {entry.status}.")

    # Going through the manager guarantees the rollback path is the
    # same whether it is triggered manually or by the timer.
    triggered = _trigger_manager_ticket(pending_id, automatic=False)
    if not triggered:
        # Fallback : the manager has no record (process restarted
        # without restore_pending_on_startup having run yet, or
        # someone wiped the in-memory state). Run the handler
        # directly so the operator gets an answer.
        with SessionLocal() as db:
            entry = db.get(models.PendingApply, pending_id)
            if entry is not None:
                _do_rollback_direct(db, entry)

    with SessionLocal() as db:
        entry = db.get(models.PendingApply, pending_id)
        if entry is None:
            raise ValueError(f"PendingApply {pending_id} not found.")
        db.expunge(entry)
        return entry


def list_pending() -> list[models.PendingApply]:
    """Return up to 50 most recent pending_apply rows (any status)."""
    with SessionLocal() as db:
        entries = (
            db.query(models.PendingApply)
            .order_by(models.PendingApply.id.desc())
            .limit(50)
            .all()
        )
        for e in entries:
            db.expunge(e)
        return entries


# --- helpers wired with the unified manager ---

def _arm_manager_ticket(
    pending_id: int,
    apply_type: str,
    old_config: dict,
    description: str,
    timeout: int,
) -> None:
    """Register the ticket with the unified manager.

    We use an explicit ``rollback_fn`` (rather than the named-handler
    path) because the rollback must also flip the PendingApply DB row
    to its final state; doing both in one closure keeps the two stores
    in sync.
    """
    def _rollback_fn(eid: int = pending_id) -> None:
        with SessionLocal() as db:
            row = db.get(models.PendingApply, eid)
            if row is None or row.status != "pending":
                return
            _do_rollback_direct(db, row)

    rollback.manager.register(
        kind=apply_type,  # type: ignore[arg-type]
        description=description,
        rollback_fn=_rollback_fn,
        timeout=timeout,
        detail={"pending_apply_id": pending_id, **old_config},
    )


def _find_manager_ticket(pending_id: int):
    for ticket in list(rollback.manager._tickets.values()):  # noqa: SLF001
        if ticket.detail.get("pending_apply_id") == pending_id and ticket.state == "pending":
            return ticket
    return None


def _cancel_manager_ticket(pending_id: int) -> None:
    ticket = _find_manager_ticket(pending_id)
    if ticket is not None:
        try:
            rollback.manager.confirm(ticket.id)
        except (KeyError, RuntimeError):
            pass


def _trigger_manager_ticket(pending_id: int, automatic: bool) -> bool:
    ticket = _find_manager_ticket(pending_id)
    if ticket is None:
        return False
    rollback.manager.rollback(ticket.id, automatic=automatic)
    return True


def _do_rollback_direct(db, entry: models.PendingApply) -> None:
    """Replay the handler and flip the PendingApply row.

    This is the single point of failure handling, used by both the
    in-process timer path and the fallback path in :func:`rollback_now`
    when the manager does not have the ticket in memory.
    """
    try:
        detail = json.loads(entry.old_config_json or "{}")
        handler = _HANDLERS.get(entry.apply_type)
        if handler is None:
            raise RuntimeError(f"no handler for {entry.apply_type!r}")
        handler(detail)
        entry.status = "rolled_back"
        entry.rolled_back_at = _utcnow()
    except Exception as exc:  # noqa: BLE001
        log.error("Rollback %s/%s failed: %s", entry.apply_type, entry.id, exc)
        entry.status = "rollback_failed"
        entry.rolled_back_at = _utcnow()
        entry.rollback_error = str(exc)[:500]
    db.commit()


def restore_pending_on_startup() -> int:
    """Rearm rollback timers for pending_apply rows after a backend restart.

    Replaces the old polling watcher thread. Called from the FastAPI
    lifespan. Rows whose expiry is in the past are rolled back
    immediately; the others get a fresh timer with the remaining
    lifetime.

    Returns the number of rows that got a timer rearmed.
    """
    restored = 0
    with SessionLocal() as db:
        rows = (
            db.query(models.PendingApply)
            .filter(models.PendingApply.status == "pending")
            .all()
        )
        for entry in rows:
            remaining = (entry.expires_at - _utcnow()).total_seconds()
            if remaining <= 0:
                log.warning(
                    "PendingApply %s (%s) already expired, rolling back now",
                    entry.id, entry.apply_type,
                )
                _do_rollback_direct(db, entry)
                continue
            old_config = {}
            try:
                old_config = json.loads(entry.old_config_json or "{}")
            except Exception:  # noqa: BLE001
                log.warning("corrupt old_config_json on PendingApply %s", entry.id)
            _arm_manager_ticket(
                pending_id=entry.id,
                apply_type=entry.apply_type,
                old_config=old_config,
                description=entry.new_config_summary or f"{entry.apply_type} apply",
                timeout=int(max(1, remaining)),
            )
            restored += 1
    return restored
