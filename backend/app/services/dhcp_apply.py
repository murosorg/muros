# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Generation de /etc/dnsmasq.d/muros.conf + reload du service dnsmasq.

Mode : dnsmasq en DHCP-only (option `port=0` desactive le DNS embarque,
on laisse Unbound s'en occuper). Une seule source : la DB MurOS.
"""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import DhcpConfig, DhcpPool, DhcpStaticLease, Interface

log = logging.getLogger("muros.dhcp")

CONF_PATH = Path("/etc/dnsmasq.d/muros.conf")
LEASES_PATH = Path("/var/lib/misc/dnsmasq.leases")
# Sentinel for tests / dev without systemd : when MUROS_APPLY is
# off, we neither write the conf nor reload the service. Accept the
# same set of truthy spellings as the rest of the codebase
# (env var is set to "true" by the systemd unit).
_APPLY = os.environ.get("MUROS_APPLY", "false").lower() in ("1", "true", "yes")


class DhcpApplyError(Exception):
    """Raised when dnsmasq refuses to load the rendered configuration.

    Routes catch this and surface it as a 409 to the UI, so a broken
    pool definition (overlapping ranges, invalid netmask, malformed
    static lease) does not silently leave the LAN without DHCP.
    """


def _get_singleton(db: Session) -> DhcpConfig:
    cfg = db.get(DhcpConfig, 1)
    if cfg is None:
        cfg = DhcpConfig(id=1)
        db.add(cfg)
        db.commit()
    return cfg


def render(db: Session) -> str:
    """Construit le contenu de muros.conf en memoire (testable sans I/O).

    Hors du mode `enabled`, on renvoie une conf neutre (empty) pour que
    le reload soit no-op. On ne genere PAS le fichier empty : c'est le
    job du caller (apply) qui le removed pour que dnsmasq n'expose
    no section DHCP.
    """
    cfg = _get_singleton(db)
    if not cfg.enabled:
        return ""

    lines: list[str] = [
        "# /etc/dnsmasq.d/muros.conf -- managed by MurOS, do not edit.",
        "# DNS embarque desactive : MurOS utilise Unbound pour le recursive.",
        "port=0",
        "# Refuse d'avancer si l'interface designee n'est pas montee.",
        "bind-interfaces",
        "dhcp-leasefile=/var/lib/misc/dnsmasq.leases",
    ]
    if cfg.authoritative:
        lines.append("dhcp-authoritative")
    if cfg.domain:
        lines.append(f"domain={cfg.domain}")
        lines.append("expand-hosts")

    pools = db.query(DhcpPool).filter(DhcpPool.enabled.is_(True)).all()
    if not pools:
        # enabled=True but no pool yet : we still emit a minimal valid
        # config so the daemon stays running and the UI shows it as
        # 'active'. Without any dhcp-range, dnsmasq simply sits idle.
        # Previous behaviour was to disable the service, which was
        # confusing ("I toggled it on but the status stays inactive").
        log.info("DHCP enabled but no active pool, emitting idle conf")
        return (
            "# /etc/dnsmasq.d/muros.conf -- managed by MurOS.\n"
            "# DHCP enabled but no pool yet : daemon idle, DNS disabled.\n"
            "port=0\n"
        )

    # Declare every interface that hosts at least one pool exactly
    # once. Repeating `interface=` is harmless but noisy; deduping keeps
    # the generated file readable.
    declared_ifaces: set[str] = set()
    for p in pools:
        iface: Interface | None = p.interface
        if iface is None or not iface.name:
            continue
        if iface.name not in declared_ifaces:
            lines.append("")
            lines.append(f"interface={iface.name}")
            declared_ifaces.add(iface.name)

        lease = p.lease_seconds or cfg.default_lease_seconds
        # Bind the range to clients arriving on this interface. dnsmasq
        # auto-tags incoming DHCP requests with the receiving interface
        # name, so `tag:<iface>` is the documented way to scope a
        # dhcp-range / dhcp-option to one specific link. Using the bare
        # interface name here (the previous behavior) was a syntax
        # error: dnsmasq expects an IP in the first positional and
        # would either reject the line or silently apply the range
        # globally.
        tag = iface.name
        lines.append(f"# Pool #{p.id} on {iface.name}")
        lines.append(
            f"dhcp-range=tag:{tag},{p.range_start},{p.range_end},{lease}s"
        )
        if p.gateway:
            lines.append(f"dhcp-option=tag:{tag},3,{p.gateway}")
        if p.dns_servers:
            dns = ",".join(s.strip() for s in p.dns_servers.split(",") if s.strip())
            lines.append(f"dhcp-option=tag:{tag},6,{dns}")

        leases = db.query(DhcpStaticLease).filter(DhcpStaticLease.pool_id == p.id).all()
        for l in leases:
            host = f",{l.hostname}" if l.hostname else ""
            lines.append(f"dhcp-host={l.mac},{l.ip}{host}")

    return "\n".join(lines) + "\n"


def write_conf(db: Session) -> None:
    """Render and persist /etc/dnsmasq.d/muros.conf only.

    Does NOT touch systemd. Used by the Save path: the new config is
    materialised so it survives a reboot via muros-boot, but the live
    daemon keeps its previous config until the operator clicks Apply.
    """
    if not _APPLY:
        log.info("MUROS_APPLY=0, skipping dnsmasq.conf write")
        return
    content = render(db)
    CONF_PATH.parent.mkdir(parents=True, exist_ok=True)
    if content:
        CONF_PATH.write_text(content)
    elif CONF_PATH.exists():
        CONF_PATH.unlink()


def reload(db: Session) -> None:
    """Restart dnsmasq (or stop it) to pick up the on-disk config.

    Called only by the explicit Apply action from the UI. Assumes
    write_conf() has already been run by the preceding Save.
    """
    if not _APPLY:
        log.info("MUROS_APPLY=0, skipping dnsmasq reload")
        return
    cfg = _get_singleton(db)
    content = render(db)
    if cfg.enabled and content:
        # Validate the drop-in before poking systemd. A broken pool /
        # static lease / option line would put dnsmasq in failed state
        # and blackhole DHCP for the whole LAN. We catch it here and
        # surface to the UI as a 409. Best-effort : if dnsmasq is not
        # installed we proceed (the systemctl call below will surface
        # the real error).
        try:
            check = subprocess.run(
                ["dnsmasq", "--test", "--conf-file=" + str(CONF_PATH)],
                capture_output=True, timeout=10,
            )
        except FileNotFoundError:
            check = None
        if check is not None and check.returncode != 0:
            raise DhcpApplyError(
                "dnsmasq --test rejected the generated configuration: "
                + (check.stderr or check.stdout).decode(errors="replace").strip()
            )

        # enable+start (idempotent), then restart to ensure the new conf
        # is picked up. dnsmasq does not support a graceful 'reload' for
        # all directives, restart is the safe path.
        subprocess.run(
            ["systemctl", "unmask", "dnsmasq.service"],
            capture_output=True, timeout=10,
        )
        subprocess.run(
            ["systemctl", "enable", "dnsmasq.service"],
            capture_output=True, timeout=10,
        )
        r = subprocess.run(
            ["systemctl", "restart", "dnsmasq.service"],
            capture_output=True, timeout=15,
        )
        if r.returncode != 0:
            log.error(
                "systemctl restart dnsmasq failed (rc=%s): %s",
                r.returncode, r.stderr.decode(errors="replace").strip(),
            )
    else:
        subprocess.run(
            ["systemctl", "disable", "--now", "dnsmasq.service"],
            capture_output=True, timeout=10,
        )


def apply(db: Session) -> None:
    """Backwards-compatible helper: write then reload in a single call.

    Kept for code paths that pre-date the Save / Apply split (legacy
    tests, watcher cron). New routes call write_conf() at Save time and
    reload() only on explicit Apply.
    """
    write_conf(db)
    reload(db)


def read_active_leases() -> list[dict]:
    """Parse /var/lib/misc/dnsmasq.leases.

    Line format produced by dnsmasq :
      <expiry_epoch> <mac> <ip> <hostname> <client_id>

    Hostname is `*` when the client did not send one. expiry_epoch is 0
    for static leases (no expiry).
    """
    out: list[dict] = []
    if not LEASES_PATH.is_file():
        return out
    try:
        for raw in LEASES_PATH.read_text(errors="replace").splitlines():
            parts = raw.strip().split()
            if len(parts) < 4:
                continue
            expiry = parts[0]
            try:
                expiry_int = int(expiry)
            except ValueError:
                expiry_int = 0
            out.append({
                "expiry": expiry_int,
                "mac": parts[1],
                "ip": parts[2],
                "hostname": parts[3] if parts[3] != "*" else None,
                "client_id": parts[4] if len(parts) >= 5 else None,
            })
    except OSError:
        pass
    return out


def get_status(db: Session) -> dict:
    """Return a snapshot of the DHCP server state."""
    from app.service_state import service_state, pkg_version, which
    from app.models import DhcpPool, DhcpStaticLease
    cfg = _get_singleton(db)
    leases = read_active_leases()
    return {
        "enabled": cfg.enabled,
        "installed": which("dnsmasq"),
        "service_state": service_state("dnsmasq.service"),
        "version": pkg_version("dnsmasq"),
        "pools_count": db.query(DhcpPool).count(),
        "static_leases_count": db.query(DhcpStaticLease).count(),
        "active_leases_count": len(leases),
        "config_path": str(CONF_PATH),
        "leases_path": str(LEASES_PATH),
    }
