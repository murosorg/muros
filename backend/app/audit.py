"""Audit log : trace toutes les actions d'ecriture effectuees via l'UI.

Middleware FastAPI qui :
  - intercepte POST/PUT/PATCH/DELETE
  - laisse passer les GET/HEAD/OPTIONS (consultation, pas trace)
  - exclut le polling tres frequent (/api/health, /api/ha/role,
    /api/system/services)
  - extrait le user du JWT Bearer
  - enregistre en DB apres response avec status_code et duree
  - rotation auto (garde les 5000 derniers)

Utilise au render pour la page Logs > onglet 'Actions web'.
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

# Methodes HTTP a auditer.
AUDITED_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# Endpoints exclus (polling frequent, pas d'interet operationnel).
EXCLUDED_PATHS_PREFIX = (
    "/api/health",
    "/api/system/services",
    "/api/system/info",
    "/api/ha/role",
    "/api/auth/me",
    "/api/auth/refresh",
    # Le polling sync HA (push/receive) entre noeuds n'est pas une action UI.
    "/api/ha/sync/receive",
    "/api/ha/sync/ping",
)

# Map prefix -> resume d'action lisible.
_ACTION_MAP: list[tuple[str, dict[str, str]]] = [
    ("/api/firewall/rules", {
        "POST": "Creation regle firewall",
        "PUT": "Modification regle firewall",
        "DELETE": "Suppression regle firewall",
    }),
    ("/api/firewall/apply", {"POST": "Application de la conf firewall"}),
    ("/api/zones", {
        "POST": "Creation zone", "PUT": "Modification zone",
        "DELETE": "Suppression zone",
    }),
    ("/api/interfaces", {
        "POST": "Creation interface", "PUT": "Modification interface",
        "DELETE": "Suppression interface",
    }),
    ("/api/routes", {
        "POST": "Creation route", "PUT": "Modification route",
        "DELETE": "Suppression route",
    }),
    ("/api/nat", {
        "POST": "Creation regle NAT", "PUT": "Modification regle NAT",
        "DELETE": "Suppression regle NAT",
    }),
    ("/api/wireguard/peers", {
        "POST": "Creation peer WireGuard", "PUT": "Modification peer WireGuard",
        "DELETE": "Suppression peer WireGuard",
    }),
    ("/api/wireguard/config", {"PUT": "Modification config WireGuard"}),
    ("/api/wireguard/apply", {"POST": "Application de la conf WireGuard"}),
    ("/api/wireguard/install", {"POST": "Installation WireGuard"}),
    ("/api/ipsec/connections", {
        "POST": "Creation connexion IPsec", "PUT": "Modification connexion IPsec",
        "DELETE": "Suppression connexion IPsec",
    }),
    ("/api/ipsec/apply", {"POST": "Application de la conf IPsec"}),
    ("/api/ipsec/install", {"POST": "Installation StrongSwan"}),
    ("/api/ipsec/ca", {"POST": "Generation CA IPsec"}),
    ("/api/ipsec/certs", {
        "POST": "Generation certificat IPsec", "DELETE": "Revocation certificat IPsec",
    }),
    ("/api/ha/sync/config", {"PUT": "Modification config sync HA"}),
    ("/api/ha/sync/push", {"POST": "Push HA manuel"}),
    ("/api/ha/install", {"POST": "Installation HA (keepalived/conntrackd)"}),
    ("/api/ha", {
        "POST": "Creation entree HA", "PUT": "Modification HA",
        "DELETE": "Suppression HA",
    }),
    ("/api/notifications/config", {"PUT": "Modification config notifications"}),
    ("/api/notifications/test", {"POST": "Test d'envoi notification"}),
    ("/api/snmp/config", {"PUT": "Modification config SNMP"}),
    ("/api/snmp/apply", {"POST": "Application config SNMP"}),
    ("/api/snmp/install", {"POST": "Installation SNMP"}),
    ("/api/tls/upload", {"POST": "Upload certificat TLS UI"}),
    ("/api/tls/regenerate-self-signed", {"POST": "Regeneration cert TLS auto-signe"}),
    ("/api/ssh/config", {"PUT": "Modification config SSH"}),
    ("/api/ssh/apply", {"POST": "Application config SSH"}),
    ("/api/ssh/install", {"POST": "Installation sshd"}),
    ("/api/ssh/keys", {
        "POST": "Ajout cle SSH autorisee", "DELETE": "Suppression cle SSH autorisee",
    }),
    ("/api/http/config", {"PUT": "Modification config HTTP nginx"}),
    ("/api/http/apply", {"POST": "Application config HTTP nginx"}),
    ("/api/system/reboot", {"POST": "Redemarrage du firewall"}),
    ("/api/system/shutdown", {"POST": "Arret du firewall"}),
    ("/api/auth/change-password", {"POST": "Changement mot de passe"}),
    ("/api/auth/login", {"POST": "Connexion UI"}),
    ("/api/auth/logout", {"POST": "Deconnexion UI"}),
    ("/api/backups", {
        "POST": "Creation backup", "DELETE": "Suppression backup",
    }),
    ("/api/dns", {"PUT": "Modification DNS"}),
    ("/api/ntp", {"PUT": "Modification NTP"}),
    ("/api/hostname", {"PUT": "Modification hostname"}),
]


def _action_summary(method: str, path: str) -> str:
    """Tente de donner un libelle lisible a partir du path + method."""
    for prefix, methods in _ACTION_MAP:
        if path.startswith(prefix) and method in methods:
            return methods[method]
    return f"{method} {path}"


def _extract_user(request: Request) -> tuple[int | None, str | None]:
    """Extrait (user_id, username) du JWT dans le header Authorization."""
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

    # Ne pas auditer GET/HEAD/OPTIONS (consultation) et exclusions.
    if method not in AUDITED_METHODS:
        return await call_next(request)
    if any(path.startswith(p) for p in EXCLUDED_PATHS_PREFIX):
        return await call_next(request)
    # On audite uniquement les routes /api/*.
    if not path.startswith("/api/"):
        return await call_next(request)

    started = time.time()
    user_id, username = _extract_user(request)
    ip = _client_ip(request)

    response = await call_next(request)

    duration_ms = int((time.time() - started) * 1000)

    # Si le user n'a pas pu etre lu en debut (ex: POST /api/auth/login),
    # on essaye apres : sur login reussi on a le username dans le body...
    # Trop complique pour V1, on note ce qu'on a.

    # Persist en DB en best-effort. Si la DB est busy, on log un warning
    # et on continue (ne bloque jamais la response).
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
            # Rotation : garde les 5000 derniers.
            _rotate(db, keep=5000)
    except Exception as exc:  # noqa: BLE001
        log.warning("audit log write failed : %s", exc)

    return response


def _rotate(db, keep: int = 5000) -> None:
    """Supprime les entrees au-dela des N derniers (par id desc)."""
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
