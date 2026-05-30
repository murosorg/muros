# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""First-boot onboarding wizard.

A fresh MurOS box ships with deliberately permissive "any -> firewall"
bootstrap rules (SSH, UI, ICMP) so the operator is never locked out
before the zones are wired. This wizard performs the one mandatory step:
pick which NIC faces the trusted LAN and give it an address. That is all
the security posture needs. The trusted LAN gets a zone and can reach the
firewall and its services; every other interface is left in no zone and
stays default-deny at the firewall, so it is filtered whether or not it is
ever named. Services keep listening on every interface; who can reach them
is enforced by the firewall zones, not by per-service binds.

The Internet uplink (WAN) is a connectivity concern, not a security one,
and is configured afterwards from the Network page where the addressing
mode (DHCP, static, PPPoE), MTU and the rest live. The wizard does not ask
for it: nothing is exposed by leaving the uplink unassigned, and forcing a
DHCP client onto a guessed NIC at first boot is more surprising than
helpful. Once the LAN zone exists, MurOS drops the permissive bootstrap
rules, leaving management reachable from the LAN only.
"""
import ipaddress

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import models, settings
from app.auth import current_user
from app.db import get_db

setup_router = APIRouter(prefix="/api/setup", tags=["setup"], dependencies=[Depends(current_user)])


class SetupInterface(BaseModel):
    name: str
    zone: str | None = None
    ip_address: str | None = None


class SetupState(BaseModel):
    completed: bool
    interfaces: list[SetupInterface]


class SetupApplyIn(BaseModel):
    lan_interface: str
    lan_cidr: str  # e.g. 192.168.1.1/24


def _is_completed(db: Session) -> bool:
    # Completed when the flag is set OR when at least one interface is
    # already bound to a zone. The second condition covers boxes
    # configured before the wizard existed (upgrade) and boxes wired
    # manually from the Network page: neither should be forced back
    # through the wizard.
    if settings.is_setup_completed():
        return True
    return db.query(models.Interface).filter(models.Interface.zone_id.isnot(None)).first() is not None


@setup_router.get("/state", response_model=SetupState)
def setup_state(db: Session = Depends(get_db)):
    ifaces = db.query(models.Interface).order_by(models.Interface.name).all()
    return SetupState(
        completed=_is_completed(db),
        interfaces=[
            SetupInterface(
                name=i.name,
                zone=i.zone.name if i.zone else None,
                ip_address=i.ip_address,
            )
            for i in ifaces
        ],
    )


def _zone(db: Session, name: str) -> models.Zone:
    z = db.query(models.Zone).filter(models.Zone.name == name).first()
    if z is None:
        z = models.Zone(name=name, description=name.upper())
        db.add(z)
        db.commit()
        db.refresh(z)
    return z


@setup_router.post("/apply", response_model=SetupState)
def setup_apply(data: SetupApplyIn, db: Session = Depends(get_db)):
    try:
        net = ipaddress.ip_interface(data.lan_cidr)
    except ValueError:
        raise HTTPException(400, "lan_cidr must be a valid address in CIDR notation (e.g. 192.168.1.1/24)")
    if net.network.prefixlen >= 31:
        raise HTTPException(400, "LAN prefix is too small to host clients")

    lan_if = db.query(models.Interface).filter(models.Interface.name == data.lan_interface).first()
    if lan_if is None:
        raise HTTPException(404, "Unknown interface")

    lan = _zone(db, "lan")

    # LAN: static, the gateway address the operator typed (host part of
    # lan_cidr).
    lan_if.zone_id = lan.id
    lan_if.ip_mode = "static"
    lan_if.ip_address = str(net)
    lan_if.enabled = True
    lan_if.dirty = True

    # Drop the permissive bootstrap rules now that the LAN zone exists.
    # The seeded "allow LAN to firewall" rule already lets the LAN reach
    # SSH/UI/services, so the any-source accepts are no longer needed and
    # would otherwise expose management to every other (untrusted) interface.
    (
        db.query(models.FirewallRule)
        .filter(
            models.FirewallRule.chain == "input",
            models.FirewallRule.action == "accept",
            models.FirewallRule.src_zone_id.is_(None),
        )
        .delete(synchronize_session=False)
    )
    # Mark zones dirty so the next firewall apply reloads the ruleset.
    for z in db.query(models.Zone).all():
        z.dirty = True
    db.commit()

    settings.set_setup_completed(True)
    return setup_state(db)
