# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Routes HTTP de l'API MurOS (sous-module)."""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app import models, schemas
from app.auth import current_user
from app.db import get_db

_auth_dep = [Depends(current_user)]


ha_sync_pub_router = APIRouter(prefix="/api/ha", tags=["ha-sync-pub"])


def _read_sync_token_header(request: Request) -> str | None:
    return request.headers.get("X-Muros-Sync-Token")


@ha_sync_pub_router.get("/sync/ping", response_model=schemas.HaSyncPingOut)
def ha_sync_ping(request: Request, db: Session = Depends(get_db)):
    """Ping accessible via token sync uniquement. Renvoie le role et la version."""
    from app import ha_sync
    token = _read_sync_token_header(request)
    cfg = ha_sync.get_config(db)
    if not cfg.enabled:
        raise HTTPException(503, "Sync HA desactivee sur ce noeud.")
    if not token or token != cfg.peer_token:
        raise HTTPException(401, "Invalid token.")
    from app import VERSION  # type: ignore
    try:
        version = VERSION
    except Exception:  # noqa: BLE001
        version = "unknown"
    return {"role": ha_sync.get_vrrp_role(), "version": version}


@ha_sync_pub_router.post("/sync/receive")
async def ha_sync_receive(request: Request, db: Session = Depends(get_db)):
    """Recoit une DB sqlite du peer (push). Pas de JWT, validation par token + HMAC."""
    from app import ha_sync
    token = _read_sync_token_header(request)
    signature = request.headers.get("X-Muros-Sync-Signature", "")
    cfg = ha_sync.get_config(db)
    if not cfg.enabled:
        raise HTTPException(503, "Sync HA desactivee sur ce noeud.")
    if not token or token != cfg.peer_token:
        raise HTTPException(401, "Invalid token.")
    body = await request.body()
    try:
        result = ha_sync.receive_from_peer(cfg, signature, body)
    except RuntimeError as exc:
        # Log echec
        entry = models.HaSyncLog(
            direction="receive", success=False, error=str(exc)[:500],
            duration_ms=0, db_size_bytes=len(body), triggered_by="peer-push",
        )
        db.add(entry)
        db.commit()
        raise HTTPException(400, str(exc))
    # Log succes
    entry = models.HaSyncLog(
        direction="receive", success=True, error=None,
        duration_ms=0, db_size_bytes=len(body), triggered_by="peer-push",
    )
    db.add(entry)
    db.commit()
    ha_sync._rotate_log(db, keep=50)
    return result


