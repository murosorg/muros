# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Routes CRUD pour les groupes de services et d'adresses.

Les groupes sont reutilisables dans les regles firewall via les
colonnes service_group_id, src_address_group_id, dst_address_group_id.
A chaque modification de groupe, le ruleset doit etre recompile pour
refleter les changements (le compilateur expand les groupes en sets
nft inline).
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models, schemas
from app.auth import current_user
from app.db import get_db

_auth_dep = [Depends(current_user)]

service_groups_router = APIRouter(
    prefix="/api/firewall/service-groups",
    tags=["firewall"],
    dependencies=_auth_dep,
)
address_groups_router = APIRouter(
    prefix="/api/firewall/address-groups",
    tags=["firewall"],
    dependencies=_auth_dep,
)


# --- Service groups ---

@service_groups_router.get("", response_model=list[schemas.ServiceGroupOut])
def list_service_groups(db: Session = Depends(get_db)):
    return db.query(models.ServiceGroup).order_by(models.ServiceGroup.name).all()


@service_groups_router.post("", response_model=schemas.ServiceGroupOut, status_code=201)
def create_service_group(payload: schemas.ServiceGroupCreate, db: Session = Depends(get_db)):
    if db.query(models.ServiceGroup).filter_by(name=payload.name).first():
        raise HTTPException(409, f"a service group named '{payload.name}' already exists")
    group = models.ServiceGroup(name=payload.name, description=payload.description)
    for p in payload.ports:
        group.ports.append(models.ServiceGroupPort(protocol=p.protocol, port=p.port))
    db.add(group)
    db.commit()
    db.refresh(group)
    return group


@service_groups_router.get("/{group_id}", response_model=schemas.ServiceGroupOut)
def get_service_group(group_id: int, db: Session = Depends(get_db)):
    g = db.get(models.ServiceGroup, group_id)
    if not g:
        raise HTTPException(404, "group not found")
    return g


@service_groups_router.put("/{group_id}", response_model=schemas.ServiceGroupOut)
def update_service_group(group_id: int, payload: schemas.ServiceGroupUpdate, db: Session = Depends(get_db)):
    g = db.get(models.ServiceGroup, group_id)
    if not g:
        raise HTTPException(404, "group not found")
    if payload.name is not None and payload.name != g.name:
        if db.query(models.ServiceGroup).filter_by(name=payload.name).first():
            raise HTTPException(409, f"a service group named '{payload.name}' already exists")
        g.name = payload.name
    if payload.description is not None:
        g.description = payload.description
    if payload.ports is not None:
        # Remplace integralement la liste des ports.
        g.ports.clear()
        db.flush()
        for p in payload.ports:
            g.ports.append(models.ServiceGroupPort(protocol=p.protocol, port=p.port))
    db.commit()
    db.refresh(g)
    return g


@service_groups_router.delete("/{group_id}", status_code=204)
def delete_service_group(group_id: int, db: Session = Depends(get_db)):
    g = db.get(models.ServiceGroup, group_id)
    if not g:
        raise HTTPException(404, "group not found")
    # Check that no rule references this group before deleting
    used = db.query(models.FirewallRule).filter_by(service_group_id=group_id).count()
    if used:
        raise HTTPException(
            409,
            f"groupe utilise par {used} regle(s) firewall, retirez la reference avant"
        )
    db.delete(g)
    db.commit()


# --- Address groups ---

@address_groups_router.get("", response_model=list[schemas.AddressGroupOut])
def list_address_groups(db: Session = Depends(get_db)):
    return db.query(models.AddressGroup).order_by(models.AddressGroup.name).all()


@address_groups_router.post("", response_model=schemas.AddressGroupOut, status_code=201)
def create_address_group(payload: schemas.AddressGroupCreate, db: Session = Depends(get_db)):
    if db.query(models.AddressGroup).filter_by(name=payload.name).first():
        raise HTTPException(409, f"an address group named '{payload.name}' already exists")
    group = models.AddressGroup(name=payload.name, description=payload.description)
    for e in payload.entries:
        group.entries.append(models.AddressGroupEntry(value=e.value))
    db.add(group)
    db.commit()
    db.refresh(group)
    return group


@address_groups_router.get("/{group_id}", response_model=schemas.AddressGroupOut)
def get_address_group(group_id: int, db: Session = Depends(get_db)):
    g = db.get(models.AddressGroup, group_id)
    if not g:
        raise HTTPException(404, "group not found")
    return g


@address_groups_router.put("/{group_id}", response_model=schemas.AddressGroupOut)
def update_address_group(group_id: int, payload: schemas.AddressGroupUpdate, db: Session = Depends(get_db)):
    g = db.get(models.AddressGroup, group_id)
    if not g:
        raise HTTPException(404, "group not found")
    if payload.name is not None and payload.name != g.name:
        if db.query(models.AddressGroup).filter_by(name=payload.name).first():
            raise HTTPException(409, f"an address group named '{payload.name}' already exists")
        g.name = payload.name
    if payload.description is not None:
        g.description = payload.description
    if payload.entries is not None:
        g.entries.clear()
        db.flush()
        for e in payload.entries:
            g.entries.append(models.AddressGroupEntry(value=e.value))
    db.commit()
    db.refresh(g)
    return g


@address_groups_router.delete("/{group_id}", status_code=204)
def delete_address_group(group_id: int, db: Session = Depends(get_db)):
    g = db.get(models.AddressGroup, group_id)
    if not g:
        raise HTTPException(404, "group not found")
    src_used = db.query(models.FirewallRule).filter_by(src_address_group_id=group_id).count()
    dst_used = db.query(models.FirewallRule).filter_by(dst_address_group_id=group_id).count()
    total = src_used + dst_used
    if total:
        raise HTTPException(
            409,
            f"groupe utilise par {total} regle(s) firewall, retirez la reference avant"
        )
    db.delete(g)
    db.commit()

