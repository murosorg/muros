# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Routes HTTP de l'API MurOS (sous-module)."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models, schemas, service_dirty
from app.auth import current_user
from app.db import get_db

_auth_dep = [Depends(current_user)]


# --- Diagnostic reseau ---
diag_router = APIRouter(prefix="/api/diag", tags=["diag"], dependencies=_auth_dep)


@diag_router.post("/ping", response_model=schemas.DiagCommandResult)
def diag_ping(data: schemas.DiagPingIn):
    from app import diag
    try:
        return diag.ping(data.target, data.count)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@diag_router.post("/traceroute", response_model=schemas.DiagCommandResult)
def diag_traceroute(data: schemas.DiagTracerouteIn):
    from app import diag
    try:
        return diag.traceroute(data.target, data.max_hops)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@diag_router.post("/dns", response_model=schemas.DiagCommandResult)
def diag_dns(data: schemas.DiagDnsIn):
    from app import diag
    try:
        return diag.dns_lookup(data.target, data.record_type, data.resolver)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@diag_router.post("/port-test", response_model=schemas.DiagCommandResult)
def diag_port_test(data: schemas.DiagPortTestIn):
    from app import diag
    try:
        return diag.port_test(data.target, data.port, data.protocol, data.timeout)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@diag_router.post("/capture", response_model=schemas.DiagCommandResult)
def diag_capture(data: schemas.DiagCaptureIn):
    from app import diag
    try:
        return diag.tcpdump_capture(data.interface, data.count, data.filter_expr)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@diag_router.post("/conntrack", response_model=schemas.DiagCommandResult)
def diag_conntrack(data: schemas.DiagConntrackIn):
    from app import diag
    try:
        return diag.conntrack_show(data.filter, data.limit)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@diag_router.get("/interfaces", response_model=list[str])
def diag_interfaces():
    from app import diag
    return diag.list_interfaces()


@diag_router.post("/public-ip", response_model=schemas.DiagCommandResult)
def diag_public_ip(data: schemas.DiagPublicIpIn):
    from app import diag
    try:
        return diag.public_ip(data.family)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


# Snapshots etat systeme. GET sans argument => safe.
@diag_router.get("/routes", response_model=schemas.DiagCommandResult)
def diag_routes():
    from app import diag
    return diag.show_routes()


@diag_router.get("/addresses", response_model=schemas.DiagCommandResult)
def diag_addresses():
    from app import diag
    return diag.show_addresses()


@diag_router.get("/nft", response_model=schemas.DiagCommandResult)
def diag_nft():
    from app import diag
    return diag.show_nft_ruleset()


# --- HTTP / nginx config ---
http_router = APIRouter(prefix="/api/http", tags=["http"], dependencies=_auth_dep)


def _get_http_config(db: Session) -> models.HttpConfig:
    cfg = db.get(models.HttpConfig, 1)
    if cfg is None:
        cfg = models.HttpConfig(id=1)
        db.add(cfg)
        db.commit()
        db.refresh(cfg)
    return cfg


@http_router.get("/status")
def http_get_status():
    """Live status of the nginx reverse proxy serving the MurOS UI.

    Returned shape matches what `ServiceStatusInline` on the
    frontend consumes for every other service page (DHCP, DNS,
    SNMP, SSH, ...). Kept lightweight (no DB hit), polled from the
    page header.
    """
    from app.service_state import (
        is_active as _is_active,
        pkg_version as _pkg_version,
        service_state as _state,
        which as _which,
    )
    unit = "nginx.service"
    installed = bool(_which("nginx"))
    return {
        "installed": installed,
        "service_active": _is_active(unit) if installed else False,
        "service_state": _state(unit) if installed else "unknown",
        "version": _pkg_version("nginx", "nginx") if installed else None,
    }


@http_router.get("/config", response_model=schemas.HttpConfigOut)
def http_get_config(db: Session = Depends(get_db)):
    return _get_http_config(db)


@http_router.put("/config", response_model=schemas.HttpConfigOut)
def http_update_config(data: schemas.HttpConfigIn, db: Session = Depends(get_db)):
    cfg = _get_http_config(db)
    # confirm_loopback / skip_rollback sont des flags transient, pas des
    # colonnes du modele. On les ignore au save.
    payload = data.model_dump(exclude={"confirm_loopback", "skip_rollback"})
    for field, value in payload.items():
        setattr(cfg, field, value)
    db.commit()
    db.refresh(cfg)
    service_dirty.mark_dirty(db, "http", summary="HTTP access config updated")
    return cfg


@http_router.get("/pending")
def http_pending(db: Session = Depends(get_db)):
    return service_dirty.get_state(db, "http")


@http_router.post("/apply", response_model=schemas.HttpApplyResult)
def http_apply(skip_rollback: bool = False, db: Session = Depends(get_db)):
    """Applique la conf HTTP et cree un pending pour rollback automatique."""
    from app import nginx_config, pending_apply
    cfg = _get_http_config(db)

    # Snapshot de l'ancienne conf avant apply (pour rollback).
    # On lit la conf sur disque pour avoir l'etat reel (au cas ou la DB
    # aurait ete mise a jour sans apply).
    old_disk = {
        "listen_address": cfg.listen_address,
        "port_https": cfg.port_https,
        "port_http": cfg.port_http,
        "redirect_http_to_https": cfg.redirect_http_to_https,
    }
    try:
        res = nginx_config.apply_config(cfg)
    except RuntimeError as exc:
        raise HTTPException(500, str(exc))

    # Cree le pending uniquement si on est en mode applique reel ET que
    # l'admin n'a pas explicitement demande a skip le rollback (cas du
    # changement d'interface admin volontaire avec perte d'acces).
    pending_id = None
    if res.get("applied") and not skip_rollback:
        new_summary = f"{cfg.listen_address}:{cfg.port_https} HTTPS"
        pending = pending_apply.create_pending(
            "http", old_disk, new_config_summary=new_summary,
            timeout_seconds=10,
        )
        pending_id = pending.id

    if res.get("applied"):
        service_dirty.mark_clean(db, "http", summary="nginx reload")

    return {
        **res,
        "pending_apply_id": pending_id,
        "rollback_timeout_seconds": 10 if pending_id else None,
    }


@http_router.post("/confirm-apply/{pending_id}")
def http_confirm_apply(pending_id: int):
    from app import pending_apply
    try:
        entry = pending_apply.confirm(pending_id)
        return {"status": entry.status, "id": entry.id}
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@http_router.post("/rollback-apply/{pending_id}")
def http_rollback_apply(pending_id: int):
    from app import pending_apply
    try:
        entry = pending_apply.rollback_now(pending_id)
        return {"status": entry.status, "id": entry.id, "error": entry.rollback_error}
    except ValueError as exc:
        raise HTTPException(404, str(exc))





