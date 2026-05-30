# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Notifications par mail SMTP.

Gere l'envoi d'alertes par mail aux destinataires configures. Le throttle
par event_type est gere ici (lecture du dernier envoi reussi dans la table
NotificationLog).

Event types reconnus (cle stable, utilise par le watcher) :
  - fail2ban_ban : une IP a ete bannie par fail2ban
  - ha_state_change : VRRP a change d'etat (MASTER/BACKUP/FAULT)
  - service_down : un service surveille est tombe, quel qu'il soit
    (backend, nginx, fail2ban, keepalived, strongswan, wg, conntrackd,
    snmpd, postfix, sshd, ntp/timesyncd, muros-watcher)
  - ipsec_sa_down : une SA IPsec n'est plus active
  - wireguard_peer_silent : un peer WG sans handshake depuis > 5 min
  - conntrack_high : table conntrack > 80% de remplissage
  - disk_high : /var > 80% d'utilisation
  - test : declenche manuellement depuis l'UI
"""
from __future__ import annotations

import logging
import smtplib
import socket
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from sqlalchemy.orm import Session

from app import models
log = logging.getLogger("muros.notifications")

# Canonical rule definitions. The watcher creates the missing ones at boot.
DEFAULT_RULES: list[tuple[str, str, int]] = [
    ("fail2ban_ban", "Fail2ban banned an IP address", 5),
    ("ha_state_change", "VRRP state change (MASTER/BACKUP/FAULT)", 1),
    ("service_down", "A monitored service went down (any)", 15),
    ("ipsec_sa_down", "IPsec tunnel not established", 15),
    ("wireguard_peer_silent", "WireGuard peer silent > 5 min", 30),
    ("wan_state_change", "WAN gateway state change (UP/DOWN)", 2),
    ("conntrack_high", "Conntrack table > 80% full", 60),
    ("disk_high", "/var > 80% used", 120),
    ("muros_update_available", "A new MurOS release is available on GitHub", 1440),
    ("test", "Test email from the UI (never throttled)", 0),
]


"""Event types deprecies qui doivent etre purges de la DB des installs
existantes. Liste codee en dur, agrandie a chaque suppression."""
OBSOLETE_EVENT_TYPES = (
    "service_down_secondary",  # fusionne dans service_down (2026-05-25)
)


def ensure_default_rules(db: Session) -> None:
    """Cree les regles par defaut manquantes, resynchronise les descriptions
    et purge les regles deprecies.

    Le statut enabled et le throttle_minutes sont conserves tels que
    saisis par l'utilisateur. Seule la description est mise a jour pour
    refleter le code (purement documentaire).
    """
    existing = {r.event_type: r for r in db.query(models.NotificationRule).all()}
    changed = False
    for event_type, description, throttle in DEFAULT_RULES:
        if event_type not in existing:
            db.add(models.NotificationRule(
                event_type=event_type,
                description=description,
                throttle_minutes=throttle,
                enabled=True,
            ))
            changed = True
        elif existing[event_type].description != description:
            existing[event_type].description = description
            changed = True
    for obsolete in OBSOLETE_EVENT_TYPES:
        if obsolete in existing:
            db.delete(existing[obsolete])
            changed = True
    if changed:
        db.commit()


def _get_config(db: Session) -> models.NotificationConfig:
    cfg = db.get(models.NotificationConfig, 1)
    if cfg is None:
        cfg = models.NotificationConfig(id=1)
        db.add(cfg)
        db.commit()
        db.refresh(cfg)
    return cfg


def _should_throttle(db: Session, event_type: str, throttle_minutes: int) -> bool:
    """True if an alert of the same type was sent successfully recently."""
    if throttle_minutes <= 0:
        return False
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=throttle_minutes)
    recent = (
        db.query(models.NotificationLog)
        .filter(models.NotificationLog.event_type == event_type)
        .filter(models.NotificationLog.success == True)  # noqa: E712
        .filter(models.NotificationLog.created_at >= cutoff)
        .first()
    )
    return recent is not None


def _send_smtp(cfg: models.NotificationConfig, subject: str, body: str) -> None:
    """Envoi SMTP brut. Leve une RuntimeError avec un message clair en cas d'echec."""
    if not cfg.smtp_host:
        raise RuntimeError("SMTP host non configure.")
    if not cfg.to_addrs:
        raise RuntimeError("Aucun destinataire configure.")

    msg = EmailMessage()
    msg["Subject"] = f"[MurOS] {subject}"
    msg["From"] = cfg.from_addr or "muros@localhost"
    msg["To"] = cfg.to_addrs
    hostname = socket.gethostname()
    full_body = f"{body}\n\n--\nMurOS firewall : {hostname}\n{datetime.now(timezone.utc).isoformat(timespec='seconds')}\n"
    msg.set_content(full_body)

    try:
        if cfg.use_tls and cfg.smtp_port == 465:
            smtp = smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port, timeout=15)
        else:
            smtp = smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=15)
            if cfg.use_tls:
                smtp.starttls()
        try:
            if cfg.smtp_user and cfg.smtp_password:
                smtp.login(cfg.smtp_user, cfg.smtp_password)
            smtp.send_message(msg)
        finally:
            try:
                smtp.quit()
            except Exception:  # noqa: BLE001
                pass
    except smtplib.SMTPRecipientsRefused as exc:
        detail = "; ".join(
            f"{addr} : {(msg_bytes or b'').decode('utf-8', 'replace').strip()}"
            for addr, (_code, msg_bytes) in exc.recipients.items()
        )
        raise RuntimeError(f"Destinataire refuse par le SMTP : {detail}") from exc
    except smtplib.SMTPSenderRefused as exc:
        raise RuntimeError(
            f"Expediteur refuse ({cfg.from_addr or 'muros@localhost'}) : "
            f"{exc.smtp_error.decode('utf-8', 'replace') if isinstance(exc.smtp_error, bytes) else exc.smtp_error}"
        ) from exc
    except smtplib.SMTPAuthenticationError as exc:
        raise RuntimeError(
            f"Authentification SMTP refusee : "
            f"{exc.smtp_error.decode('utf-8', 'replace') if isinstance(exc.smtp_error, bytes) else exc.smtp_error}"
        ) from exc
    except smtplib.SMTPConnectError as exc:
        raise RuntimeError(
            f"Connexion SMTP impossible vers {cfg.smtp_host}:{cfg.smtp_port} : {exc.smtp_error if hasattr(exc, 'smtp_error') else exc}"
        ) from exc
    except (ConnectionRefusedError, OSError) as exc:
        raise RuntimeError(
            f"Connexion impossible vers {cfg.smtp_host}:{cfg.smtp_port} : {exc}"
        ) from exc
    except smtplib.SMTPException as exc:
        raise RuntimeError(f"Erreur SMTP : {exc}") from exc


def notify(db: Session, event_type: str, subject: str, body: str, *, force: bool = False) -> dict:
    """Envoie une alerte si :
      - notifications activees globalement
      - regle correspondante existante et activee
      - throttle pas en cours (sauf force=True)
    Log toujours le tentative en DB.
    """
    cfg = _get_config(db)
    rule = (
        db.query(models.NotificationRule)
        .filter_by(event_type=event_type)
        .first()
    )

    if not cfg.enabled:
        return {"sent": False, "reason": "notifications globalement desactivees"}
    if rule is None:
        return {"sent": False, "reason": f"event_type '{event_type}' inconnu"}
    if not rule.enabled:
        return {"sent": False, "reason": "regle desactivee"}
    if not force and _should_throttle(db, event_type, rule.throttle_minutes):
        return {"sent": False, "reason": f"throttle ({rule.throttle_minutes} min)"}

    entry = models.NotificationLog(
        event_type=event_type, subject=subject, body=body,
        success=False, error=None,
    )
    db.add(entry)
    db.commit()

    try:
        _send_smtp(cfg, subject, body)
        entry.success = True
        entry.error = None
        db.commit()
        _rotate_log(db, keep=50)
        return {"sent": True}
    except Exception as exc:  # noqa: BLE001
        entry.success = False
        entry.error = str(exc)[:500]
        db.commit()
        log.warning("Echec envoi mail [%s] : %s", event_type, exc)
        return {"sent": False, "reason": f"erreur SMTP : {exc}"}


def _rotate_log(db: Session, keep: int = 50) -> None:
    """Supprime les vieux logs au-dela des N derniers."""
    ids = (
        db.query(models.NotificationLog.id)
        .order_by(models.NotificationLog.id.desc())
        .offset(keep)
        .all()
    )
    if ids:
        old_ids = [i[0] for i in ids]
        db.query(models.NotificationLog).filter(
            models.NotificationLog.id.in_(old_ids)
        ).delete(synchronize_session=False)
        db.commit()


def send_test(db: Session) -> dict:
    """Test send from the UI: force=True (no throttle)."""
    return notify(
        db, "test",
        subject="Mail de test",
        body="Si vous recevez ce message, la configuration SMTP de MurOS fonctionne.",
        force=True,
    )


# --- Postfix : status et installation ---

