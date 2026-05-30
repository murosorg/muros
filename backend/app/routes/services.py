# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""REST CRUD for the DHCP server (Kea) and the recursive DNS (Unbound)."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import current_user
from app.db import get_db
from app.models import (
    DhcpConfig, DhcpPool, DhcpStaticLease, DnsConfig, DnsLocalRecord,
    Interface,
)
from app.services.dns_apply import DnsApplyError
from app.services.dhcp_apply import DhcpApplyError
from app import service_dirty
from app.schemas.services import (
    DhcpActiveLease,
    DhcpConfigIn, DhcpConfigOut,
    DhcpPoolIn, DhcpPoolOut,
    DhcpStaticLeaseIn, DhcpStaticLeaseOut,
    DhcpStatus,
    DnsConfigIn, DnsConfigOut,
    DnsLocalRecordIn, DnsLocalRecordOut,
    DnsStatus,
)
from app.services import dhcp_apply, dns_apply


def _stage_dhcp(db: Session, summary: str | None = None) -> None:
    """Save path: regenerate the Kea config and flag dhcp dirty.

    The live daemon keeps the previous config until the operator clicks
    Apply on the page header, which is the only path that calls
    `dhcp_apply.reload()`.
    """
    dhcp_apply.write_conf(db)
    service_dirty.mark_dirty(db, "dhcp", summary=summary)


def _stage_dns(db: Session, summary: str | None = None) -> None:
    """Save path for the recursive DNS server (unbound)."""
    dns_apply.write_conf(db)
    service_dirty.mark_dirty(db, "dns", summary=summary)


def _reload_dns(db: Session) -> None:
    """Apply path: reload unbound. Surfaces validation errors as 409."""
    try:
        dns_apply.reload(db)
    except DnsApplyError as exc:
        raise HTTPException(409, str(exc)) from exc

_auth_dep = [Depends(current_user)]

dhcp_router = APIRouter(prefix="/api/dhcp", tags=["dhcp"], dependencies=_auth_dep)
# dns_services_router to avoid clashing with the existing dns_router
# (system_ops.py) that handles /etc/resolv.conf (resolver clients).
dns_services_router = APIRouter(prefix="/api/dns", tags=["dns"], dependencies=_auth_dep)
# Local alias kept so the rest of this file reads naturally.
dns_router = dns_services_router


# --- DHCP -----------------------------------------------------------------

def _get_dhcp_cfg(db: Session) -> DhcpConfig:
    cfg = db.get(DhcpConfig, 1)
    if cfg is None:
        cfg = DhcpConfig(id=1)
        db.add(cfg)
        db.commit()
    return cfg


@dhcp_router.get("/config", response_model=DhcpConfigOut)
def dhcp_get(db: Session = Depends(get_db)):
    return DhcpConfigOut.model_validate(_get_dhcp_cfg(db))


@dhcp_router.put("/config", response_model=DhcpConfigOut)
def dhcp_put(payload: DhcpConfigIn, db: Session = Depends(get_db)):
    cfg = _get_dhcp_cfg(db)
    cfg.enabled = payload.enabled
    cfg.authoritative = payload.authoritative
    cfg.default_lease_seconds = payload.default_lease_seconds
    cfg.domain = payload.domain
    db.commit()
    db.refresh(cfg)
    _stage_dhcp(db, summary="DHCP config updated")
    return DhcpConfigOut.model_validate(cfg)


@dhcp_router.get("/status", response_model=DhcpStatus)
def dhcp_get_status(db: Session = Depends(get_db)):
    return DhcpStatus.model_validate(dhcp_apply.get_status(db))


@dhcp_router.get("/leases/active", response_model=list[DhcpActiveLease])
def dhcp_active_leases():
    return [DhcpActiveLease.model_validate(lease) for lease in dhcp_apply.read_active_leases()]


@dhcp_router.get("/pools", response_model=list[DhcpPoolOut])
def dhcp_pools_list(db: Session = Depends(get_db)):
    return [DhcpPoolOut.model_validate(p)
            for p in db.query(DhcpPool).order_by(DhcpPool.id).all()]


@dhcp_router.post("/pools", response_model=DhcpPoolOut, status_code=201)
def dhcp_pool_create(payload: DhcpPoolIn, db: Session = Depends(get_db)):
    iface = db.get(Interface, payload.interface_id)
    if iface is None:
        raise HTTPException(404, "Unknown interface")
    existing = db.query(DhcpPool).filter(
        DhcpPool.interface_id == payload.interface_id
    ).first()
    if existing is not None:
        raise HTTPException(400, "A pool already exists on this interface")
    p = DhcpPool(**payload.model_dump())
    db.add(p)
    db.commit()
    db.refresh(p)
    _stage_dhcp(db, summary="DHCP pool added")
    return DhcpPoolOut.model_validate(p)


@dhcp_router.put("/pools/{pool_id}", response_model=DhcpPoolOut)
def dhcp_pool_update(pool_id: int, payload: DhcpPoolIn, db: Session = Depends(get_db)):
    p = db.get(DhcpPool, pool_id)
    if p is None:
        raise HTTPException(404, "Unknown pool")
    for k, v in payload.model_dump().items():
        setattr(p, k, v)
    db.commit()
    db.refresh(p)
    _stage_dhcp(db, summary="DHCP pool updated")
    return DhcpPoolOut.model_validate(p)


@dhcp_router.delete("/pools/{pool_id}", status_code=204)
def dhcp_pool_delete(pool_id: int, db: Session = Depends(get_db)):
    p = db.get(DhcpPool, pool_id)
    if p is None:
        raise HTTPException(404, "Unknown pool")
    db.delete(p)
    db.commit()
    _stage_dhcp(db, summary="DHCP pool removed")


@dhcp_router.get("/leases", response_model=list[DhcpStaticLeaseOut])
def dhcp_leases_list(db: Session = Depends(get_db)):
    return [DhcpStaticLeaseOut.model_validate(lease)
            for lease in db.query(DhcpStaticLease).order_by(DhcpStaticLease.id).all()]


@dhcp_router.post("/leases", response_model=DhcpStaticLeaseOut, status_code=201)
def dhcp_lease_create(payload: DhcpStaticLeaseIn, db: Session = Depends(get_db)):
    if db.get(DhcpPool, payload.pool_id) is None:
        raise HTTPException(404, "Unknown pool")
    lease = DhcpStaticLease(**payload.model_dump())
    db.add(lease)
    db.commit()
    db.refresh(lease)
    _stage_dhcp(db, summary="DHCP static lease added")
    return DhcpStaticLeaseOut.model_validate(lease)


@dhcp_router.put("/leases/{lease_id}", response_model=DhcpStaticLeaseOut)
def dhcp_lease_update(lease_id: int, payload: DhcpStaticLeaseIn, db: Session = Depends(get_db)):
    lease = db.get(DhcpStaticLease, lease_id)
    if lease is None:
        raise HTTPException(404, "Unknown lease")
    for k, v in payload.model_dump().items():
        setattr(lease, k, v)
    db.commit()
    db.refresh(lease)
    _stage_dhcp(db, summary="DHCP static lease updated")
    return DhcpStaticLeaseOut.model_validate(lease)


@dhcp_router.delete("/leases/{lease_id}", status_code=204)
def dhcp_lease_delete(lease_id: int, db: Session = Depends(get_db)):
    lease = db.get(DhcpStaticLease, lease_id)
    if lease is None:
        raise HTTPException(404, "Unknown lease")
    db.delete(lease)
    db.commit()
    _stage_dhcp(db, summary="DHCP static lease removed")


# --- DNS recursive (Unbound) ----------------------------------------------

def _get_dns_cfg(db: Session) -> DnsConfig:
    cfg = db.get(DnsConfig, 1)
    if cfg is None:
        cfg = DnsConfig(id=1)
        db.add(cfg)
        db.commit()
    return cfg


@dns_router.get("/recursive/config", response_model=DnsConfigOut)
def dns_get(db: Session = Depends(get_db)):
    return DnsConfigOut.model_validate(_get_dns_cfg(db))


@dns_router.put("/recursive/config", response_model=DnsConfigOut)
def dns_put(payload: DnsConfigIn, db: Session = Depends(get_db)):
    cfg = _get_dns_cfg(db)
    cfg.enabled = payload.enabled
    cfg.allow_query_cidrs = payload.allow_query_cidrs
    cfg.dnssec = payload.dnssec
    cfg.prefetch = payload.prefetch
    cfg.forwarders = payload.forwarders
    cfg.use_as_system_resolver = payload.use_as_system_resolver
    db.commit()
    db.refresh(cfg)
    _stage_dns(db, summary="DNS config updated")
    return DnsConfigOut.model_validate(cfg)


@dns_router.get("/recursive/status", response_model=DnsStatus)
def dns_get_status(db: Session = Depends(get_db)):
    return DnsStatus.model_validate(dns_apply.get_status(db))


@dns_router.get("/recursive/records", response_model=list[DnsLocalRecordOut])
def dns_records_list(db: Session = Depends(get_db)):
    return [DnsLocalRecordOut.model_validate(r)
            for r in db.query(DnsLocalRecord).order_by(DnsLocalRecord.id).all()]


@dns_router.post("/recursive/records", response_model=DnsLocalRecordOut, status_code=201)
def dns_record_create(payload: DnsLocalRecordIn, db: Session = Depends(get_db)):
    r = DnsLocalRecord(**payload.model_dump())
    db.add(r)
    db.commit()
    db.refresh(r)
    _stage_dns(db, summary="DNS local record added")
    return DnsLocalRecordOut.model_validate(r)


@dns_router.put("/recursive/records/{rec_id}", response_model=DnsLocalRecordOut)
def dns_record_update(rec_id: int, payload: DnsLocalRecordIn, db: Session = Depends(get_db)):
    r = db.get(DnsLocalRecord, rec_id)
    if r is None:
        raise HTTPException(404, "Unknown record")
    for k, v in payload.model_dump().items():
        setattr(r, k, v)
    db.commit()
    db.refresh(r)
    _stage_dns(db, summary="DNS local record updated")
    return DnsLocalRecordOut.model_validate(r)


@dns_router.delete("/recursive/records/{rec_id}", status_code=204)
def dns_record_delete(rec_id: int, db: Session = Depends(get_db)):
    r = db.get(DnsLocalRecord, rec_id)
    if r is None:
        raise HTTPException(404, "Unknown record")
    db.delete(r)
    db.commit()
    _stage_dns(db, summary="DNS local record removed")


# --- Apply endpoints (Save/Apply split) -----------------------------------
# Save (PUT/POST/DELETE above) writes DB + on-disk config + sets dirty.
# Apply (POST /apply below) restarts the daemon and clears dirty.
# Pending (GET /pending) is polled by the yellow Apply button to decide
# whether to show its orange dot.

@dhcp_router.get("/pending")
def dhcp_pending(db: Session = Depends(get_db)):
    return service_dirty.get_state(db, "dhcp")


@dhcp_router.post("/apply")
def dhcp_apply_now(db: Session = Depends(get_db)):
    try:
        dhcp_apply.reload(db)
    except DhcpApplyError as exc:
        # Keep the dirty flag set so the UI keeps the orange dot lit.
        raise HTTPException(409, str(exc)) from exc
    service_dirty.mark_clean(db, "dhcp", summary="Kea reload")
    return {"applied": True, **service_dirty.get_state(db, "dhcp")}


@dns_router.get("/recursive/pending")
def dns_pending(db: Session = Depends(get_db)):
    return service_dirty.get_state(db, "dns")


@dns_router.post("/recursive/apply")
def dns_apply_now(db: Session = Depends(get_db)):
    _reload_dns(db)
    service_dirty.mark_clean(db, "dns", summary="unbound reload")
    return {"applied": True, **service_dirty.get_state(db, "dns")}
