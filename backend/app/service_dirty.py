# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Shared dirty-state tracking for managed system services.

A "service" here is a logical page in the UI that owns one systemd
daemon (or a small cluster of related daemons, like keepalived +
conntrackd for HA). Examples: 'dhcp', 'dns', 'snmp', 'wireguard',
'ipsec', 'ha', 'ssh', 'http', 'notifications'.

Pattern enforced across the UI :
- Save in a form / modal writes the DB row(s) AND regenerates the
  on-disk config file. It does NOT touch systemd. After the write, the
  route calls `mark_dirty(db, "dhcp")`.
- The yellow Apply button in the page header polls `is_dirty()` via
  `GET /api/services/<name>/pending` and decorates itself with an
  orange dot when dirty.
- Clicking Apply hits `POST /api/services/<name>/apply` which calls
  the relevant `reload()` helper and then `mark_clean(db, "dhcp")`.

This module is intentionally tiny : a stable name -> dirty + timestamps
mapping kept in SQLite. Daemons that fail to reload should NOT clear
the dirty flag (the route is responsible for raising before
mark_clean).
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import ServiceApplyState, ServiceApplyLog


# Canonical list of service names. Used by the generic pending route
# to render a single payload describing every page in the UI. Keep in
# sync with the routes that wire mark_dirty / mark_clean.
KNOWN_SERVICES: tuple[str, ...] = (
    "dhcp",
    "dns",
    "snmp",
    "wireguard",
    "ipsec",
    "ha",
    "ssh",
    "http",
    "notifications",
    "qos",
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _get_or_create(db: Session, name: str) -> ServiceApplyState:
    row = db.get(ServiceApplyState, name)
    if row is None:
        row = ServiceApplyState(name=name, dirty=False)
        db.add(row)
        db.flush()
    return row


def _log(db: Session, name: str, action: str, actor=None, summary: str | None = None) -> None:
    """Append one row to service_apply_log. Best effort, never raises.

    `actor` accepts either a User SQLAlchemy row, a (id, username)
    tuple, or None. Keeping the signature loose avoids forcing routes
    to import the auth module just to log.
    """
    actor_id: int | None = None
    actor_username: str | None = None
    if actor is not None:
        try:
            actor_id = int(getattr(actor, "id", actor[0] if isinstance(actor, tuple) else None))
        except (TypeError, ValueError):
            actor_id = None
        actor_username = (
            getattr(actor, "username", None)
            or (actor[1] if isinstance(actor, tuple) and len(actor) > 1 else None)
        )
    db.add(ServiceApplyLog(
        name=name,
        action=action,
        actor_user_id=actor_id,
        actor_username=actor_username,
        summary=summary,
    ))


def mark_dirty(db: Session, name: str, actor=None, summary: str | None = None) -> None:
    """Flag the service as needing a reload + append a 'save' audit row.

    Idempotent on the dirty flag (multiple Saves before a single Apply
    just bump `last_marked_dirty_at` and stack audit rows).
    """
    row = _get_or_create(db, name)
    row.dirty = True
    row.last_marked_dirty_at = _utcnow()
    _log(db, name, "save", actor=actor, summary=summary)
    db.commit()


def mark_clean(db: Session, name: str, actor=None, summary: str | None = None) -> None:
    """Flag the service as in sync + append an 'apply' audit row."""
    row = _get_or_create(db, name)
    row.dirty = False
    row.last_applied_at = _utcnow()
    _log(db, name, "apply", actor=actor, summary=summary)
    db.commit()


def recent_log(db: Session, name: str | None = None, limit: int = 50) -> list[dict]:
    """Return the most recent audit rows, optionally filtered by service."""
    q = db.query(ServiceApplyLog).order_by(ServiceApplyLog.at.desc())
    if name is not None:
        q = q.filter(ServiceApplyLog.name == name)
    rows = q.limit(limit).all()
    return [
        {
            "id": r.id,
            "name": r.name,
            "action": r.action,
            "actor_user_id": r.actor_user_id,
            "actor_username": r.actor_username,
            "summary": r.summary,
            "at": r.at.isoformat() if r.at else None,
        }
        for r in rows
    ]


def _sha256_text(t: str) -> str:
    return hashlib.sha256(t.encode("utf-8", errors="replace")).hexdigest()


def _sha256_path(p) -> str | None:
    try:
        return _sha256_text(p.read_text())
    except FileNotFoundError:
        return None
    except OSError:
        return None


def _reconcile_dhcp(db: Session, source: str) -> bool:
    try:
        from app.services import dhcp_apply
        expected = dhcp_apply.render(db)
        on_disk = _sha256_path(dhcp_apply.CONF_PATH)
        match = (_sha256_text(expected) == on_disk) if expected else (on_disk is None)
        if is_dirty(db, "dhcp") and match:
            mark_clean(db, "dhcp", summary=f"{source}: on-disk conf already matches DB")
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _reconcile_dns(db: Session, source: str) -> bool:
    try:
        from app.services import dns_apply
        expected = dns_apply.render(db)
        on_disk = _sha256_path(dns_apply.CONF_PATH)
        match = (_sha256_text(expected) == on_disk) if expected else (on_disk is None)
        if is_dirty(db, "dns") and match:
            mark_clean(db, "dns", summary=f"{source}: on-disk conf already matches DB")
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _reconcile_snmp(db: Session, source: str) -> bool:
    try:
        from app import snmp
        from app.routes.notif import _get_snmp_config
        cfg = _get_snmp_config(db)
        expected = snmp.render_conf(cfg) if cfg.enabled else ""
        on_disk = _sha256_path(snmp.SNMP_CONF)
        match = (_sha256_text(expected) == on_disk) if expected else (on_disk is None)
        if is_dirty(db, "snmp") and match:
            mark_clean(db, "snmp", summary=f"{source}: on-disk conf already matches DB")
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _reconcile_ssh_admin_flag(db: Session, source: str) -> bool:
    """Clear ssh_config.admin_disabled if sshd was re-enabled externally.

    Out-of-band scenario: the operator ran `systemctl enable --now ssh`
    from the serial console (or the .deb postinst did) AFTER having
    flipped the UI toggle off. The flag in DB still says 'admin
    disabled' so the dashboard would keep labeling sshd as 'disabled
    by admin' even though it is actually running. Detect this and
    auto-clear the flag so the UI stays honest.
    """
    try:
        from app import models, ssh_config
        cfg = db.get(models.SshConfig, 1)
        if cfg is None or not getattr(cfg, "admin_disabled", False):
            return False
        active = (
            ssh_config._systemd_active("ssh")
            or ssh_config._systemd_active("sshd")
        )
        if active:
            cfg.admin_disabled = False
            db.commit()
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _reconcile_wireguard(db: Session, source: str) -> bool:
    """WireGuard reconcile: compare /etc/wireguard/<iface>.conf with DB.

    When the singleton config is enabled, the on-disk file must match
    what `wireguard.render_config()` produces from the current DB rows.
    When disabled, the file must be absent (write_conf removes it).
    """
    if not is_dirty(db, "wireguard"):
        return False
    try:
        from app import models, wireguard
        cfg = db.get(models.WireGuardConfig, 1)
        if cfg is None:
            return False
        iface = cfg.interface_name or "wg0"
        conf_path = wireguard.WG_DIR / f"{iface}.conf"
        if not cfg.enabled:
            if not conf_path.exists():
                mark_clean(db, "wireguard",
                           summary=f"{source}: WireGuard disabled, no on-disk conf")
                return True
            return False
        # Enabled: render expected and compare.
        peers = (db.query(models.WireGuardPeer)
                   .order_by(models.WireGuardPeer.id).all())
        try:
            expected = wireguard.render_config(cfg, peers)
        except ValueError:
            # Config incomplete (no private key / address). The pending
            # apply is legitimate, leave the dirty flag in place.
            return False
        if _sha256_text(expected) == _sha256_path(conf_path):
            mark_clean(db, "wireguard",
                       summary=f"{source}: wg0.conf already matches DB")
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _reconcile_ipsec(db: Session, source: str) -> bool:
    """IPsec reconcile: compare /etc/swanctl/conf.d/muros.conf with DB.

    Only the main swanctl conf is checked. Secrets and PKI are excluded
    on purpose (they would force a constant re-render due to file-mode
    only deltas).
    """
    if not is_dirty(db, "ipsec"):
        return False
    try:
        from app import models, ipsec
        connections = (db.query(models.IpsecConnection)
                         .order_by(models.IpsecConnection.id).all())
        certs = db.query(models.IpsecCert).all()
        certs_by_id = {c.id: c for c in certs}
        expected = ipsec.render_swanctl_conf(connections, certs_by_id)
        if _sha256_text(expected) == _sha256_path(ipsec.SWANCTL_CONF):
            mark_clean(db, "ipsec",
                       summary=f"{source}: swanctl conf already matches DB")
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _reconcile_ssh(db: Session, source: str) -> bool:
    """SSH reconcile: compare /etc/ssh/sshd_config.d/muros.conf with DB.

    The drop-in is always present (no enable/disable on this file),
    only its contents vary.
    """
    if not is_dirty(db, "ssh"):
        return False
    try:
        from app import models, ssh_config
        cfg = db.get(models.SshConfig, 1)
        if cfg is None:
            return False
        expected = ssh_config.render_dropin(cfg)
        if _sha256_text(expected) == _sha256_path(ssh_config.DROPIN_PATH):
            mark_clean(db, "ssh",
                       summary=f"{source}: sshd drop-in already matches DB")
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _reconcile_http(db: Session, source: str) -> bool:
    """HTTP access reconcile: compare the nginx site conf with DB."""
    if not is_dirty(db, "http"):
        return False
    try:
        from app import models, nginx_config
        cfg = db.get(models.HttpConfig, 1)
        if cfg is None:
            return False
        expected = nginx_config.render_site_conf(cfg)
        if _sha256_text(expected) == _sha256_path(nginx_config.SITE_CONF):
            mark_clean(db, "http",
                       summary=f"{source}: nginx site conf already matches DB")
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _reconcile_ha(db: Session, source: str) -> bool:
    """HA reconcile: clear dirty if keepalived + conntrackd state matches DB.

    The HA pipeline is special because there are two daemons (keepalived
    + conntrackd) and an explicit enabled/disabled flag in the config.
    When enabled, we compare both rendered files against on-disk. When
    disabled, we just check both files are absent (apply removes them).
    """
    if not is_dirty(db, "ha"):
        return False
    try:
        from app import ha, models
        cfg = db.get(models.HaConfig, 1)
        if cfg is None:
            return False
        vips = db.query(models.HaVip).order_by(models.HaVip.vrid).all()

        if not cfg.enabled:
            # Disabled state: both conf files must be absent.
            ka_absent = not ha.KEEPALIVED_CONF.exists()
            cd_absent = not ha.CONNTRACKD_CONF.exists()
            if ka_absent and cd_absent:
                mark_clean(db, "ha", summary=f"{source}: HA disabled, no on-disk conf")
                return True
            return False

        # Enabled state: needs peer / sync_interface / at least one VIP.
        # If preconditions are not met, the pending apply is legitimate
        # (DB says enabled=true but apply would 400) -> keep dirty.
        if not cfg.peer_address or not cfg.sync_interface or not vips:
            return False

        import platform
        hostname = platform.node() or "muros"
        cfg_dict = {
            "enabled": True,
            "role": cfg.role,
            "peer_address": cfg.peer_address,
            "sync_interface": cfg.sync_interface,
            "conntrack_sync": cfg.conntrack_sync,
            "preempt": cfg.preempt,
        }
        vips_dict = [
            {
                "vrid": v.vrid, "interface": v.interface, "vip_cidr": v.vip_cidr,
                "auth_pass": v.auth_pass, "priority": v.priority,
                "description": v.description, "enabled": v.enabled,
            } for v in vips
        ]
        expected_ka = ha.render_keepalived(cfg_dict, vips_dict, hostname)
        ka_match = (_sha256_text(expected_ka) == _sha256_path(ha.KEEPALIVED_CONF))

        cd_match = True
        if cfg.conntrack_sync:
            try:
                local_addr = ha._detect_local_ip(cfg.sync_interface)
                expected_cd = ha.render_conntrackd(cfg_dict, local_addr)
                cd_match = (_sha256_text(expected_cd) == _sha256_path(ha.CONNTRACKD_CONF))
            except RuntimeError:
                cd_match = False
        else:
            cd_match = not ha.CONNTRACKD_CONF.exists()

        if ka_match and cd_match:
            mark_clean(db, "ha", summary=f"{source}: keepalived/conntrackd conf already matches DB")
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def reconcile_all(db: Session, source: str = "reconcile") -> dict[str, bool]:
    """Run cheap reconcile for every managed service.

    For each service we re-render the expected on-disk config(s) from
    the DB and compare to what is actually on disk. If they match, the
    daemon is already in sync, clear the dirty flag. If they differ,
    leave it alone (real pending Save or pending Apply).

    Safe to call on every poll: each check is a few stat() + sha256
    on small files, no subprocess or network calls.
    """
    # Side-effect reconcile that does not touch the dirty flag map
    # (it owns the ssh_config.admin_disabled boolean, not a dirty bit).
    _reconcile_ssh_admin_flag(db, source)
    return {
        "dhcp":      _reconcile_dhcp(db, source),
        "dns":       _reconcile_dns(db, source),
        "snmp":      _reconcile_snmp(db, source),
        "ha":        _reconcile_ha(db, source),
        "wireguard": _reconcile_wireguard(db, source),
        "ipsec":     _reconcile_ipsec(db, source),
        "ssh":       _reconcile_ssh(db, source),
        "http":      _reconcile_http(db, source),
    }


# Kept for backward compat with main.py startup hook.
def reconcile_on_startup(db: Session) -> dict[str, bool]:
    """Boot-time alias for reconcile_all(source='reconcile-on-startup').

    Some daemons (Kea, unbound, snmpd) load their drop-in conf
    files at OS boot, BEFORE the MurOS backend starts. If the operator
    clicked Save but never Apply, then rebooted, the daemon ends up
    running the saved config anyway -- yet the dirty flag in
    service_apply_state still says 'pending'. That's a false positive
    that causes phantom orange dots in the UI. Clear them here.
    """
    return {
        name: ok
        for name, ok in reconcile_all(db, source="reconcile-on-startup").items()
        if ok
    }


def is_dirty(db: Session, name: str) -> bool:
    row = db.get(ServiceApplyState, name)
    return bool(row and row.dirty)


def get_state(db: Session, name: str) -> dict:
    """Return a JSON-serializable snapshot of the apply state."""
    row = db.get(ServiceApplyState, name)
    if row is None:
        return {
            "name": name,
            "dirty": False,
            "last_applied_at": None,
            "last_marked_dirty_at": None,
        }
    return {
        "name": row.name,
        "dirty": bool(row.dirty),
        "last_applied_at": row.last_applied_at.isoformat() if row.last_applied_at else None,
        "last_marked_dirty_at": row.last_marked_dirty_at.isoformat() if row.last_marked_dirty_at else None,
    }


def all_states(db: Session) -> dict[str, dict]:
    """Return the state of every known service, populating missing rows."""
    return {name: get_state(db, name) for name in KNOWN_SERVICES}
