# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Routes HTTP de l'API MurOS (sous-module)."""

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app import models, schemas, service_dirty
from app.auth import current_user
from app.db import get_db

_auth_dep = [Depends(current_user)]


# --- Haute dispo (HA) ---
ha_router = APIRouter(prefix="/api/ha", tags=["ha"], dependencies=_auth_dep)


@ha_router.get("/config", response_model=schemas.HaConfigOut)
def ha_get_config(db: Session = Depends(get_db)):
    from app import models
    row = db.get(models.HaConfig, 1)
    if row is None:
        row = models.HaConfig(id=1)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


@ha_router.put("/config", response_model=schemas.HaConfigOut)
def ha_put_config(payload: schemas.HaConfigIn, db: Session = Depends(get_db)):
    from app import models
    row = db.get(models.HaConfig, 1)
    if row is None:
        row = models.HaConfig(id=1)
        db.add(row)
    for k, v in payload.model_dump().items():
        setattr(row, k, v)
    db.commit()
    db.refresh(row)
    service_dirty.mark_dirty(db, "ha", summary="HA config updated")
    return row


@ha_router.get("/pending")
def ha_pending(db: Session = Depends(get_db)):
    return service_dirty.get_state(db, "ha")


@ha_router.get("/vips", response_model=list[schemas.HaVipOut])
def ha_list_vips(db: Session = Depends(get_db)):
    from app import models
    return db.query(models.HaVip).order_by(models.HaVip.vrid).all()


@ha_router.post("/vips", response_model=schemas.HaVipOut, status_code=201)
def ha_create_vip(payload: schemas.HaVipIn, db: Session = Depends(get_db)):
    from app import ha, models
    try:
        ha._validate_vrid(payload.vrid)
        ha._validate_cidr(payload.vip_cidr)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    # VRID uniqueness (within the instance, not on the L2 network segment
    # which we cannot control).
    exists = db.query(models.HaVip).filter(models.HaVip.vrid == payload.vrid).first()
    if exists:
        raise HTTPException(400, f"VRID {payload.vrid} already in use")
    row = models.HaVip(**payload.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    service_dirty.mark_dirty(db, "ha", summary=f"VIP VRID {row.vrid} added")
    return row


@ha_router.put("/vips/{vip_id}", response_model=schemas.HaVipOut)
def ha_update_vip(vip_id: int, payload: schemas.HaVipIn, db: Session = Depends(get_db)):
    from app import ha, models
    row = db.get(models.HaVip, vip_id)
    if row is None:
        raise HTTPException(404, "VIP not found")
    try:
        ha._validate_vrid(payload.vrid)
        ha._validate_cidr(payload.vip_cidr)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if payload.vrid != row.vrid:
        clash = db.query(models.HaVip).filter(
            models.HaVip.vrid == payload.vrid, models.HaVip.id != vip_id,
        ).first()
        if clash:
            raise HTTPException(400, f"VRID {payload.vrid} already in use")
    for k, v in payload.model_dump().items():
        setattr(row, k, v)
    db.commit()
    db.refresh(row)
    service_dirty.mark_dirty(db, "ha", summary=f"VIP VRID {row.vrid} updated")
    return row


@ha_router.delete("/vips/{vip_id}", status_code=204)
def ha_delete_vip(vip_id: int, db: Session = Depends(get_db)):
    from app import models
    row = db.get(models.HaVip, vip_id)
    if row is None:
        raise HTTPException(404, "VIP not found")
    vrid = row.vrid
    db.delete(row)
    db.commit()
    service_dirty.mark_dirty(db, "ha", summary=f"VIP VRID {vrid} removed")
    return Response(status_code=204)


@ha_router.post("/apply", response_model=schemas.HaApplyResult)
def ha_apply(db: Session = Depends(get_db)):
    from app import ha, models
    cfg = db.get(models.HaConfig, 1)
    if cfg is None:
        raise HTTPException(400, "Aucune config HA, fais d'abord PUT /api/ha/config")
    vips = db.query(models.HaVip).order_by(models.HaVip.vrid).all()
    cfg_dict = {
        "enabled": cfg.enabled, "role": cfg.role,
        "peer_address": cfg.peer_address, "sync_interface": cfg.sync_interface,
        "conntrack_sync": cfg.conntrack_sync, "preempt": cfg.preempt,
    }
    if cfg.enabled:
        if not cfg.peer_address or not cfg.sync_interface:
            raise HTTPException(400, "peer_address et sync_interface obligatoires pour activer la HA")
        if not vips:
            raise HTTPException(400, "At least one VIP is required to enable HA")
    vips_dict = [
        {
            "vrid": v.vrid, "interface": v.interface, "vip_cidr": v.vip_cidr,
            "auth_pass": v.auth_pass, "priority": v.priority,
            "description": v.description, "enabled": v.enabled,
        }
        for v in vips
    ]
    try:
        res = ha.apply_config(cfg_dict, vips_dict)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(500, str(exc))
    service_dirty.mark_clean(db, "ha", summary="keepalived reload")
    return res


@ha_router.get("/status", response_model=schemas.HaStatusOut)
def ha_status():
    from app import ha
    return ha.get_status()


@ha_router.post("/install", response_model=schemas.HaInstallResult)
def ha_install():
    """Installe keepalived + conntrackd via apt. Idempotent."""
    from app import ha
    try:
        return ha.install_packages()
    except RuntimeError as exc:
        raise HTTPException(500, str(exc))


# --- HA sync : config + push/pull DB ---

@ha_router.get("/role", response_model=schemas.HaSyncRole)
def ha_get_role():
    """Return the current VRRP role and whether the node accepts writes."""
    from app import ha_sync
    role = ha_sync.get_vrrp_role()
    return {"role": role, "writable": role in ("MASTER", "STANDALONE")}


@ha_router.get("/sync/config", response_model=schemas.HaSyncConfigOut)
def ha_sync_get_config(db: Session = Depends(get_db)):
    from app import ha_sync
    return ha_sync.get_config(db)


@ha_router.put("/sync/config", response_model=schemas.HaSyncConfigOut)
def ha_sync_update_config(data: schemas.HaSyncConfigIn, db: Session = Depends(get_db)):
    from app import ha_sync
    cfg = ha_sync.get_config(db)
    for field, value in data.model_dump().items():
        setattr(cfg, field, value)
    db.commit()
    db.refresh(cfg)
    return cfg


@ha_router.post("/sync/generate-token", response_model=schemas.HaSyncToken)
def ha_sync_gen_token():
    from app import ha_sync
    return {"token": ha_sync.generate_token()}


@ha_router.post("/sync/test", response_model=schemas.HaSyncTestResult)
def ha_sync_test(db: Session = Depends(get_db)):
    from app import ha_sync
    cfg = ha_sync.get_config(db)
    try:
        result = ha_sync.test_connection(cfg)
        return {
            "success": True,
            "peer_role": result.get("role"),
            "peer_version": result.get("version"),
        }
    except RuntimeError as exc:
        return {"success": False, "error": str(exc)}


@ha_router.post("/sync/push", response_model=schemas.HaSyncPushResult)
def ha_sync_push(db: Session = Depends(get_db)):
    from app import ha_sync
    cfg = ha_sync.get_config(db)
    try:
        return ha_sync.push_to_peer(db, cfg, triggered_by="manual")
    except RuntimeError as exc:
        raise HTTPException(500, str(exc))


@ha_router.get("/sync/log", response_model=list[schemas.HaSyncLogOut])
def ha_sync_get_log(db: Session = Depends(get_db), limit: int = 50):
    return (
        db.query(models.HaSyncLog)
        .order_by(models.HaSyncLog.id.desc())
        .limit(min(limit, 200))
        .all()
    )


