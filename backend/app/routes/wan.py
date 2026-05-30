# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""REST CRUD for the multi-WAN failover gateways.

The muros-wan-monitor daemon consumes this table and maintains `status`,
`consecutive_failures`, `consecutive_successes`, `last_probe_at`,
`last_change_at`. The admin only edits the config, never these runtime
fields.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import current_user
from app.db import get_db
from app.models import Interface, WanGateway
from app.schemas.network import WanActiveOut, WanGatewayIn, WanGatewayOut
from app import network as net

import logging
import subprocess

log = logging.getLogger("muros.wan")

_auth_dep = [Depends(current_user)]


def _sync_monitor_service(db: Session) -> None:
    """Start/stop muros-wan-monitor depending on whether any WAN is active.

    Avoids burning idle CPU when no WAN is declared; starts as soon as
    there is at least one enabled row. Best-effort: if systemctl is not
    available (tests, dev on a Mac, etc.) the error is logged and ignored.
    """
    has_active = db.query(WanGateway).filter(WanGateway.enabled.is_(True)).count() > 0
    cmd = ["systemctl", "enable" if has_active else "disable", "--now",
           "muros-wan-monitor.service"]
    try:
        subprocess.run(cmd, capture_output=True, timeout=10)
    except Exception as exc:  # noqa: BLE001
        log.debug("Could not (de)activate the WAN monitor service: %s", exc)

wan_router = APIRouter(prefix="/api/wan", tags=["wan"], dependencies=_auth_dep)


@wan_router.get("/status")
def wan_get_status():
    """Live state of the muros-wan-monitor daemon (service_state + version).

    The monitor probes the listed gateways and switches the default route
    by priority. Version = version of the installed muros package.
    """
    from app.service_state import service_state, pkg_version
    return {
        "service_state": service_state("muros-wan-monitor.service"),
        "version": pkg_version("muros", label="muros-wan-monitor"),
    }


def _to_out(g: WanGateway) -> WanGatewayOut:
    return WanGatewayOut.model_validate(g)


@wan_router.get("/gateways", response_model=list[WanGatewayOut])
def list_gateways(db: Session = Depends(get_db)):
    rows = db.query(WanGateway).order_by(WanGateway.priority, WanGateway.id).all()
    return [_to_out(r) for r in rows]


@wan_router.post("/gateways", response_model=WanGatewayOut, status_code=201)
def create_gateway(payload: WanGatewayIn, db: Session = Depends(get_db)):
    iface = db.query(Interface).filter(Interface.id == payload.interface_id).first()
    if not iface:
        raise HTTPException(404, "Unknown interface")
    g = WanGateway(
        name=payload.name,
        interface_id=payload.interface_id,
        gateway=payload.gateway,
        priority=payload.priority,
        monitoring_target=payload.monitoring_target,
        interval_s=payload.interval_s,
        failures_threshold=payload.failures_threshold,
        enabled=payload.enabled,
        comment=payload.comment,
        status="unknown",
    )
    db.add(g)
    db.commit()
    db.refresh(g)
    # Install the default route in the dedicated table. Best-effort: if
    # the interface is not ready yet, the monitor will reinstall it on the
    # next successful probe.
    try:
        net.wan_set_table_default(g.id, iface.name, g.gateway)
    except Exception as exc:  # noqa: BLE001
        log.debug("Deferred WAN default route install (gw %s): %s", g.id, exc)
    _sync_monitor_service(db)
    return _to_out(g)


@wan_router.put("/gateways/{gw_id}", response_model=WanGatewayOut)
def update_gateway(gw_id: int, payload: WanGatewayIn, db: Session = Depends(get_db)):
    g = db.query(WanGateway).filter(WanGateway.id == gw_id).first()
    if not g:
        raise HTTPException(404, "Unknown WAN gateway")
    iface = db.query(Interface).filter(Interface.id == payload.interface_id).first()
    if not iface:
        raise HTTPException(404, "Unknown interface")
    # Changing the interface or gateway-ip invalidates the dedicated table.
    iface_changed = g.interface_id != payload.interface_id
    gw_changed = g.gateway != payload.gateway
    g.name = payload.name
    g.interface_id = payload.interface_id
    g.gateway = payload.gateway
    g.priority = payload.priority
    g.monitoring_target = payload.monitoring_target
    g.interval_s = payload.interval_s
    g.failures_threshold = payload.failures_threshold
    g.enabled = payload.enabled
    g.comment = payload.comment
    db.commit()
    db.refresh(g)
    if iface_changed or gw_changed:
        try:
            net.wan_set_table_default(g.id, iface.name, g.gateway)
        except Exception as exc:  # noqa: BLE001
            log.debug("Deferred WAN default route update (gw %s): %s", g.id, exc)
    _sync_monitor_service(db)
    return _to_out(g)


@wan_router.delete("/gateways/{gw_id}", status_code=204)
def delete_gateway(gw_id: int, db: Session = Depends(get_db)):
    g = db.query(WanGateway).filter(WanGateway.id == gw_id).first()
    if not g:
        raise HTTPException(404, "Unknown WAN gateway")
    # We clean up the dedicated table before the DB drop. If this WAN carried
    # the global default at delete time, the monitor will recompute at the
    # next tick (and switch to another WAN that is UP, or remove the default
    # if no WAN is UP anymore).
    try:
        net.wan_clear_table(g.id)
    except Exception:
        pass
    db.delete(g)
    db.commit()
    _sync_monitor_service(db)


@wan_router.get("/active", response_model=WanActiveOut)
def get_active(db: Session = Depends(get_db)):
    """The WAN currently carrying the default route.

    Among the enabled+up gateways, the highest priority (lowest `priority`
    value). If there is no candidate, report `all_down` or `no_gateway`
    depending on the case, to help the UI diagnostics.
    """
    enabled = [
        g for g in db.query(WanGateway).order_by(WanGateway.priority).all()
        if g.enabled
    ]
    if not enabled:
        return WanActiveOut(active_id=None, active_name=None, reason="no_gateway")
    ups = [g for g in enabled if g.status == "up"]
    if not ups:
        return WanActiveOut(active_id=None, active_name=None, reason="all_down")
    best = ups[0]
    return WanActiveOut(active_id=best.id, active_name=best.name, reason="healthy")


@wan_router.post("/gateways/{gw_id}/probe", response_model=WanGatewayOut)
def probe_now(gw_id: int, db: Session = Depends(get_db)):
    """Force an immediate probe. Useful for tests / the UI 'Test now'."""
    g = db.query(WanGateway).filter(WanGateway.id == gw_id).first()
    if not g:
        raise HTTPException(404, "Unknown WAN gateway")
    if not g.interface:
        raise HTTPException(400, "Gateway has no interface")
    try:
        ok = net.wan_probe(g.interface.name, g.monitoring_target)
    except ValueError as e:
        raise HTTPException(400, str(e))
    g.last_probe_at = datetime.now(timezone.utc)
    # Manual probe: we do not touch the anti-flap counters (managed by the
    # daemon). We only expose the raw result through the status.
    g.status = "up" if ok else "down"
    db.commit()
    db.refresh(g)
    return _to_out(g)
