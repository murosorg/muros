# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Application des routes statiques et de l'IP forwarding.

Mode dry-run par defaut (rien n'est touche sur le systeme).
Active par MUROS_APPLY=true sur la cible.
"""
import logging
import os
import subprocess
from pathlib import Path

from sqlalchemy.orm import Session, joinedload

from app import models

log = logging.getLogger("muros.routing")

APPLY_ENABLED = os.environ.get("MUROS_APPLY", "false").lower() == "true"
SYSCTL_FILE = Path(os.environ.get("MUROS_SYSCTL", "/etc/sysctl.d/99-muros.conf"))



def _run(args: list[str]) -> tuple[int, str]:
    if not APPLY_ENABLED:
        log.debug("DRY-RUN routing: %s", " ".join(args))
        return 0, ""
    try:
        res = subprocess.run(args, capture_output=True, text=True, timeout=5)
        return res.returncode, (res.stdout + res.stderr).strip()
    except (subprocess.SubprocessError, FileNotFoundError) as e:
        return 1, str(e)


def enable_ip_forwarding() -> None:
    """Active le forwarding IPv4 et IPv6 (runtime + persistance)."""
    if APPLY_ENABLED:
        try:
            Path("/proc/sys/net/ipv4/ip_forward").write_text("1\n")
            Path("/proc/sys/net/ipv6/conf/all/forwarding").write_text("1\n")
        except OSError as e:
            log.warning("Impossible d'activer le forwarding runtime: %s", e)

        try:
            SYSCTL_FILE.parent.mkdir(parents=True, exist_ok=True)
            SYSCTL_FILE.write_text(
                "# Genere par MurOS, ne pas editer manuellement\n"
                "net.ipv4.ip_forward=1\n"
                "net.ipv6.conf.all.forwarding=1\n"
            )
        except OSError as e:
            log.warning("Impossible d'ecrire %s: %s", SYSCTL_FILE, e)
    else:
        log.debug("DRY-RUN: enable_ip_forwarding (sysctl + /proc)")


def _route_to_args(route: models.StaticRoute, action: str) -> list[str] | None:
    """Convertit une route en arguments `ip route add|del|replace ...`."""
    if not route.destination:
        return None
    args = ["ip", "route", action, route.destination]
    if route.gateway:
        args += ["via", route.gateway]
    if route.interface and route.interface.name:
        args += ["dev", route.interface.name]
    if route.metric:
        args += ["metric", str(route.metric)]
    return args


def apply_route(route: models.StaticRoute, action: str) -> tuple[int, str]:
    """Applique ou retire une route en runtime via `ip route add|del`."""
    args = _route_to_args(route, action)
    if not args:
        return 1, "route incomplete"
    return _run(args)


def apply_all_routes(db: Session) -> None:
    """Applique toutes les routes activees (au demarrage par exemple)."""
    routes = (
        db.query(models.StaticRoute)
        .options(joinedload(models.StaticRoute.interface))
        .filter(models.StaticRoute.enabled.is_(True))
        .all()
    )
    for r in routes:
        rc, msg = apply_route(r, "replace")
        if rc != 0 and msg:
            log.warning("Echec route %s: %s", r.destination, msg)
    # Pas de fichier de persistance : muros-boot relit la DB et rejoue
    # apply_all_routes au boot, source unique = DB SQLite.

