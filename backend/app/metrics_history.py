"""Collecte et stockage des metriques en SQLite.

Un thread daemon sample toutes les `INTERVAL_SECONDS` (defaut 60s) et
stocke en base. Les samples plus vieux que `RETENTION_HOURS` (defaut 24h)
sont supprimes a chaque ecriture.
"""
import logging
import os
import threading
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete

from app import metrics, models
from app.db import SessionLocal

log = logging.getLogger("muros.metrics_history")

INTERVAL_SECONDS = int(os.environ.get("MUROS_METRICS_INTERVAL", "60"))
RETENTION_HOURS = int(os.environ.get("MUROS_METRICS_RETENTION_HOURS", "24"))

_thread: threading.Thread | None = None
_stop_event = threading.Event()


def _collect_once() -> None:
    """Recolte un sample et l'ecrit en base. Nettoie les vieux samples."""
    now = datetime.now(timezone.utc)
    cpu = metrics.cpu_usage_percent()
    mem = metrics.memory_info()
    load = metrics.load_average()
    ct = metrics.conntrack_info()
    ifaces = metrics.interfaces_stats()

    with SessionLocal() as db:
        db.add(models.MetricSample(
            timestamp=now,
            cpu_usage_percent=cpu,
            memory_used_percent=mem["used_percent"],
            memory_used_bytes=mem["used_bytes"],
            conntrack_current=ct["current"],
            conntrack_used_percent=ct["used_percent"],
            load_1=load[0],
            load_5=load[1],
            load_15=load[2],
        ))
        for iface in ifaces:
            db.add(models.InterfaceSample(
                timestamp=now,
                interface_name=iface["name"],
                rx_bytes=iface["rx_bytes"],
                tx_bytes=iface["tx_bytes"],
                rx_packets=iface["rx_packets"],
                tx_packets=iface["tx_packets"],
            ))

        # Cleanup : supprime les samples plus vieux que la retention
        cutoff = now - timedelta(hours=RETENTION_HOURS)
        db.execute(delete(models.MetricSample).where(models.MetricSample.timestamp < cutoff))
        db.execute(delete(models.InterfaceSample).where(models.InterfaceSample.timestamp < cutoff))
        db.commit()


def _run_loop() -> None:
    log.info("Collecteur metriques demarre (interval=%ds, retention=%dh)", INTERVAL_SECONDS, RETENTION_HOURS)
    # Premiere collecte immediate pour amorcer le calcul CPU (besoin de 2 samples)
    try:
        metrics.cpu_usage_percent()
    except Exception:
        pass
    while not _stop_event.is_set():
        try:
            _collect_once()
        except Exception as e:
            log.warning("Echec collecte metriques: %s", e)
        if _stop_event.wait(INTERVAL_SECONDS):
            break
    log.info("Collecteur metriques arrete")


def start() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop_event.clear()
    _thread = threading.Thread(target=_run_loop, daemon=True, name="muros-metrics")
    _thread.start()


def stop() -> None:
    _stop_event.set()
