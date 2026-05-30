# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""IPv6 Router Advertisements (radvd) endpoints."""
import ipaddress

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import models
from app.auth import current_user
from app.db import get_db
from app.services import ra_apply

ra_router = APIRouter(prefix="/api/ipv6", tags=["ipv6"], dependencies=[Depends(current_user)])


class RaOut(BaseModel):
    enabled: bool
    interface: str | None
    managed: bool
    other_config: bool
    advertise_dns: bool
    # Computed /64 prefix that would be advertised (None if the chosen
    # interface has no IPv6 address yet).
    prefix: str | None
    # Interfaces that carry an IPv6 address and can therefore advertise.
    available_interfaces: list[str]


class RaIn(BaseModel):
    enabled: bool
    interface: str | None = None
    managed: bool = False
    other_config: bool = False
    advertise_dns: bool = True


def _v6_interfaces(db: Session) -> list[str]:
    out = []
    for itf in db.query(models.Interface).order_by(models.Interface.name).all():
        if not itf.ip_address:
            continue
        try:
            if ipaddress.ip_interface(itf.ip_address).version == 6:
                out.append(itf.name)
        except ValueError:
            continue
    return out


def _out(db: Session) -> RaOut:
    cfg = ra_apply.get_config(db)
    return RaOut(
        enabled=cfg.enabled,
        interface=cfg.interface,
        managed=cfg.managed,
        other_config=cfg.other_config,
        advertise_dns=cfg.advertise_dns,
        prefix=ra_apply._iface_v6_prefix(db, cfg.interface),
        available_interfaces=_v6_interfaces(db),
    )


@ra_router.get("/ra", response_model=RaOut)
def get_ra(db: Session = Depends(get_db)):
    return _out(db)


@ra_router.put("/ra", response_model=RaOut)
def set_ra(data: RaIn, db: Session = Depends(get_db)):
    if data.enabled:
        if not data.interface:
            raise HTTPException(400, "an interface is required to enable Router Advertisements")
        if ra_apply._iface_v6_prefix(db, data.interface) is None:
            raise HTTPException(400, "the selected interface has no IPv6 address to advertise")
    cfg = ra_apply.get_config(db)
    cfg.enabled = data.enabled
    cfg.interface = data.interface
    cfg.managed = data.managed
    cfg.other_config = data.other_config
    cfg.advertise_dns = data.advertise_dns
    db.commit()
    try:
        ra_apply.apply(db)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"failed to apply radvd: {exc}")
    return _out(db)
