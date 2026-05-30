# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""REST CRUD for the stateful DHCPv6 server (Kea DHCPv6).

Mirrors the IPv4 DHCP routes: Save regenerates kea-dhcp6.conf and flags
the 'dhcp6' service dirty; the page header Apply restarts kea-dhcp6 and
clears the flag.
"""
from __future__ import annotations

import ipaddress

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import service_dirty
from app.auth import current_user
from app.db import get_db
from app.models import Dhcp6Config, Dhcp6Pool, Interface
from app.schemas.dhcp6 import (
    Dhcp6ActiveLease, Dhcp6ConfigIn, Dhcp6ConfigOut,
    Dhcp6PoolIn, Dhcp6PoolOut, Dhcp6Status,
)
from app.services import dhcp6_apply
from app.services.dhcp6_apply import Dhcp6ApplyError

_auth_dep = [Depends(current_user)]
dhcp6_router = APIRouter(prefix="/api/dhcp6", tags=["dhcp6"], dependencies=_auth_dep)


def _get_cfg(db: Session) -> Dhcp6Config:
    cfg = db.get(Dhcp6Config, 1)
    if cfg is None:
        cfg = Dhcp6Config(id=1)
        db.add(cfg)
        db.commit()
    return cfg


def _stage(db: Session, summary: str | None = None) -> None:
    dhcp6_apply.write_conf(db)
    service_dirty.mark_dirty(db, "dhcp6", summary=summary)


def _validate_range(range_start: str, range_end: str) -> None:
    try:
        start = ipaddress.IPv6Address(range_start.strip())
        end = ipaddress.IPv6Address(range_end.strip())
    except ipaddress.AddressValueError:
        raise HTTPException(400, "range_start and range_end must be valid IPv6 addresses")
    if int(end) < int(start):
        raise HTTPException(400, "range_end must be >= range_start")
    # Kea wants both ends inside one /64 subnet.
    net = ipaddress.IPv6Network((start, 64), strict=False)
    if end not in net:
        raise HTTPException(400, "range_start and range_end must share the same /64")


def _pool_out(p: Dhcp6Pool) -> Dhcp6PoolOut:
    out = Dhcp6PoolOut.model_validate(p)
    out.interface_name = p.interface.name if p.interface else None
    return out


@dhcp6_router.get("/config", response_model=Dhcp6ConfigOut)
def dhcp6_get(db: Session = Depends(get_db)):
    return Dhcp6ConfigOut.model_validate(_get_cfg(db))


@dhcp6_router.put("/config", response_model=Dhcp6ConfigOut)
def dhcp6_put(payload: Dhcp6ConfigIn, db: Session = Depends(get_db)):
    cfg = _get_cfg(db)
    cfg.enabled = payload.enabled
    cfg.default_lease_seconds = payload.default_lease_seconds
    db.commit()
    db.refresh(cfg)
    _stage(db, summary="DHCPv6 config updated")
    return Dhcp6ConfigOut.model_validate(cfg)


@dhcp6_router.get("/status", response_model=Dhcp6Status)
def dhcp6_status(db: Session = Depends(get_db)):
    return Dhcp6Status.model_validate(dhcp6_apply.get_status(db))


@dhcp6_router.get("/leases/active", response_model=list[Dhcp6ActiveLease])
def dhcp6_active_leases():
    return [Dhcp6ActiveLease.model_validate(x) for x in dhcp6_apply.read_active_leases()]


@dhcp6_router.get("/pools", response_model=list[Dhcp6PoolOut])
def dhcp6_pools_list(db: Session = Depends(get_db)):
    return [_pool_out(p) for p in db.query(Dhcp6Pool).order_by(Dhcp6Pool.id).all()]


@dhcp6_router.post("/pools", response_model=Dhcp6PoolOut, status_code=201)
def dhcp6_pool_create(payload: Dhcp6PoolIn, db: Session = Depends(get_db)):
    iface = db.get(Interface, payload.interface_id)
    if iface is None:
        raise HTTPException(404, "Unknown interface")
    if db.query(Dhcp6Pool).filter(Dhcp6Pool.interface_id == payload.interface_id).first():
        raise HTTPException(400, "A DHCPv6 pool already exists for this interface")
    _validate_range(payload.range_start, payload.range_end)
    pool = Dhcp6Pool(
        interface_id=payload.interface_id, range_start=payload.range_start.strip(),
        range_end=payload.range_end.strip(), dns_servers=payload.dns_servers,
        lease_seconds=payload.lease_seconds, enabled=payload.enabled,
        comment=payload.comment,
    )
    db.add(pool)
    db.commit()
    db.refresh(pool)
    _stage(db, summary="DHCPv6 pool created")
    return _pool_out(pool)


@dhcp6_router.put("/pools/{pool_id}", response_model=Dhcp6PoolOut)
def dhcp6_pool_update(pool_id: int, payload: Dhcp6PoolIn, db: Session = Depends(get_db)):
    pool = db.get(Dhcp6Pool, pool_id)
    if pool is None:
        raise HTTPException(404, "Unknown pool")
    if db.get(Interface, payload.interface_id) is None:
        raise HTTPException(404, "Unknown interface")
    if payload.interface_id != pool.interface_id and db.query(Dhcp6Pool).filter(
        Dhcp6Pool.interface_id == payload.interface_id, Dhcp6Pool.id != pool_id,
    ).first():
        raise HTTPException(400, "A DHCPv6 pool already exists for this interface")
    _validate_range(payload.range_start, payload.range_end)
    pool.interface_id = payload.interface_id
    pool.range_start = payload.range_start.strip()
    pool.range_end = payload.range_end.strip()
    pool.dns_servers = payload.dns_servers
    pool.lease_seconds = payload.lease_seconds
    pool.enabled = payload.enabled
    pool.comment = payload.comment
    db.commit()
    db.refresh(pool)
    _stage(db, summary="DHCPv6 pool updated")
    return _pool_out(pool)


@dhcp6_router.delete("/pools/{pool_id}", status_code=204)
def dhcp6_pool_delete(pool_id: int, db: Session = Depends(get_db)):
    pool = db.get(Dhcp6Pool, pool_id)
    if pool is None:
        raise HTTPException(404, "Unknown pool")
    db.delete(pool)
    db.commit()
    _stage(db, summary="DHCPv6 pool deleted")


@dhcp6_router.get("/pending")
def dhcp6_pending(db: Session = Depends(get_db)):
    return service_dirty.get_state(db, "dhcp6")


@dhcp6_router.post("/apply")
def dhcp6_apply_now(db: Session = Depends(get_db)):
    from app import ha_sync
    try:
        dhcp6_apply.reload(db)
    except Dhcp6ApplyError as exc:
        raise HTTPException(409, str(exc)) from exc
    service_dirty.mark_clean(db, "dhcp6", summary="kea-dhcp6 reload")
    ha_sync.maybe_auto_push(db, triggered_by="dhcp6-apply")
    return {"message": "DHCPv6 applied."}
