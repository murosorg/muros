# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""REST endpoints for remote syslog forwarding (rsyslog omfwd).

Mirrors the SNMP service pattern: Save writes the DB row + the on-disk
rsyslog drop-in and flags the 'syslog' service dirty; the page header
Apply button restarts rsyslog and clears the flag.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models, service_dirty, syslog_fwd
from app.auth import current_user
from app.db import get_db
from app.schemas.syslog import (
    SyslogApplyResult, SyslogConfigIn, SyslogConfigOut, SyslogInstallResult,
    SyslogStatus,
)

_auth_dep = [Depends(current_user)]
syslog_router = APIRouter(prefix="/api/syslog", tags=["syslog"], dependencies=_auth_dep)


def _get_config(db: Session) -> models.SyslogConfig:
    cfg = db.get(models.SyslogConfig, 1)
    if cfg is None:
        cfg = models.SyslogConfig(id=1)
        db.add(cfg)
        db.commit()
        db.refresh(cfg)
    return cfg


@syslog_router.get("/status", response_model=SyslogStatus)
def syslog_status():
    return syslog_fwd.get_status()


@syslog_router.post("/install", response_model=SyslogInstallResult)
def syslog_install():
    try:
        return syslog_fwd.install_packages()
    except RuntimeError as e:
        raise HTTPException(500, str(e))


@syslog_router.get("/config", response_model=SyslogConfigOut)
def syslog_get_config(db: Session = Depends(get_db)):
    return _get_config(db)


@syslog_router.put("/config", response_model=SyslogConfigOut)
def syslog_update_config(data: SyslogConfigIn, db: Session = Depends(get_db)):
    """Save path: persist DB + write the drop-in + flag dirty (no restart)."""
    cfg = _get_config(db)
    if data.enabled:
        try:
            syslog_fwd.validate_config(data.host, data.port, data.protocol, data.format)
        except ValueError as e:
            raise HTTPException(400, str(e))
    cfg.enabled = data.enabled
    cfg.host = data.host
    cfg.port = data.port
    cfg.protocol = data.protocol
    cfg.format = data.format
    cfg.comment = data.comment
    try:
        syslog_fwd.write_conf(cfg)
    except RuntimeError as e:
        raise HTTPException(500, str(e))
    db.commit()
    db.refresh(cfg)
    service_dirty.mark_dirty(db, "syslog", summary="Syslog forwarding config updated")
    return cfg


@syslog_router.get("/pending")
def syslog_pending(db: Session = Depends(get_db)):
    return service_dirty.get_state(db, "syslog")


@syslog_router.post("/apply", response_model=SyslogApplyResult)
def syslog_apply(db: Session = Depends(get_db)):
    """Apply path: restart rsyslog then clear the dirty flag."""
    from app import ha_sync
    cfg = _get_config(db)
    try:
        res = syslog_fwd.reload(cfg)
    except RuntimeError as e:
        raise HTTPException(500, str(e))
    service_dirty.mark_clean(db, "syslog", summary="rsyslog reload")
    ha_sync.maybe_auto_push(db, triggered_by="syslog-apply")
    return SyslogApplyResult(**res)
