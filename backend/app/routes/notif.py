# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Routes HTTP de l'API MurOS (sous-module)."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models, schemas, service_dirty
from app.auth import current_user
from app.db import get_db

_auth_dep = [Depends(current_user)]


# --- Notifications ---
notifications_router = APIRouter(
    prefix="/api/notifications", tags=["notifications"], dependencies=_auth_dep,
)


def _get_notif_config(db: Session) -> models.NotificationConfig:
    cfg = db.get(models.NotificationConfig, 1)
    if cfg is None:
        cfg = models.NotificationConfig(id=1)
        db.add(cfg)
        db.commit()
        db.refresh(cfg)
    return cfg


@notifications_router.get("/config", response_model=schemas.NotificationConfigOut)
def notif_get_config(db: Session = Depends(get_db)):
    return _get_notif_config(db)


@notifications_router.get("/status")
def notif_get_status():
    """Etat live du daemon muros-watcher (service_state + version paquet).

    Le watcher tourne en sidecar du backend : il poll periodiquement la
    DB et envoie un mail/webhook a chaque event nouveau. Version =
    version du paquet muros installe (le binaire est livre par le .deb).
    """
    from app.service_state import service_state, pkg_version
    return {
        "service_state": service_state("muros-watcher.service"),
        "version": pkg_version("muros", label="muros-watcher"),
    }


@notifications_router.put("/config", response_model=schemas.NotificationConfigOut)
def notif_update_config(data: schemas.NotificationConfigIn, db: Session = Depends(get_db)):
    cfg = _get_notif_config(db)
    for field, value in data.model_dump().items():
        setattr(cfg, field, value)
    db.commit()
    db.refresh(cfg)

    # The alert watcher (muros-watcher.service) follows the `cfg.enabled`
    # flag, which is the user switch on the page. For a long time we gated
    # on `smtp_host` only (the idea being: no channel -> no wakeup), but that
    # made the "Enable notifications" toggle silent for the admin: they
    # flipped the toggle and the watcher stayed down (smtp_host empty or not),
    # with no feedback. The watcher is light and idempotent, it logs even
    # without an SMTP channel, so we drive it directly from `enabled`. An
    # incomplete SMTP config will just make sending fail, which appears in
    # the log and stays visible.
    import subprocess
    import logging
    log = logging.getLogger(__name__)
    action = ["enable", "--now"] if cfg.enabled else ["disable", "--now"]
    try:
        r = subprocess.run(
            ["systemctl", *action, "muros-watcher.service"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0:
            # Non blocking for the save itself (the config is already
            # committed) but we want a trace when the unit refuses to
            # start (masked, missing, etc.) so support can debug.
            log.warning(
                "systemctl %s muros-watcher.service exited %s: %s",
                " ".join(action), r.returncode,
                (r.stderr or r.stdout or "").strip(),
            )
    except Exception as exc:  # noqa: BLE001 - non blocking for the save
        log.warning("Failed to %s muros-watcher.service: %s", " ".join(action), exc)

    return cfg


@notifications_router.post("/test", response_model=schemas.NotificationTestResult)
def notif_send_test(db: Session = Depends(get_db)):
    from app import notifications
    res = notifications.send_test(db)
    return {"sent": res.get("sent", False), "reason": res.get("reason")}


@notifications_router.get("/rules", response_model=list[schemas.NotificationRuleOut])
def notif_list_rules(db: Session = Depends(get_db)):
    from app import notifications
    notifications.ensure_default_rules(db)
    return db.query(models.NotificationRule).order_by(models.NotificationRule.id).all()


@notifications_router.put("/rules/{rule_id}", response_model=schemas.NotificationRuleOut)
def notif_update_rule(rule_id: int, data: schemas.NotificationRuleUpdate, db: Session = Depends(get_db)):
    rule = db.get(models.NotificationRule, rule_id)
    if rule is None:
        raise HTTPException(404, "Rule not found")
    rule.enabled = data.enabled
    rule.throttle_minutes = data.throttle_minutes
    db.commit()
    db.refresh(rule)
    return rule


@notifications_router.get("/log", response_model=list[schemas.NotificationLogOut])
def notif_get_log(db: Session = Depends(get_db), limit: int = 50):
    return (
        db.query(models.NotificationLog)
        .order_by(models.NotificationLog.id.desc())
        .limit(min(limit, 200))
        .all()
    )


# --- SNMP ---
snmp_router = APIRouter(prefix="/api/snmp", tags=["snmp"], dependencies=_auth_dep)


def _get_snmp_config(db: Session) -> models.SnmpConfig:
    cfg = db.get(models.SnmpConfig, 1)
    if cfg is None:
        cfg = models.SnmpConfig(id=1)
        db.add(cfg)
        db.commit()
        db.refresh(cfg)
    return cfg


@snmp_router.get("/status", response_model=schemas.SnmpStatus)
def snmp_status():
    from app import snmp
    return snmp.get_status()


@snmp_router.post("/install", response_model=schemas.SnmpInstallResult)
def snmp_install():
    from app import snmp
    try:
        return snmp.install_packages()
    except RuntimeError as exc:
        raise HTTPException(500, str(exc))


@snmp_router.get("/config", response_model=schemas.SnmpConfigOut)
def snmp_get_config(db: Session = Depends(get_db)):
    return _get_snmp_config(db)


@snmp_router.put("/config", response_model=schemas.SnmpConfigOut)
def snmp_update_config(data: schemas.SnmpConfigIn, db: Session = Depends(get_db)):
    """Save path: persist DB + write snmpd.conf.d/muros.conf + flag dirty.

    Does NOT restart snmpd. The page header Apply button (yellow dot
    when dirty) is the only path that calls snmp.reload().
    """
    from app import snmp
    cfg = _get_snmp_config(db)
    for field, value in data.model_dump().items():
        setattr(cfg, field, value)
    db.commit()
    db.refresh(cfg)
    try:
        snmp.write_conf(cfg)
    except RuntimeError as exc:
        raise HTTPException(500, str(exc))
    service_dirty.mark_dirty(db, "snmp", summary="SNMP config updated")
    return cfg


@snmp_router.get("/pending")
def snmp_pending(db: Session = Depends(get_db)):
    return service_dirty.get_state(db, "snmp")


@snmp_router.post("/apply", response_model=schemas.SnmpApplyResult)
def snmp_apply(db: Session = Depends(get_db)):
    """Apply path: reload snmpd then clear the dirty flag.

    write_conf has already been run by the preceding Save; we only
    restart the daemon here.
    """
    from app import snmp, ha_sync
    cfg = _get_snmp_config(db)
    try:
        res = snmp.reload(cfg)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(500, str(exc))
    service_dirty.mark_clean(db, "snmp", summary="snmpd reload")
    ha_sync.maybe_auto_push(db, triggered_by="snmp-apply")
    return res
