# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""MurOS backend API."""
import logging
import os
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.db import init_db, SessionLocal
from app.routes import (
    auth_router, users_router, zones_router, interfaces_router, firewall_router,
    nat_router, routes_router, network_router, logs_router, metrics_router,
    backups_router, ntp_router, dns_router, updates_router,
    hardening_router, backup_remote_router, pending_router,
    pending_apply_router, system_settings_router,
    ha_router, wireguard_router, ipsec_router,
    notifications_router, snmp_router,
    ha_sync_pub_router,
    tls_router, ssh_router, system_actions_router, http_router,
    diag_router,
    service_groups_router, address_groups_router,
    wan_router,
    dhcp_router, dns_services_router,
    service_apply_router,
    setup_router,
    ra_router,
    qos_router,
    syslog_router,
)
from app.routing import apply_all_routes, enable_ip_forwarding
from app.seed import (
    seed_root_user,
    seed_if_empty,
    seed_snmp_if_missing,
    seed_ssh_disabled_by_default,
    apply_snmp_if_enabled,
)

# Format unique de log :  [LEVEL] logger.name : message
# Sous systemd (journalctl) le timestamp est ajoute automatiquement, on
# l'omet pour ne pas le dupliquer.
#
# Niveau ajustable via MUROS_LOG (env) : DEBUG, INFO, WARNING, ERROR. Defaut
# INFO. En DEBUG, on voit les dry-run de toutes les commandes ip/nft, utile
# pour diagnostiquer un apply qui ne fait pas ce qu'on attend.
_log_level = os.environ.get("MUROS_LOG", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, _log_level, logging.INFO),
    format="[%(levelname)s] %(name)s : %(message)s",
)

# Per-module levels: some are verbose by nature (wireguard poll every 5s,
# conntrack sync...), so we raise them to WARNING by default to keep
# journalctl readable. The admin can drop everything back to DEBUG via
# MUROS_LOG=DEBUG. To override a single module without touching the global
# level, edit this dict and restart the service.
MODULE_LEVELS: dict[str, str] = {
    # Verbose: do not pollute journalctl by default.
    "muros.wireguard.poll": "WARNING",
    "muros.apply": "INFO",
    "muros.routing": "INFO",
    # SQLAlchemy logger: quiet by default (otherwise it prints every SQL
    # query at startup).
    "sqlalchemy.engine": "WARNING",
    "uvicorn.access": "WARNING",
}
for _name, _lvl in MODULE_LEVELS.items():
    logging.getLogger(_name).setLevel(getattr(logging, _lvl, logging.INFO))

log = logging.getLogger("muros")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("MurOS API starting (version %s)", __version__)
    init_db()
    enable_ip_forwarding()
    with SessionLocal() as db:
        seed_root_user(db)
        # Automatic adoption of the network configuration on the very first
        # startup. If the DB is empty AND the marker /var/lib/muros/.adopted
        # is absent, we capture the kernel's active interfaces / IPs / routes.
        # This lets a backend started without muros-boot.service (dev case, or
        # install via pip / git clone) still adopt the existing state.
        # Idempotent: silent skip if already done.
        from app import adoption
        try:
            adoption.adopt_kernel_state(db)
        except Exception:
            log.exception("Initial adoption failed (non blocking)")
        seed_if_empty(db)
        seed_snmp_if_missing(db)
        seed_ssh_disabled_by_default(db)
        apply_all_routes(db)
        apply_snmp_if_enabled(db)
        # Clear false-positive dirty flags : if the operator clicked
        # Save then rebooted without clicking Apply, daemons like
        # Kea / unbound / snmpd load the saved conf at OS boot
        # anyway, so the dirty flag in the DB is stale. Compare the
        # rendered conf to the on-disk file and clear when they match.
        from app import service_dirty
        try:
            reconciled = service_dirty.reconcile_on_startup(db)
            if reconciled:
                log.info("service_dirty reconciled at startup : %s", reconciled)
        except Exception:
            log.exception("service_dirty reconcile_on_startup failed (non blocking)")
    # Rearm rollback timers for any pending_apply rows left over from a
    # prior process: nginx/sshd/tls/interface/route. Replaces the old
    # polling watcher thread, the unified rollback manager (app.rollback)
    # now owns the timers.
    from app import pending_apply, rollback as _rollback_mod
    try:
        restored = pending_apply.restore_pending_on_startup()
        if restored:
            log.info("pending_apply: %s rollback timer(s) rearmed", restored)
    except Exception:
        log.exception("pending_apply restore failed (non blocking)")
    try:
        restored = _rollback_mod.manager.restore_from_db()
        if restored:
            log.info("rollback: %s persistent ticket(s) restored", restored)
    except Exception:
        log.exception("rollback.restore_from_db failed (non blocking)")
    from app import updates as _updates_mod
    _updates_mod.ensure_updates_checker_started()
    log.info("MurOS API ready")
    yield
    log.info("MurOS API shutting down")


app = FastAPI(title="MurOS API", version=__version__, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


from app.audit import audit_middleware as _audit_middleware  # noqa: E402  (after app/CORS setup to avoid an import cycle)


@app.middleware("http")
async def audit_actions(request, call_next):
    """Trace les actions write dans la table audit_log."""
    return await _audit_middleware(request, call_next)


@app.middleware("http")
async def lock_writes_on_backup(request, call_next):
    """Reject writes on a node in VRRP BACKUP role.

    Exceptions:
      - GET / HEAD: reads always allowed
      - /api/auth/*: login/logout always allowed
      - /api/ha/sync/*: HA push/pull always allowed (this is what lets the
        MASTER push here)
      - /api/health, /api/system/info: healthcheck
    """
    method = request.method.upper()
    if method in ("GET", "HEAD", "OPTIONS"):
        return await call_next(request)
    path = request.url.path
    if (
        path.startswith("/api/auth/")
        or path.startswith("/api/ha/sync/")
        or path == "/api/ha/role"
        or path == "/api/health"
        or path == "/api/system/info"
    ):
        return await call_next(request)

    # Check the VRRP role via the ha_sync module (fast read of a /run file,
    # no DB I/O).
    try:
        from app import ha_sync
        if not ha_sync.is_writable_role():
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=423,
                content={
                    "detail": "This node is VRRP BACKUP. Changes must be made "
                              "on the MASTER node, they will be replicated "
                              "automatically.",
                },
            )
    except Exception:  # noqa: BLE001
        # If we cannot determine the role, let the request through
        # (no regression on installs without HA).
        pass
    return await call_next(request)


def _deb_version() -> str | None:
    """Read the version of the 'muros' deb package via dpkg-query (None outside install)."""
    import shutil
    import subprocess
    if not shutil.which("dpkg-query"):
        return None
    try:
        out = subprocess.run(
            ["dpkg-query", "-W", "-f=${Version}", "muros"],
            capture_output=True, text=True, timeout=2,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    if out.returncode != 0:
        return None
    v = (out.stdout or "").strip()
    return v or None


_BOOT_TIME = time.monotonic()


@app.get("/api/health")
def health():
    # Endpoint exposed without auth for external checks (Prometheus,
    # Centreon, load balancer). It gives everything needed to monitor
    # without logging in:
    #   - status: 'ok' as long as the API responds
    #   - version: deb version of the 'muros' package if installed, else module
    #   - apply_enabled: True if MUROS_APPLY=true (real effects), False otherwise
    #   - uptime_seconds: seconds since the backend process started
    from app.apply import APPLY_ENABLED
    return {
        "status": "ok",
        "version": _deb_version() or __version__,
        "apply_enabled": bool(APPLY_ENABLED),
        "uptime_seconds": int(time.monotonic() - _BOOT_TIME),
    }


@app.get("/api/system/info")
def system_info():
    import os
    import platform
    from app.apply import APPLY_ENABLED
    return {
        "hostname": platform.node(),
        "kernel": platform.release(),
        "arch": platform.machine(),
        "apply_enabled": APPLY_ENABLED,
        "is_root": os.geteuid() == 0,
    }


app.include_router(auth_router)
app.include_router(users_router)
app.include_router(zones_router)
app.include_router(interfaces_router)
app.include_router(firewall_router)
app.include_router(nat_router)
app.include_router(routes_router)
app.include_router(network_router)
app.include_router(logs_router)
app.include_router(metrics_router)
app.include_router(backups_router)
app.include_router(backup_remote_router)
app.include_router(ntp_router)
app.include_router(dns_router)
app.include_router(updates_router)
app.include_router(hardening_router)
app.include_router(system_settings_router)
app.include_router(pending_router)
app.include_router(pending_apply_router)
app.include_router(ha_router)
app.include_router(wireguard_router)
app.include_router(ipsec_router)
app.include_router(notifications_router)
app.include_router(snmp_router)
app.include_router(ha_sync_pub_router)
app.include_router(tls_router)
app.include_router(ssh_router)
app.include_router(system_actions_router)
app.include_router(http_router)
app.include_router(diag_router)
app.include_router(service_groups_router)
app.include_router(address_groups_router)
app.include_router(wan_router)
app.include_router(dhcp_router)
app.include_router(dns_services_router)
app.include_router(service_apply_router)
app.include_router(setup_router)
app.include_router(ra_router)
app.include_router(qos_router)
app.include_router(syslog_router)
