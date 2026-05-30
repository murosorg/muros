# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Aggregated apply-state endpoints for managed services.

Individual services keep their own /pending and /apply endpoints
(prefixed by the service path : /api/dhcp/pending, /api/dns/recursive/pending,
/api/snmp/pending, ...). This module exposes a thin aggregation layer
used by the sidebar to render a single global indicator ("3 services
     need apply") with a single poll.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import service_dirty
from app.auth import current_user
from app.db import get_db

# Require an authenticated session like every other API router. These
# endpoints expose service apply-state and the Save/Apply audit log (which
# includes operator usernames), so they must not be reachable anonymously.
_auth_dep = [Depends(current_user)]

service_apply_router = APIRouter(
    prefix="/api/services", tags=["services"], dependencies=_auth_dep,
)


@service_apply_router.get("/pending")
def services_pending(db: Session = Depends(get_db)):
    """Return the apply state of every managed service in one payload.

    Used by the sidebar's pending badge to avoid running N polls every
    3 seconds (one per service). Shape :

        {
          "states": { "dhcp": {...}, "dns": {...}, ... },
          "dirty_count": 2,
          "dirty_services": ["dhcp", "dns"]
        }
    """
    # Cheap lazy reconcile so phantom dirty flags (apply that failed
    # mid-flight, manual rollback on disk, etc) clear on the next poll
    # without requiring a reboot.
    service_dirty.reconcile_all(db, source="poll")
    states = service_dirty.all_states(db)
    dirty_services = [name for name, s in states.items() if s["dirty"]]
    return {
        "states": states,
        "dirty_count": len(dirty_services),
        "dirty_services": dirty_services,
    }


@service_apply_router.get("/log")
def services_log(
    name: str | None = None,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """Recent Save / Apply audit rows, optionally filtered by service name."""
    return {"entries": service_dirty.recent_log(db, name=name, limit=max(1, min(limit, 500)))}
