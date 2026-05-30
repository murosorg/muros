"""Audit log: record every write action performed through the UI.

FastAPI middleware that:
  - intercepts POST/PUT/PATCH/DELETE
  - lets GET/HEAD/OPTIONS through (reads, not recorded)
  - excludes very frequent polling (/api/health, /api/ha/role,
    /api/system/services)
  - extracts the user from the JWT Bearer token
  - records to the DB after the response with status_code and duration
  - auto rotation (keeps the last 5000 entries)

Used to render the Logs > 'Web actions' tab.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from collections.abc import Awaitable, Callable

from fastapi import Request, Response

from app.db import SessionLocal
from app import models

log = logging.getLogger("muros.audit")

# HTTP methods to audit.
AUDITED_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# Excluded endpoints (frequent polling, no operational interest).
EXCLUDED_PATHS_PREFIX = (
    "/api/health",
    "/api/system/services",
    "/api/system/info",
    "/api/ha/role",
    "/api/auth/me",
    "/api/auth/refresh",
    # HA sync polling (push/receive) between nodes is not a UI action.
    "/api/ha/sync/receive",
    "/api/ha/sync/ping",
)

# Map prefix -> human-readable action summary.
_ACTION_MAP: list[tuple[str, dict[str, str]]] = [
    ("/api/firewall/rules", {
        "POST": "Create firewall rule",
        "PUT": "Update firewall rule",
        "DELETE": "Delete firewall rule",
    }),
    ("/api/firewall/apply", {"POST": "Apply firewall configuration"}),
    ("/api/zones", {
        "POST": "Create zone", "PUT": "Update zone",
        "DELETE": "Delete zone",
    }),
    ("/api/interfaces", {
        "POST": "Create interface", "PUT": "Update interface",
        "DELETE": "Delete interface",
    }),
    ("/api/routes", {
        "POST": "Create route", "PUT": "Update route",
        "DELETE": "Delete route",
    }),
    ("/api/nat", {
        "POST": "Create NAT rule", "PUT": "Update NAT rule",
        "DELETE": "Delete NAT rule",
    }),
    ("/api/wireguard/peers", {
        "POST": "Create WireGuard peer", "PUT": "Update WireGuard peer",
        "DELETE": "Delete WireGuard peer",
    }),
    ("/api/wireguard/config", {"PUT": "Update WireGuard configuration"}),
    ("/api/wireguard/apply", {"POST": "Apply WireGuard configuration"}),
    ("/api/wireguard/install", {"POST": "Install WireGuard"}),
    ("/api/ipsec/connections", {
        "POST": "Create IPsec connection", "PUT": "Update IPsec connection",
        "DELETE": "Delete IPsec connection",
    }),
    ("/api/ipsec/apply", {"POST": "Apply IPsec configuration"}),
    ("/api/ipsec/install", {"POST": "Install StrongSwan"}),
    ("/api/ipsec/ca", {"POST": "Generate IPsec CA"}),
    ("/api/ipsec/certs", {
        "POST": "Generate IPsec certificate", "DELETE": "Revoke IPsec certificate",
    }),
    ("/api/ha/sync/config", {"PUT": "Update HA sync configuration"}),
    ("/api/ha/sync/push", {"POST": "Manual HA push"}),
    ("/api/ha/install", {"POST": "Install HA (keepalived/conntrackd)"}),
    ("/api/ha", {
        "POST": "Create HA entry", "PUT": "Update HA",
        "DELETE": "Delete HA",
    }),
    ("/api/notifications/config", {"PUT": "Update notifications configuration"}),
    ("/api/notifications/test", {"POST": "Send test notification"}),
    ("/api/snmp/config", {"PUT": "Update SNMP configuration"}),
    ("/api/snmp/apply", {"POST": "Apply SNMP configuration"}),
    ("/api/snmp/install", {"POST": "Install SNMP"}),
    ("/api/tls/upload", {"POST": "Upload UI TLS certificate"}),
    ("/api/tls/regenerate-self-signed", {"POST": "Regenerate self-signed TLS certificate"}),
    ("/api/ssh/config", {"PUT": "Update SSH configuration"}),
    ("/api/ssh/apply", {"POST": "Apply SSH configuration"}),
    ("/api/ssh/install", {"POST": "Install sshd"}),
    ("/api/ssh/keys", {
        "POST": "Add authorized SSH key", "DELETE": "Remove authorized SSH key",
    }),
    ("/api/http/config", {"PUT": "Update nginx HTTP configuration"}),
    ("/api/http/apply", {"POST": "Apply nginx HTTP configuration"}),
    ("/api/system/reboot", {"POST": "Reboot the firewall"}),
    ("/api/system/shutdown", {"POST": "Shut down the firewall"}),
    ("/api/auth/change-password", {"POST": "Change password"}),
    ("/api/auth/login", {"POST": "UI login"}),
    ("/api/auth/logout", {"POST": "UI logout"}),
    ("/api/backups", {
        "POST": "Create backup", "DELETE": "Delete backup",
    }),
    ("/api/dns", {"PUT": "Update DNS"}),
    ("/api/ntp", {"PUT": "Update NTP"}),
    ("/api/hostname", {"PUT": "Update hostname"}),
]


def _action_summary(method: str, path: str) -> str:
    """Build a human-readable label from the path + method."""
    for prefix, methods in _ACTION_MAP:
        if path.startswith(prefix) and method in methods:
            return methods[method]
    return f"{method} {path}"


def _extract_user(request: Request) -> tuple[int | None, str | None]:
    """Extract (user_id, username) from the JWT in the Authorization header."""
    auth_hdr = request.headers.get("Authorization", "")
    if not auth_hdr.startswith("Bearer "):
        return None, None
    token = auth_hdr[len("Bearer "):].strip()
    try:
        import jwt
        from app.auth import JWT_SECRET, JWT_ALGO
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
        uid = payload.get("sub")
        if isinstance(uid, str) and uid.isdigit():
            uid = int(uid)
        return uid if isinstance(uid, int) else None, payload.get("username")
    except Exception:  # noqa: BLE001
        return None, None


def _client_ip(request: Request) -> str | None:
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip()
    real = request.headers.get("X-Real-IP", "")
    if real:
        return real.strip()
    if request.client:
        return request.client.host
    return None


async def audit_middleware(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    method = request.method.upper()
    path = request.url.path

    # Do not audit GET/HEAD/OPTIONS (reads) and exclusions.
    if method not in AUDITED_METHODS:
        return await call_next(request)
    if any(path.startswith(p) for p in EXCLUDED_PATHS_PREFIX):
        return await call_next(request)
    # We only audit /api/* routes.
    if not path.startswith("/api/"):
        return await call_next(request)

    started = time.time()
    user_id, username = _extract_user(request)
    ip = _client_ip(request)

    response = await call_next(request)

    duration_ms = int((time.time() - started) * 1000)

    # If the user could not be read up front (e.g. POST /api/auth/login),
    # we could try afterwards: on a successful login the username is in the
    # body... Too complex for V1, we record what we have.

    # Persist to the DB best-effort. If the DB is busy, we log a warning
    # and continue (never blocks the response).
    try:
        with SessionLocal() as db:
            entry = models.AuditLog(
                timestamp=datetime.now(timezone.utc).replace(tzinfo=None),
                user_id=user_id,
                username=username,
                method=method,
                path=path,
                status_code=response.status_code,
                client_ip=ip,
                duration_ms=duration_ms,
                action_summary=_action_summary(method, path),
            )
            db.add(entry)
            db.commit()
            # Rotation: keep the last 5000 entries.
            _rotate(db, keep=5000)
    except Exception as exc:  # noqa: BLE001
        log.warning("audit log write failed: %s", exc)

    return response


def _rotate(db, keep: int = 5000) -> None:
    """Delete entries beyond the last N (by id desc)."""
    ids = (
        db.query(models.AuditLog.id)
        .order_by(models.AuditLog.id.desc())
        .offset(keep)
        .all()
    )
    if ids:
        old_ids = [i[0] for i in ids]
        db.query(models.AuditLog).filter(
            models.AuditLog.id.in_(old_ids)
        ).delete(synchronize_session=False)
        db.commit()
