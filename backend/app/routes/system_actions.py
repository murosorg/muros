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


@system_actions_router.get("/listen-addresses", response_model=list[schemas.ListenAddressOut])
def sys_listen_addresses():
    """Liste les IPs locales utilisables comme adresse d'ecoute."""
    from app import system_actions
    return system_actions.list_listen_addresses()


@system_actions_router.post("/shutdown", response_model=schemas.SystemActionResult)
def sys_shutdown():
    from app import system_actions
    try:
        return system_actions.shutdown()
    except RuntimeError as exc:
        raise HTTPException(500, str(exc))


# Endpoints accessibles uniquement via token de sync (pas via JWT).
# On les expose dans un router separe sans auth_dep, et on valide a la main.
