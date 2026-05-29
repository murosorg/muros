# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""REST CRUD pour les WAN gateways du multi-WAN failover.

Le daemon muros-wan-monitor consomme cette table et maintient `status`,
`consecutive_failures`, `consecutive_successes`, `last_probe_at`,
`last_change_at`. L'admin n'edite que la conf, jamais ces champs runtime.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import current_user
from app.db import get_db
from app.models import Interface, WanGateway
from app.schemas.network import WanActiveOut, WanGatewayIn, WanGatewayOut
from app import network as net

import subprocess

_auth_dep = [Depends(current_user)]


def _sync_monitor_service(db: Session) -> None:
    """Start/stop muros-wan-monitor selon presence de WANs actifs.

    Idle CPU pour rien quand aucun WAN n'est declare ; demarre des qu'il
    y a au moins une row enabled. Best-effort : si systemctl indisponible
    (tests, dev sur Mac, etc.) on swallow l'erreur.
    """
    has_active = db.query(WanGateway).filter(WanGateway.enabled.is_(True)).count() > 0
    cmd = ["systemctl", "enable" if has_active else "disable", "--now",
           "muros-wan-monitor.service"]
    try:
        subprocess.run(cmd, capture_output=True, timeout=10)
    except Exception:
        pass

wan_router = APIRouter(prefix="/api/wan", tags=["wan"], dependencies=_auth_dep)


@wan_router.get("/status")
def wan_get_status():
    """Etat live du daemon muros-wan-monitor (service_state + version).

    Le monitor probe les gateways listees et bascule la default route
    selon priorite. Version = version du paquet muros installe.
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
    # Pose la default dans la table dediee. Best-effort : si l'interface
    # n'est pas encore prete, le monitor reposera la default sur le
    # prochain probe up.
    try:
        net.wan_set_table_default(g.id, iface.name, g.gateway)
    except Exception:
        pass
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
    # Le changement d'interface ou de gateway-ip invalide la table dediee.
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
        except Exception:
            pass
    _sync_monitor_service(db)
    return _to_out(g)


@wan_router.delete("/gateways/{gw_id}", status_code=204)
def delete_gateway(gw_id: int, db: Session = Depends(get_db)):
    g = db.query(WanGateway).filter(WanGateway.id == gw_id).first()
    if not g:
        raise HTTPException(404, "Unknown WAN gateway")
    # On nettoie la table dediee avant le drop DB. Si ce WAN portait la
    # default globale au moment du delete, le monitor recalculera au
    # prochain tick (et basculera sur un autre WAN UP, ou supprimera la
    # default si plus aucun WAN n'est UP).
    try:
        net.wan_clear_table(g.id)
    except Exception:
        pass
    db.delete(g)
    db.commit()
    _sync_monitor_service(db)


@wan_router.get("/active", response_model=WanActiveOut)
def get_active(db: Session = Depends(get_db)):
    """WAN qui porte la default route en ce moment.

    Parmi les enabled+up, le plus prioritaire (plus petite valeur
    `priority`). Si aucun candidat, on indique `all_down` ou
    `no_gateway` selon le cas, pour aider le diag UI.
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
    """Force un probe immediat. Utile pour les tests / l'UI 'Test now'."""
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
    # Probe manuel : on ne touche pas aux compteurs anti-flap (geres par
    # le daemon). On expose juste le resultat brut via le status.
    g.status = "up" if ok else "down"
    db.commit()
    db.refresh(g)
    return _to_out(g)
