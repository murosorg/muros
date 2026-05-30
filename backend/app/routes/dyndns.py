# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""REST endpoints for the dynamic DNS client.

No apply/dirty workflow: there is no config file. Changes take effect on
the next scheduler cycle, and an explicit 'Update now' pushes immediately.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import dyndns, models
from app.auth import current_user
from app.db import get_db
from app.schemas.dyndns import DynDnsEntryIn, DynDnsEntryOut, DynDnsUpdateResult

_auth_dep = [Depends(current_user)]
dyndns_router = APIRouter(prefix="/api/dyndns", tags=["dyndns"], dependencies=_auth_dep)


def _apply_provider_defaults(data: DynDnsEntryIn) -> tuple[str, str]:
    """Resolve (provider, server) and validate the provider-specific fields."""
    preset = dyndns.PROVIDER_PRESETS.get(data.provider)
    if preset is None:
        raise HTTPException(400, f"unknown provider '{data.provider}'")
    if data.provider == "custom":
        if not (data.custom_url or "").strip():
            raise HTTPException(400, "custom_url is required for the custom provider")
        return data.provider, ""
    server = data.server.strip() or preset["server"]
    if not server:
        raise HTTPException(400, "server is required")
    return data.provider, server


@dyndns_router.get("/providers")
def dyndns_providers():
    return dyndns.PROVIDER_PRESETS


@dyndns_router.get("/public-ip")
def dyndns_public_ip():
    return {"ip": dyndns.detect_public_ip()}


@dyndns_router.get("", response_model=list[DynDnsEntryOut])
def dyndns_list(db: Session = Depends(get_db)):
    return db.query(models.DynDnsEntry).order_by(models.DynDnsEntry.id).all()


@dyndns_router.post("", response_model=DynDnsEntryOut)
def dyndns_create(data: DynDnsEntryIn, db: Session = Depends(get_db)):
    provider, server = _apply_provider_defaults(data)
    entry = models.DynDnsEntry(
        enabled=data.enabled, provider=provider, server=server,
        hostname=data.hostname.strip(), username=data.username,
        password=data.password, custom_url=data.custom_url,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def _get_entry(db: Session, entry_id: int) -> models.DynDnsEntry:
    entry = db.get(models.DynDnsEntry, entry_id)
    if entry is None:
        raise HTTPException(404, "entry not found")
    return entry


@dyndns_router.put("/{entry_id}", response_model=DynDnsEntryOut)
def dyndns_update(entry_id: int, data: DynDnsEntryIn, db: Session = Depends(get_db)):
    entry = _get_entry(db, entry_id)
    provider, server = _apply_provider_defaults(data)
    entry.enabled = data.enabled
    entry.provider = provider
    entry.server = server
    entry.hostname = data.hostname.strip()
    entry.username = data.username
    # Keep the stored password when the form sends an empty value (the UI
    # never echoes the secret back).
    if data.password:
        entry.password = data.password
    entry.custom_url = data.custom_url
    db.commit()
    db.refresh(entry)
    return entry


@dyndns_router.delete("/{entry_id}")
def dyndns_delete(entry_id: int, db: Session = Depends(get_db)):
    entry = _get_entry(db, entry_id)
    db.delete(entry)
    db.commit()
    return {"deleted": entry_id}


@dyndns_router.post("/{entry_id}/update-now", response_model=DynDnsUpdateResult)
def dyndns_update_now_one(entry_id: int, db: Session = Depends(get_db)):
    entry = _get_entry(db, entry_id)
    ip = dyndns.detect_public_ip()
    if not ip:
        raise HTTPException(502, "public IP detection failed")
    res = dyndns.update_entry(db, entry, ip)
    return DynDnsUpdateResult(ip=ip, results=[res])


@dyndns_router.post("/update-now", response_model=DynDnsUpdateResult)
def dyndns_update_now_all(db: Session = Depends(get_db)):
    return DynDnsUpdateResult(**dyndns.run_updates(db, force=True))
