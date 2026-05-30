# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""REST CRUD for QoS / traffic shaping (egress HTB + fq_codel via tc).

Three nested resources:
  - shaper : one per interface, caps egress bandwidth
  - class  : a priority bucket under a shaper
  - rule   : a classifier steering matched traffic into a class

Every write marks the 'qos' service dirty; the page header Apply button
calls POST /api/qos/apply which rebuilds the qdisc tree on the kernel and
clears the flag. tc state is volatile so muros-boot replays it at boot.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models, qos, service_dirty
from app.auth import current_user
from app.db import get_db
from app.schemas.qos import (
    QosApplyResult, QosClassIn, QosClassOut, QosRuleIn, QosRuleOut,
    QosShaperIn, QosShaperOut,
)

_auth_dep = [Depends(current_user)]
qos_router = APIRouter(prefix="/api/qos", tags=["qos"], dependencies=_auth_dep)


def _shaper_out(sh: models.QosShaper) -> QosShaperOut:
    return QosShaperOut(
        id=sh.id,
        interface_id=sh.interface_id,
        interface_name=sh.interface.name if sh.interface else None,
        enabled=sh.enabled,
        bandwidth_kbit=sh.bandwidth_kbit,
        comment=sh.comment,
        dirty=sh.dirty,
        created_at=sh.created_at,
        classes=[_class_out(c) for c in sorted(sh.classes, key=lambda c: c.minor)],
    )


def _class_out(c: models.QosClass) -> QosClassOut:
    return QosClassOut(
        id=c.id, shaper_id=c.shaper_id, name=c.name, minor=c.minor,
        priority=c.priority, rate_kbit=c.rate_kbit, ceil_kbit=c.ceil_kbit,
        is_default=c.is_default, comment=c.comment,
        rules=[QosRuleOut.model_validate(r) for r in sorted(c.rules, key=lambda x: x.position)],
    )


def _next_minor(sh: models.QosShaper) -> int:
    used = {c.minor for c in sh.classes}
    for m in range(10, 100):
        if m not in used:
            return m
    raise HTTPException(400, "Too many classes on this shaper (max 90)")


def _mark_dirty(db: Session) -> None:
    service_dirty.mark_dirty(db, "qos", summary="QoS config updated")


# --- Shapers ---

@qos_router.get("/shapers", response_model=list[QosShaperOut])
def list_shapers(db: Session = Depends(get_db)):
    rows = db.query(models.QosShaper).order_by(models.QosShaper.id).all()
    return [_shaper_out(s) for s in rows]


@qos_router.post("/shapers", response_model=QosShaperOut, status_code=201)
def create_shaper(payload: QosShaperIn, db: Session = Depends(get_db)):
    iface = db.get(models.Interface, payload.interface_id)
    if not iface:
        raise HTTPException(404, "Unknown interface")
    existing = db.query(models.QosShaper).filter(
        models.QosShaper.interface_id == payload.interface_id
    ).first()
    if existing:
        raise HTTPException(409, "A shaper already exists for this interface")
    try:
        qos.validate_shaper(payload.bandwidth_kbit)
    except ValueError as e:
        raise HTTPException(400, str(e))
    sh = models.QosShaper(
        interface_id=payload.interface_id, enabled=payload.enabled,
        bandwidth_kbit=payload.bandwidth_kbit, comment=payload.comment,
    )
    db.add(sh)
    db.flush()
    # Seed a catch-all default class so the shaper is functional at once.
    db.add(models.QosClass(
        shaper_id=sh.id, name="Default", minor=10, priority=3,
        rate_kbit=max(1, payload.bandwidth_kbit // 10), ceil_kbit=None,
        is_default=True,
    ))
    _mark_dirty(db)
    db.commit()
    db.refresh(sh)
    return _shaper_out(sh)


@qos_router.put("/shapers/{shaper_id}", response_model=QosShaperOut)
def update_shaper(shaper_id: int, payload: QosShaperIn, db: Session = Depends(get_db)):
    sh = db.get(models.QosShaper, shaper_id)
    if not sh:
        raise HTTPException(404, "Unknown shaper")
    iface = db.get(models.Interface, payload.interface_id)
    if not iface:
        raise HTTPException(404, "Unknown interface")
    if payload.interface_id != sh.interface_id:
        clash = db.query(models.QosShaper).filter(
            models.QosShaper.interface_id == payload.interface_id,
            models.QosShaper.id != shaper_id,
        ).first()
        if clash:
            raise HTTPException(409, "A shaper already exists for this interface")
    try:
        qos.validate_shaper(payload.bandwidth_kbit)
    except ValueError as e:
        raise HTTPException(400, str(e))
    sh.interface_id = payload.interface_id
    sh.enabled = payload.enabled
    sh.bandwidth_kbit = payload.bandwidth_kbit
    sh.comment = payload.comment
    sh.dirty = True
    _mark_dirty(db)
    db.commit()
    db.refresh(sh)
    return _shaper_out(sh)


@qos_router.delete("/shapers/{shaper_id}", status_code=204)
def delete_shaper(shaper_id: int, db: Session = Depends(get_db)):
    sh = db.get(models.QosShaper, shaper_id)
    if not sh:
        raise HTTPException(404, "Unknown shaper")
    db.delete(sh)
    _mark_dirty(db)
    db.commit()


# --- Classes ---

def _clear_other_defaults(db: Session, shaper_id: int, keep_id: int | None) -> None:
    q = db.query(models.QosClass).filter(models.QosClass.shaper_id == shaper_id)
    for c in q.all():
        if keep_id is not None and c.id == keep_id:
            continue
        c.is_default = False


@qos_router.post("/shapers/{shaper_id}/classes", response_model=QosClassOut, status_code=201)
def create_class(shaper_id: int, payload: QosClassIn, db: Session = Depends(get_db)):
    sh = db.get(models.QosShaper, shaper_id)
    if not sh:
        raise HTTPException(404, "Unknown shaper")
    try:
        qos.validate_class(payload.rate_kbit, payload.ceil_kbit, payload.priority)
    except ValueError as e:
        raise HTTPException(400, str(e))
    c = models.QosClass(
        shaper_id=shaper_id, name=payload.name, minor=_next_minor(sh),
        priority=payload.priority, rate_kbit=payload.rate_kbit,
        ceil_kbit=payload.ceil_kbit, is_default=payload.is_default,
        comment=payload.comment,
    )
    db.add(c)
    db.flush()
    if payload.is_default:
        _clear_other_defaults(db, shaper_id, keep_id=c.id)
    _mark_dirty(db)
    db.commit()
    db.refresh(c)
    return _class_out(c)


@qos_router.put("/classes/{class_id}", response_model=QosClassOut)
def update_class(class_id: int, payload: QosClassIn, db: Session = Depends(get_db)):
    c = db.get(models.QosClass, class_id)
    if not c:
        raise HTTPException(404, "Unknown class")
    try:
        qos.validate_class(payload.rate_kbit, payload.ceil_kbit, payload.priority)
    except ValueError as e:
        raise HTTPException(400, str(e))
    c.name = payload.name
    c.priority = payload.priority
    c.rate_kbit = payload.rate_kbit
    c.ceil_kbit = payload.ceil_kbit
    c.is_default = payload.is_default
    c.comment = payload.comment
    if payload.is_default:
        _clear_other_defaults(db, c.shaper_id, keep_id=c.id)
    _mark_dirty(db)
    db.commit()
    db.refresh(c)
    return _class_out(c)


@qos_router.delete("/classes/{class_id}", status_code=204)
def delete_class(class_id: int, db: Session = Depends(get_db)):
    c = db.get(models.QosClass, class_id)
    if not c:
        raise HTTPException(404, "Unknown class")
    db.delete(c)
    _mark_dirty(db)
    db.commit()


# --- Rules ---

@qos_router.post("/classes/{class_id}/rules", response_model=QosRuleOut, status_code=201)
def create_rule(class_id: int, payload: QosRuleIn, db: Session = Depends(get_db)):
    c = db.get(models.QosClass, class_id)
    if not c:
        raise HTTPException(404, "Unknown class")
    try:
        qos.validate_rule(payload.protocol, payload.dst_port,
                          payload.src_address, payload.dst_address, payload.dscp)
    except ValueError as e:
        raise HTTPException(400, str(e))
    r = models.QosRule(
        class_id=class_id, position=payload.position, protocol=payload.protocol,
        dst_port=payload.dst_port, src_address=payload.src_address,
        dst_address=payload.dst_address, dscp=payload.dscp,
        enabled=payload.enabled, comment=payload.comment,
    )
    db.add(r)
    _mark_dirty(db)
    db.commit()
    db.refresh(r)
    return QosRuleOut.model_validate(r)


@qos_router.put("/rules/{rule_id}", response_model=QosRuleOut)
def update_rule(rule_id: int, payload: QosRuleIn, db: Session = Depends(get_db)):
    r = db.get(models.QosRule, rule_id)
    if not r:
        raise HTTPException(404, "Unknown rule")
    try:
        qos.validate_rule(payload.protocol, payload.dst_port,
                          payload.src_address, payload.dst_address, payload.dscp)
    except ValueError as e:
        raise HTTPException(400, str(e))
    r.position = payload.position
    r.protocol = payload.protocol
    r.dst_port = payload.dst_port
    r.src_address = payload.src_address
    r.dst_address = payload.dst_address
    r.dscp = payload.dscp
    r.enabled = payload.enabled
    r.comment = payload.comment
    _mark_dirty(db)
    db.commit()
    db.refresh(r)
    return QosRuleOut.model_validate(r)


@qos_router.delete("/rules/{rule_id}", status_code=204)
def delete_rule(rule_id: int, db: Session = Depends(get_db)):
    r = db.get(models.QosRule, rule_id)
    if not r:
        raise HTTPException(404, "Unknown rule")
    db.delete(r)
    _mark_dirty(db)
    db.commit()


# --- Pending / Apply ---

@qos_router.get("/pending")
def qos_pending(db: Session = Depends(get_db)):
    return service_dirty.get_state(db, "qos")


@qos_router.post("/apply", response_model=QosApplyResult)
def qos_apply(db: Session = Depends(get_db)):
    try:
        summary = qos.apply_all(db)
    except (RuntimeError, ValueError) as e:
        raise HTTPException(500, f"QoS apply failed: {e}")
    for sh in db.query(models.QosShaper).all():
        sh.dirty = False
    service_dirty.mark_clean(db, "qos", summary="tc qdisc reloaded")
    db.commit()
    return QosApplyResult(**summary)
