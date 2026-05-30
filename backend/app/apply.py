# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""nftables apply, backed by the unified rollback manager.

This module keeps the legacy ``ApplyManager`` interface (apply / confirm
/ rollback / status / check) so the existing HTTP routes and frontend
keep working unchanged. Internally everything goes through
:mod:`app.rollback`, which is the single source of truth for the
commit-confirmed pattern across MurOS.

The nftables specifics (running ``nft -f``, snapshotting the live
ruleset, persisting it to ``/etc/muros/nftables.conf``) live here.
The timer, state machine and ticket lifecycle live in
:mod:`app.rollback`.
"""
from __future__ import annotations

import logging
import os
import subprocess
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from app.rollback import (
    DEFAULT_TIMEOUT_SECONDS as DEFAULT_TIMEOUT,
    RollbackTicket,
    iso_utc,
    manager as rollback_manager,
)

log = logging.getLogger("muros.apply")

APPLY_ENABLED = os.environ.get("MUROS_APPLY", "false").lower() == "true"
RULESET_PATH = Path(os.environ.get("MUROS_RULESET", "/etc/muros/nftables.conf"))
BACKUP_PATH = Path("/tmp/muros-nftables-backup.conf")

ApplyState = Literal["idle", "pending", "committed", "rolled_back", "failed"]

_TICKET_TO_APPLY_STATE: dict[str, ApplyState] = {
    "pending": "pending",
    "committed": "committed",
    "rolled_back": "rolled_back",
    "rollback_failed": "failed",
}


@dataclass
class ApplyStatus:
    """Public-facing view of the current nftables apply.

    Built on demand from the active rollback ticket (or, if none, from
    the last terminated one) so the legacy HTTP contract is preserved.
    """
    state: ApplyState = "idle"
    started_at: datetime | None = None
    expires_at: datetime | None = None
    timeout_seconds: int = DEFAULT_TIMEOUT
    applied_ruleset: str | None = None
    backup_ruleset: str | None = None
    dry_run: bool = field(default=not APPLY_ENABLED)
    message: str | None = None

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "started_at": iso_utc(self.started_at),
            "expires_at": iso_utc(self.expires_at),
            "timeout_seconds": self.timeout_seconds,
            "dry_run": self.dry_run,
            "message": self.message,
        }


def _status_from_ticket(ticket: RollbackTicket | None) -> ApplyStatus:
    if ticket is None:
        return ApplyStatus()
    return ApplyStatus(
        state=_TICKET_TO_APPLY_STATE.get(ticket.state, "idle"),
        started_at=ticket.started_at,
        expires_at=ticket.expires_at,
        timeout_seconds=ticket.timeout_seconds,
        applied_ruleset=ticket.detail.get("applied_ruleset"),
        backup_ruleset=ticket.detail.get("backup_ruleset"),
        dry_run=not APPLY_ENABLED,
        message=ticket.message,
    )


class ApplyManager:
    """Thin facade around the unified rollback manager for nftables."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # Id of the most recent nftables ticket we registered. Used to
        # surface terminal state (committed/rolled_back/failed) after
        # the ticket has left the active pending set.
        self._last_ticket_id: str | None = None
        # Fallback status used when an apply failed before a ticket
        # could be registered (e.g. nft -f returned non-zero).
        self._last_failed_status: ApplyStatus | None = None

    @property
    def status(self) -> ApplyStatus:
        active = rollback_manager.active_of_kind("nftables")
        if active is not None:
            return _status_from_ticket(active)
        if self._last_ticket_id is not None:
            t = rollback_manager.get(self._last_ticket_id)
            if t is not None:
                return _status_from_ticket(t)
        if self._last_failed_status is not None:
            return self._last_failed_status
        return ApplyStatus()

    # -- subprocess helpers ---------------------------------------------

    def _run(self, args: list[str], stdin: str | None = None) -> tuple[int, str]:
        if not APPLY_ENABLED:
            # DEBUG only: a single apply can spawn many _run calls; INFO
            # would flood journalctl. Use MUROS_LOG=DEBUG to inspect.
            log.debug("DRY-RUN: %s", " ".join(args))
            return 0, ""
        try:
            res = subprocess.run(
                args, input=stdin, capture_output=True, text=True, timeout=10,
            )
            return res.returncode, (res.stdout + res.stderr).strip()
        except (subprocess.SubprocessError, FileNotFoundError) as e:
            return 1, str(e)

    def check(self, ruleset: str) -> tuple[bool, str]:
        """Validate a ruleset's syntax without loading it (nft -c -f -).

        Read-only: safe even when MUROS_APPLY is false; ``nft -c`` never
        modifies kernel state.
        """
        try:
            res = subprocess.run(
                ["nft", "-c", "-f", "-"],
                input=ruleset, capture_output=True, text=True, timeout=10,
            )
        except FileNotFoundError:
            return False, "nft is not installed on this system."
        except subprocess.SubprocessError as exc:
            return False, f"check error: {exc}"
        if res.returncode == 0:
            return True, "Syntax OK, the ruleset can be applied."
        msg = (res.stderr or res.stdout).strip()
        return False, msg or f"nft -c exited with code {res.returncode}"

    def _read_current_ruleset(self) -> str:
        if not APPLY_ENABLED:
            return "# dry-run: live ruleset not read\n"
        rc, out = self._run(["nft", "list", "ruleset"])
        return out if rc == 0 else ""

    # -- public API ------------------------------------------------------

    def apply(self, new_ruleset: str, timeout: int | None = None) -> ApplyStatus:
        with self._lock:
            if rollback_manager.active_of_kind("nftables") is not None:
                raise RuntimeError(
                    "An nftables apply is already pending, confirm or rollback it first"
                )

            backup = self._read_current_ruleset()
            if APPLY_ENABLED:
                BACKUP_PATH.write_text(backup)
                RULESET_PATH.parent.mkdir(parents=True, exist_ok=True)
                RULESET_PATH.write_text(new_ruleset)

            rc, msg = self._run(["nft", "-f", "-"], stdin=new_ruleset)
            if rc != 0:
                self._last_failed_status = ApplyStatus(
                    state="failed",
                    started_at=datetime.now(timezone.utc),
                    dry_run=not APPLY_ENABLED,
                    message=msg or "apply failed",
                )
                log.error("apply failed: %s", msg)
                return self._last_failed_status

            def _rollback_nft() -> None:
                # Snapshot replay: feed the captured ruleset back to nft
                # and restore the persisted file. Raised exceptions are
                # caught by the rollback manager which marks the ticket
                # as ``rollback_failed``.
                if not backup:
                    return
                rc2, msg2 = self._run(["nft", "-f", "-"], stdin=backup)
                if rc2 != 0:
                    raise RuntimeError(msg2 or f"nft -f exited with {rc2}")
                if APPLY_ENABLED:
                    RULESET_PATH.write_text(backup)

            ticket = rollback_manager.register(
                kind="nftables",
                description="nftables ruleset apply",
                rollback_fn=_rollback_nft,
                timeout=timeout,
                detail={
                    "applied_ruleset": new_ruleset,
                    "backup_ruleset": backup,
                    "dry_run": not APPLY_ENABLED,
                },
                exclusive_kind=True,
            )
            ticket.message = "Confirmation required or automatic rollback"
            self._last_ticket_id = ticket.id
            self._last_failed_status = None
            log.info(
                "apply pending, rollback in %ds (dry_run=%s, ticket=%s)",
                timeout, not APPLY_ENABLED, ticket.id,
            )
            return _status_from_ticket(ticket)

    def confirm(self) -> ApplyStatus:
        with self._lock:
            ticket = rollback_manager.active_of_kind("nftables")
            if ticket is None:
                raise RuntimeError("No nftables apply waiting for confirmation")
            rollback_manager.confirm(ticket.id)
            log.info("apply confirmed (ticket=%s)", ticket.id)
            return _status_from_ticket(ticket)

    def rollback(self, automatic: bool = False) -> ApplyStatus:
        with self._lock:
            ticket = rollback_manager.active_of_kind("nftables")
            if ticket is None:
                raise RuntimeError("No nftables apply pending")
            rollback_manager.rollback(ticket.id, automatic=automatic)
            log.info("rollback done (automatic=%s, ticket=%s)", automatic, ticket.id)
            return _status_from_ticket(ticket)


# Process-wide singleton.
manager = ApplyManager()
