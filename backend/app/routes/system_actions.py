# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Routes HTTP de l'API MurOS (sous-module)."""

from fastapi import APIRouter, Depends, HTTPException

from app import schemas
from app.auth import current_user

_auth_dep = [Depends(current_user)]


# --- System actions (reboot, shutdown) ---
system_actions_router = APIRouter(
    prefix="/api/system", tags=["system"], dependencies=_auth_dep,
)


@system_actions_router.post("/reboot", response_model=schemas.SystemActionResult)
def sys_reboot():
    from app import system_actions
    try:
        return system_actions.reboot()
    except RuntimeError as exc:
        raise HTTPException(500, str(exc))


@system_actions_router.get("/services", response_model=list[schemas.SystemServiceOut])
def sys_services():
    """Liste les services MurOS-geres effectivement installes, avec leur statut."""
    from app import system_actions
    return system_actions.list_services()


@system_actions_router.post("/shutdown", response_model=schemas.SystemActionResult)
def sys_shutdown():
    from app import system_actions
    try:
        return system_actions.shutdown()
    except RuntimeError as exc:
        raise HTTPException(500, str(exc))


# Endpoints accessibles uniquement via token de sync (pas via JWT).
# We expose them in a separate router without auth_dep, and validate by hand.
