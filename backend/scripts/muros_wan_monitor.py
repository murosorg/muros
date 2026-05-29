#!/usr/bin/env python3
"""MurOS multi-WAN failover monitor.

Daemon ICMP qui probe chaque WAN gateway de la DB sur SON interface,
maintient un compteur d'echecs/succes consecutifs, et reecrit la default
route globale quand le WAN actif tombe (ou quand un WAN de meilleure
priorite revient).

Lance par systemd (muros-wan-monitor.service). Toutes les structures
runtime sont en DB pour que l'UI puisse les afficher en live.

Le choix de l'actif suit l'algo deterministe :
  - Filtrer enabled=True
  - Filtrer status='up'
  - Trier par priority ASC (le plus petit gagne)
  - Si la liste est vide -> 'all_down', on supprime la default globale
  - Sinon -> tete de liste

Ce choix est applique sans rate-limit : c'est notre boucle qui detecte
les changements, donc on peut convergir en <= interval_s.
"""
from __future__ import annotations

import logging
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from app.db import SessionLocal  # noqa: E402
from app.models import WanGateway  # noqa: E402
from app import network as net  # noqa: E402
from app import notifications as notif  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s muros-wan-monitor: %(message)s",
)
log = logging.getLogger("muros.wan_monitor")

# Sleep entre 2 ticks de la boucle. Le vrai interval_s est par gateway ;
# on tourne plus vite et on respecte interval_s en testant le delta sur
# last_probe_at. Permet d'avoir des gateways avec des intervals tres
# differents sans logique complexe.
LOOP_TICK_S = float(os.environ.get("MUROS_WAN_TICK", "1.0"))


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _to_aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    # SQLite renvoie des datetimes naive : on les force en UTC pour
    # eviter les comparaisons naive vs aware qui crashent.
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def apply_default_route(active: WanGateway | None) -> None:
    """Reecrit la default route globale en fonction du WAN actif.

    None = tous les WANs sont down. On supprime alors le default plutot
    que de laisser une route blackhole : un default vers un gateway
    injoignable consomme silencieusement le trafic au lieu de renvoyer
    une erreur 'no route to host' que les apps peuvent gerer.
    """
    if active is None:
        try:
            net.wan_remove_main_default()
            log.warning("all WANs down, default route removed")
        except Exception as e:
            log.error("failed to remove default route: %s", e)
        return
    if not active.interface:
        log.error("active gateway %s has no interface, skipping", active.name)
        return
    try:
        net.wan_set_main_default(active.interface.name, active.gateway)
        log.info(
            "default route via %s dev %s (gateway '%s', priority %d)",
            active.gateway, active.interface.name, active.name, active.priority,
        )
    except Exception as e:
        log.error("failed to set default route: %s", e)


def pick_active(gateways: list[WanGateway]) -> WanGateway | None:
    ups = [g for g in gateways if g.enabled and g.status == "up"]
    if not ups:
        return None
    ups.sort(key=lambda g: (g.priority, g.id))
    return ups[0]


def probe_and_update(g: WanGateway) -> bool:
    """Probe une gateway et met a jour ses compteurs en RAM (pas commit).

    Renvoie True si l'etat a change (up<->down), pour declencher la
    reconvergence + l'envoi de notification.
    """
    if not g.interface:
        return False
    try:
        ok = net.wan_probe(g.interface.name, g.monitoring_target, timeout_s=1.0)
    except ValueError as e:
        log.error("probe %s: invalid params: %s", g.name, e)
        return False
    g.last_probe_at = now_utc()
    prev = g.status
    if ok:
        g.consecutive_failures = 0
        g.consecutive_successes += 1
        # On passe up uniquement apres N succes consecutifs (anti-flap).
        # Pour le premier probe (status='unknown'), 1 succes suffit a
        # confirmer up : sinon on attendrait N*interval_s avant que
        # n'importe quel WAN soit considere comme utilisable.
        if prev != "up" and (prev == "unknown" or g.consecutive_successes >= g.failures_threshold):
            g.status = "up"
            g.last_change_at = now_utc()
            return True
    else:
        g.consecutive_successes = 0
        g.consecutive_failures += 1
        if prev != "down" and g.consecutive_failures >= g.failures_threshold:
            g.status = "down"
            g.last_change_at = now_utc()
            return True
    return False


def send_state_change_notif(g: WanGateway) -> None:
    """Envoie une notif WAN up/down a la SMTP de MurOS.

    `event_type` 'wan_state_change' : l'admin peut activer/desactiver la
    regle, et la throttle empeche un WAN flappant d'inonder la boite.
    """
    try:
        with SessionLocal() as db:
            verdict = "UP" if g.status == "up" else "DOWN"
            iface = g.interface.name if g.interface else "?"
            notif.notify(
                db,
                event_type="wan_state_change",
                subject=f"WAN {verdict}: {g.name} ({iface})",
                body=(
                    f"WAN gateway '{g.name}' is now {verdict}.\n\n"
                    f"Interface: {iface}\n"
                    f"Gateway IP: {g.gateway}\n"
                    f"Monitoring target: {g.monitoring_target}\n"
                    f"Priority: {g.priority}\n"
                    f"Consecutive {'failures' if verdict == 'DOWN' else 'successes'}: "
                    f"{g.consecutive_failures if verdict == 'DOWN' else g.consecutive_successes}\n"
                ),
            )
    except Exception as e:
        log.error("failed to send notification for %s: %s", g.name, e)


def tick() -> None:
    """Une iteration de la boucle : probe les gateways dont l'interval est
    ecoule, puis recalcule l'actif si quelque chose a change.
    """
    with SessionLocal() as db:
        gws = db.query(WanGateway).all()
        if not gws:
            return
        any_state_change = False
        now = now_utc()
        for g in gws:
            if not g.enabled:
                continue
            last = _to_aware(g.last_probe_at)
            if last is not None and now - last < timedelta(seconds=g.interval_s):
                continue
            changed = probe_and_update(g)
            if changed:
                any_state_change = True
                log.info(
                    "WAN '%s' state change -> %s (consecutive: f=%d s=%d)",
                    g.name, g.status, g.consecutive_failures, g.consecutive_successes,
                )
        db.commit()
        # Re-fetch frais apres commit pour la decision (necessaire si on a
        # croise d'autres commits depuis la lecture initiale, edge case).
        if any_state_change:
            fresh = db.query(WanGateway).all()
            active = pick_active(fresh)
            apply_default_route(active)
            # Notifications APRES routing : on prefere notifier l'admin
            # une fois que la bascule reseau est faite, pas l'inverse.
            for g in fresh:
                if g.last_change_at and _to_aware(g.last_change_at) and (
                    now - _to_aware(g.last_change_at) < timedelta(seconds=2)
                ):
                    send_state_change_notif(g)


def ensure_routing_tables() -> None:
    """Au demarrage du daemon, on (re)pose les defaults dans les tables
    dediees a chaque gateway. Utile apres un reboot ou un restart du
    daemon : muros-boot ne touche pas aux tables de WAN.
    """
    with SessionLocal() as db:
        for g in db.query(WanGateway).all():
            if not g.interface:
                continue
            try:
                net.wan_set_table_default(g.id, g.interface.name, g.gateway)
            except Exception as e:
                log.error("failed to seed table for %s: %s", g.name, e)


def main() -> None:
    log.info("muros-wan-monitor starting (tick=%.1fs)", LOOP_TICK_S)
    ensure_routing_tables()
    while True:
        try:
            tick()
        except Exception as e:
            # On NE crashe JAMAIS : un bug dans tick() ne doit pas tuer
            # le daemon, sinon plus de failover du tout. On loggue et on
            # retry au tick suivant.
            log.exception("tick failed: %s", e)
        time.sleep(LOOP_TICK_S)


if __name__ == "__main__":
    main()
