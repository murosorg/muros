# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Routes HTTP de l'API MurOS (sous-module)."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app import models, schemas
from app.auth import current_user
from app.db import get_db
from app.metrics import (
    conntrack_info, cpu_cores, cpu_usage_percent, disks_info,
    interfaces_stats, load_average, memory_info, swap_info, uptime_seconds,
)
from app.metrics_history import RETENTION_HOURS as METRICS_RETENTION_HOURS

from .network_fw import nat_router

_auth_dep = [Depends(current_user)]

# --- Logs ---
logs_router = APIRouter(prefix="/api/logs", tags=["logs"], dependencies=_auth_dep)


@logs_router.get("/firewall", response_model=list[schemas.FirewallLogEntryOut])
def get_firewall_logs(
    limit: int = 200,
    search: str | None = None,
    scope: str = "muros",
):
    from app.logs import read_firewall_logs as _read
    try:
        return _read(limit=limit, search=search, scope=scope)
    except RuntimeError as exc:
        raise HTTPException(500, str(exc))


@logs_router.get("/audit", response_model=list[schemas.AuditLogOut])
def logs_audit(
    db: Session = Depends(get_db),
    limit: int = 200,
    method: str | None = None,
    username: str | None = None,
    contains: str | None = None,
):
    """Liste les dernieres actions UI tracees dans audit_log."""
    q = db.query(models.AuditLog).order_by(models.AuditLog.id.desc())
    if method:
        q = q.filter(models.AuditLog.method == method.upper())
    if username:
        q = q.filter(models.AuditLog.username == username)
    if contains:
        like = f"%{contains}%"
        q = q.filter(
            (models.AuditLog.path.like(like)) |
            (models.AuditLog.action_summary.like(like))
        )
    return q.limit(min(limit, 1000)).all()


@logs_router.get("/status", response_model=schemas.LogsStatusOut)
def get_logs_status_route(db: Session = Depends(get_db)):
    from app.logs import get_logs_status
    return get_logs_status(db)


@logs_router.get("/system", response_model=list[schemas.SystemLogEntryOut])
def get_system_logs(
    unit: str = "muros-backend.service",
    limit: int = 200,
    since_minutes: int | None = None,
    search: str | None = None,
    priority: str | None = None,
):
    """Lit le journald d'un service systemd (whitelist d'units)."""
    from app.logs import read_system_logs
    try:
        return read_system_logs(unit, limit, since_minutes, search, priority)
    except RuntimeError as exc:
        raise HTTPException(500, str(exc))


@logs_router.get("/system/units", response_model=list[str])
def get_system_log_units():
    from app.logs import list_known_units
    return list_known_units()


# --- Metrics ---
metrics_router = APIRouter(prefix="/api/metrics", tags=["metrics"], dependencies=_auth_dep)


@metrics_router.get("/summary", response_model=schemas.MetricsSummaryOut)
def metrics_summary():
    return schemas.MetricsSummaryOut(
        timestamp=datetime.now(timezone.utc),
        cpu_usage_percent=cpu_usage_percent(),
        cpu_cores=cpu_cores(),
        memory=memory_info(),
        swap=swap_info(),
        load=load_average(),
        uptime_seconds=uptime_seconds(),
        disks=disks_info(),
        interfaces=interfaces_stats(),
        conntrack=conntrack_info(),
    )


@metrics_router.get("/history", response_model=schemas.MetricsHistoryOut)
def metrics_history(hours: int = 24, db: Session = Depends(get_db)):
    """Retourne l'historique des metriques stocke en base.

    hours : fenetre temporelle (1 a la retention configuree).
    """
    hours = max(1, min(hours, METRICS_RETENTION_HOURS))
    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    samples = (
        db.query(models.MetricSample)
        .filter(models.MetricSample.timestamp >= since)
        .order_by(models.MetricSample.timestamp.asc())
        .all()
    )
    iface_samples = (
        db.query(models.InterfaceSample)
        .filter(models.InterfaceSample.timestamp >= since)
        .order_by(models.InterfaceSample.timestamp.asc())
        .all()
    )

    interfaces: dict[str, list[schemas.InterfaceSamplePoint]] = {}
    for s in iface_samples:
        interfaces.setdefault(s.interface_name, []).append(
            schemas.InterfaceSamplePoint.model_validate(s)
        )

    return schemas.MetricsHistoryOut(
        samples=[schemas.MetricSamplePoint.model_validate(s) for s in samples],
        interfaces=interfaces,
        retention_hours=METRICS_RETENTION_HOURS,
    )


@nat_router.get("/rules", response_model=list[schemas.NatRuleOut])
def list_nat(db: Session = Depends(get_db)):
    return (
        db.query(models.NatRule)
        .order_by(models.NatRule.position, models.NatRule.id)
        .all()
    )


@nat_router.post("/rules", response_model=schemas.NatRuleOut, status_code=status.HTTP_201_CREATED)
def create_nat(data: schemas.NatRuleCreate, db: Session = Depends(get_db)):
    if data.interface_id and not db.get(models.Interface, data.interface_id):
        raise HTTPException(400, "invalid interface_id")
    rule = models.NatRule(**data.model_dump(), dirty=True)
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


@nat_router.put("/rules/{rule_id}", response_model=schemas.NatRuleOut)
def update_nat(rule_id: int, data: schemas.NatRuleUpdate, db: Session = Depends(get_db)):
    rule = db.get(models.NatRule, rule_id)
    if not rule:
        raise HTTPException(404, "NAT rule not found")
    payload = data.model_dump(exclude_unset=True)
    if "interface_id" in payload and payload["interface_id"] is not None:
        if not db.get(models.Interface, payload["interface_id"]):
            raise HTTPException(400, "invalid interface_id")
    # Only flag dirty when a value actually changes (see update_rule).
    changed = False
    for k, v in payload.items():
        if getattr(rule, k) != v:
            setattr(rule, k, v)
            changed = True
    if changed:
        rule.dirty = True
    db.commit()
    db.refresh(rule)
    return rule


@nat_router.delete("/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_nat(rule_id: int, db: Session = Depends(get_db)):
    rule = db.get(models.NatRule, rule_id)
    if not rule:
        raise HTTPException(404, "NAT rule not found")
    db.delete(rule)
    # Global singleton flag: covers also the case where this was the
    # last NAT rule (nothing else to flag with the per-row dirty).
    from app.routes.network_fw import mark_firewall_dirty
    mark_firewall_dirty(db)
    db.commit()


@nat_router.post("/rules/reorder", response_model=list[schemas.NatRuleOut])
def reorder_nat(payload: schemas.NatReorderIn, db: Session = Depends(get_db)):
    """Renumerote les positions des regles NAT en multiples de 10.

    Apres drag-and-drop dans l'UI, le front envoie l'ordre desire sous
    forme d'une liste d'IDs. On reaffecte position = 10, 20, 30...
    L'ordre compte car nft applique en sequence et la premiere match
    gagne (DNAT/SNAT/MASQUERADE sont evalues dans l'ordre de la chaine).
    """
    requested_ids = list(payload.rule_ids)
    existing = db.query(models.NatRule).all()
    by_id = {r.id: r for r in existing}
    if set(requested_ids) != set(by_id.keys()):
        raise HTTPException(
            400,
            "rule_ids must be exactly the existing NAT rules "
            f"({sorted(by_id.keys())} expected, got {sorted(requested_ids)})",
        )
    for index, rid in enumerate(requested_ids):
        new_pos = (index + 1) * 10
        if by_id[rid].position != new_pos:
            by_id[rid].position = new_pos
            by_id[rid].dirty = True
    db.commit()
    out = (
        db.query(models.NatRule)
        .order_by(models.NatRule.position, models.NatRule.id)
        .all()
    )
    return out


# --- Backups ---
backups_router = APIRouter(prefix="/api/backups", tags=["backups"], dependencies=_auth_dep)


@backups_router.get("", response_model=list[schemas.BackupOut])
def list_backups():
    from app import backups
    return backups.list_backups()


@backups_router.post("", response_model=schemas.BackupOut, status_code=status.HTTP_201_CREATED)
def create_backup(data: schemas.BackupCreateRequest):
    from app import backups
    return backups.create_backup(label=data.label)


@backups_router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT)
def delete_backup(name: str):
    from app import backups
    try:
        backups.delete_backup(name)
    except FileNotFoundError:
        raise HTTPException(404, "Backup not found")
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@backups_router.post("/{name}/restore", response_model=schemas.BackupRestoreResult)
def restore_backup(name: str):
    from app import backups
    try:
        return backups.restore_backup(name)
    except FileNotFoundError:
        raise HTTPException(404, "Backup not found")
    except ValueError as exc:
        raise HTTPException(400, str(exc))


# --- NTP ---
ntp_router = APIRouter(prefix="/api/ntp", tags=["ntp"], dependencies=_auth_dep)


@ntp_router.get("/status", response_model=schemas.NtpStatusOut)
def ntp_status():
    from app import ntp
    return ntp.get_status()


@ntp_router.get("/servers", response_model=schemas.NtpServersOut)
def ntp_servers(db: Session = Depends(get_db)):
    from app import ntp
    cfg = ntp.get_config(db)
    return schemas.NtpServersOut(
        servers=ntp.get_servers(),
        config_path=ntp.get_config_path(),
        serve_lan=cfg.serve_lan,
    )


@ntp_router.put("/servers", response_model=schemas.NtpServersOut)
def ntp_set_servers(data: schemas.NtpServersIn, db: Session = Depends(get_db)):
    from app import ntp
    cfg = ntp.get_config(db)
    cfg.serve_lan = data.serve_lan
    db.commit()
    try:
        ntp.apply_config(db, servers=data.servers)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except RuntimeError as exc:
        raise HTTPException(400, str(exc))
    except OSError as exc:
        raise HTTPException(500, f"unable to write the config : {exc}")
    return schemas.NtpServersOut(
        servers=ntp.get_servers(),
        config_path=ntp.get_config_path(),
        serve_lan=cfg.serve_lan,
    )


# --- DNS ---
dns_router = APIRouter(prefix="/api/dns", tags=["dns"], dependencies=_auth_dep)


@dns_router.get("", response_model=schemas.DnsConfigOut)
def dns_get():
    from app import dns
    return dns.get_resolvers()


@dns_router.put("", response_model=schemas.DnsConfigOut)
def dns_set(data: schemas.DnsConfigIn):
    from app import dns
    try:
        return dns.set_resolvers(data.resolvers, data.search_domains)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except (OSError, RuntimeError) as exc:
        raise HTTPException(500, f"unable to write the config : {exc}")


# --- Updates ---
updates_router = APIRouter(prefix="/api/updates", tags=["updates"], dependencies=_auth_dep)


@updates_router.get("", response_model=schemas.UpdateStatusOut)
def updates_status():
    from app import updates
    return updates.get_status()


@updates_router.post("/check", response_model=schemas.UpdateStatusOut)
def updates_check():
    """Verif apt seule. Conserve pour retrocompat, l'UI utilise /check-all."""
    from app import updates
    try:
        return updates.check_updates()
    except RuntimeError as exc:
        raise HTTPException(500, str(exc))


@updates_router.post("/check-all")
def updates_check_all():
    """Verification unique des deux flux (apt + muros). Source de verite UI."""
    from app import updates
    try:
        return updates.check_all()
    except RuntimeError as exc:
        raise HTTPException(500, str(exc))


@updates_router.post("/install", response_model=schemas.UpdateInstallResult)
def updates_install():
    from app import updates
    try:
        return updates.install_updates()
    except RuntimeError as exc:
        raise HTTPException(500, str(exc))


@updates_router.get("/muros", response_model=schemas.MurosUpdateStatusOut)
def updates_muros_status():
    from app import updates
    return updates.get_muros_status()


@updates_router.post("/muros/install", response_model=schemas.UpdateInstallResult)
def updates_muros_install():
    from app import updates
    try:
        return updates.install_muros()
    except RuntimeError as exc:
        raise HTTPException(500, str(exc))


@updates_router.get("/muros/progress")
def updates_muros_progress():
    """Etat de l'upgrade auto-declenchee (unit systemd transient + log)."""
    from app import updates
    return updates.get_muros_install_progress()


_UNATTENDED_CONF = "/etc/muros/unattended.json"
_UNATTENDED_HELPER = "/usr/lib/muros/apply-unattended.sh"
_VALID_DAYS = {"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"}


def _read_unattended_conf() -> dict:
    """Read the persisted UI override if present."""
    import json
    from pathlib import Path
    p = Path(_UNATTENDED_CONF)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


@updates_router.get("/unattended")
def updates_unattended_status():
    """Expose the current unattended-upgrades schedule and toggles.

    Composes the persisted UI override (/etc/muros/unattended.json) with
    the live state of the systemd timer, so the UI always shows what is
    actually applied.
    """
    import subprocess
    from pathlib import Path

    def _systemd_show(unit: str, prop: str) -> str:
        try:
            r = subprocess.run(
                ["systemctl", "show", unit, f"--property={prop}", "--value"],
                capture_output=True, text=True, timeout=5,
            )
            return r.stdout.strip() if r.returncode == 0 else ""
        except (subprocess.SubprocessError, FileNotFoundError):
            return ""

    timer = "apt-daily-upgrade.timer"
    unit_state = _systemd_show(timer, "UnitFileState")
    enabled = unit_state in ("enabled", "static", "enabled-runtime") \
        and _systemd_show(timer, "ActiveState") == "active"
    next_run = _systemd_show(timer, "NextElapseUSecRealtime") or None
    last_run = _systemd_show(timer, "LastTriggerUSec") or None

    schedule = None
    dropin = Path("/etc/systemd/system/apt-daily-upgrade.timer.d/muros-schedule.conf")
    if dropin.exists():
        for line in dropin.read_text().splitlines():
            stripped = line.strip()
            if stripped.startswith("OnCalendar=") and stripped != "OnCalendar=":
                schedule = stripped.split("=", 1)[1].strip()
                break

    # User-facing structured fields, defaulted from the persisted JSON or
    # parsed back from the drop-in if no UI override exists yet.
    conf = _read_unattended_conf()
    days = conf.get("days") if isinstance(conf.get("days"), list) else None
    hour = conf.get("hour") if isinstance(conf.get("hour"), int) else None
    minute = conf.get("minute") if isinstance(conf.get("minute"), int) else None
    if (days is None or hour is None or minute is None) and schedule:
        # schedule looks like 'Mon,Wed 03:00'
        try:
            day_part, time_part = schedule.split()
            parsed_days = [d.strip() for d in day_part.split(",") if d.strip()]
            h, m = time_part.split(":")
            days = days or parsed_days
            hour = hour if hour is not None else int(h)
            minute = minute if minute is not None else int(m)
        except (ValueError, IndexError):
            pass

    return {
        "enabled": enabled,
        "schedule": schedule,
        "days": days or [],
        "hour": hour if hour is not None else 3,
        "minute": minute if minute is not None else 0,
        "next_run": next_run or None,
        "last_run": last_run if last_run and last_run != "n/a" else None,
        "excluded_packages": ["muros", "muros-*"],
    }


@updates_router.put("/unattended")
def updates_unattended_update(payload: dict):
    """Persist the UI override to /etc/muros/unattended.json and re-apply.

    Expected payload:
      {
        "enabled": true,
        "days": ["Mon", "Wed"],   # any subset of Mon..Sun
        "hour": 3,                # 0..23
        "minute": 0               # 0..59
      }
    """
    import json
    import subprocess
    from pathlib import Path

    enabled = bool(payload.get("enabled", True))
    days = payload.get("days") or []
    if not isinstance(days, list) or not all(d in _VALID_DAYS for d in days):
        raise HTTPException(400, "days must be a list of Mon..Sun")
    if not days:
        raise HTTPException(400, "at least one day must be selected")
    hour = payload.get("hour", 3)
    minute = payload.get("minute", 0)
    if not isinstance(hour, int) or not 0 <= hour <= 23:
        raise HTTPException(400, "hour must be 0..23")
    if not isinstance(minute, int) or not 0 <= minute <= 59:
        raise HTTPException(400, "minute must be 0..59")

    # Preserve canonical day ordering Mon..Sun for readability.
    ordered = [d for d in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun") if d in days]

    Path("/etc/muros").mkdir(mode=0o750, exist_ok=True)
    Path(_UNATTENDED_CONF).write_text(json.dumps({
        "enabled": enabled, "days": ordered, "hour": hour, "minute": minute,
    }, indent=2))

    try:
        r = subprocess.run([_UNATTENDED_HELPER], capture_output=True, text=True, timeout=15)
        if r.returncode != 0:
            raise HTTPException(500, f"apply-unattended failed: {r.stderr.strip() or r.stdout.strip()}")
    except FileNotFoundError:
        raise HTTPException(500, f"{_UNATTENDED_HELPER} not found (reinstall muros)")
    return updates_unattended_status()


@updates_router.get("/reboot-required")
def updates_reboot_required():
    """Expose /var/run/reboot-required state.

    Created by Debian packages (kernel, libc, dbus, etc.) after an upgrade
    that needs a reboot to take effect. unattended-upgrades never reboots
    on its own (Automatic-Reboot=false), so the admin must see the badge
    in the UI and pick a maintenance window.
    """
    from pathlib import Path
    flag = Path("/var/run/reboot-required")
    pkgs_file = Path("/var/run/reboot-required.pkgs")
    if not flag.exists():
        return {"required": False, "packages": []}
    pkgs: list[str] = []
    try:
        pkgs = sorted({line.strip() for line in pkgs_file.read_text().splitlines()
                       if line.strip()})
    except OSError:
        pass
    return {"required": True, "packages": pkgs}


@updates_router.post("/muros/repair")
def updates_muros_repair():
    """Repare un paquet muros laisse en etat dpkg incoherent."""
    from app import updates
    try:
        return updates.repair_muros_package()
    except RuntimeError as exc:
        raise HTTPException(500, str(exc))


# --- Hardening sysctl ---
hardening_router = APIRouter(prefix="/api/hardening", tags=["hardening"], dependencies=_auth_dep)


@hardening_router.get("", response_model=schemas.HardeningStatusOut)
def hardening_status():
    """Etat read-only des cles sysctl gerees par le drop-in MurOS.

    Le drop-in /etc/sysctl.d/99-muros-hardening.conf est livre par le paquet
    et applique au postinst (sysctl --system). L'admin ne peut pas le
    modifier depuis l'UI : c'est une garantie structurelle de l'appliance.
    Cet endpoint reste expose en lecture pour les checks de diagnostic.
    """
    from app import hardening
    return hardening.get_status()


# --- Backup distant ---
backup_remote_router = APIRouter(
    prefix="/api/backups/remote", tags=["backups"], dependencies=_auth_dep,
)


@backup_remote_router.get("", response_model=schemas.BackupRemoteConfig)
def backup_remote_get():
    from app import backups_remote
    return backups_remote.get_config()


@backup_remote_router.put("", response_model=schemas.BackupRemoteConfig)
def backup_remote_set(data: schemas.BackupRemoteConfigIn):
    from app import backups_remote
    try:
        return backups_remote.set_config(data.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except RuntimeError as exc:
        raise HTTPException(500, str(exc))


@backup_remote_router.post("/test", response_model=schemas.BackupRemoteTestResult)
def backup_remote_test(override: schemas.BackupRemoteConfigIn | None = None):
    from app import backups_remote
    payload = override.model_dump(exclude_unset=True) if override else None
    try:
        return backups_remote.test_connection(payload)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc))


@backup_remote_router.get("/ssh-key", response_model=schemas.SshKeyOut)
def backup_remote_get_key():
    from app import backups_remote
    return backups_remote.get_public_key()


@backup_remote_router.post("/ssh-key", response_model=schemas.SshKeyOut)
def backup_remote_generate_key(data: schemas.SshKeyGenerateRequest):
    from app import backups_remote
    try:
        return backups_remote.generate_ssh_key(force=data.force)
    except RuntimeError as exc:
        raise HTTPException(500, str(exc))


# --- System-wide settings (apply confirm timeout, etc.) ---
system_settings_router = APIRouter(
    prefix="/api/system/settings", tags=["system"], dependencies=_auth_dep,
)


@system_settings_router.get("")
def get_system_settings():
    """Read-only view of system-wide knobs exposed to the UI.

    Returns the current value, the default and the allowed choices so
    the front-end can render a select without having to hardcode the
    list (single source of truth).
    """
    from app import settings as app_settings
    return {
        "apply_confirm_timeout": {
            "value": app_settings.get_apply_confirm_timeout(),
            "default": app_settings.APPLY_CONFIRM_TIMEOUT_DEFAULT,
            "choices": list(app_settings.APPLY_CONFIRM_TIMEOUT_CHOICES),
        },
    }


@system_settings_router.put("/apply-confirm-timeout")
def set_apply_confirm_timeout(payload: dict):
    """Persist a new apply confirmation timeout in seconds.

    Accepts ``{"value": <int>}`` where ``value`` is one of the choices
    advertised by GET ``/api/system/settings``. Any other value is
    refused with 400 to keep the DB consistent.
    """
    from app import settings as app_settings
    raw = payload.get("value")
    if not isinstance(raw, int):
        raise HTTPException(400, "value must be an integer (seconds)")
    try:
        app_settings.set_apply_confirm_timeout(raw)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"value": app_settings.get_apply_confirm_timeout()}


# --- Pending changes (rollback temporise interfaces / routes) ---
pending_router = APIRouter(prefix="/api/pending", tags=["pending"], dependencies=_auth_dep)


@pending_router.get("", response_model=list[schemas.PendingChangeOut])
def list_pending():
    # The unified rollback manager now also holds tickets that belong
    # to /api/pending-apply (http/ssh/tls). Filter them out here so the
    # operator does not see the same change twice when polling both
    # endpoints. The kinds kept here match the historical contract of
    # /api/pending: network-level changes that go through safe_apply.
    from app import safe_apply
    _NETWORK_KINDS = {"interface", "route", "vlan", "nftables"}
    return [
        t for t in safe_apply.manager.list_pending()
        if t.get("kind") in _NETWORK_KINDS
    ]


@pending_router.post("/{pid}/confirm", response_model=schemas.PendingChangeOut)
def confirm_pending(pid: str):
    from app import safe_apply
    try:
        return safe_apply.manager.confirm(pid).to_public()
    except KeyError:
        raise HTTPException(404, "Pending change not found")
    except RuntimeError as exc:
        raise HTTPException(400, str(exc))


@pending_router.post("/{pid}/rollback", response_model=schemas.PendingChangeOut)
def rollback_pending(pid: str):
    from app import safe_apply
    try:
        return safe_apply.manager.rollback(pid, automatic=False).to_public()
    except KeyError:
        raise HTTPException(404, "Pending change not found")


# --- Pending apply (DB-backed) : http, ssh, tls, interface, route ---
# Endpoint unifie pour que la modale RollbackModal cote frontend voie d'un
# coup tous les changements en attente, peu importe leur source.
pending_apply_router = APIRouter(
    prefix="/api/pending-apply", tags=["pending-apply"], dependencies=_auth_dep,
)


def _pending_apply_to_public(entry) -> dict:
    return {
        "id": entry.id,
        "apply_type": entry.apply_type,
        "status": entry.status,
        "summary": entry.new_config_summary,
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
        "expires_at": entry.expires_at.isoformat() if entry.expires_at else None,
        "timeout_seconds": entry.timeout_seconds,
        "rollback_error": entry.rollback_error,
    }


@pending_apply_router.get("")
def list_pending_apply():
    from app import pending_apply
    return [_pending_apply_to_public(e) for e in pending_apply.list_pending()
            if e.status == "pending"]


@pending_apply_router.post("/{pid}/confirm")
def confirm_pending_apply(pid: int):
    from app import pending_apply
    try:
        return _pending_apply_to_public(pending_apply.confirm(pid))
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@pending_apply_router.post("/{pid}/rollback")
def rollback_pending_apply(pid: int):
    from app import pending_apply
    try:
        return _pending_apply_to_public(pending_apply.rollback_now(pid))
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@backups_router.post("/{name}/push", response_model=schemas.BackupPushResult)
def backup_push(name: str):
    from app import backups_remote
    try:
        return backups_remote.push_backup(name)
    except FileNotFoundError:
        raise HTTPException(404, "Backup not found")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except RuntimeError as exc:
        raise HTTPException(500, str(exc))


