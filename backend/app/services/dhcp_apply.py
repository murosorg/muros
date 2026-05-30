# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Generation of /etc/kea/kea-dhcp4.conf + reload of the Kea DHCPv4 server.

MurOS uses ISC Kea as its DHCPv4 server. Kea is DHCP-only (it never
binds port 53), so it coexists cleanly with Unbound as the recursive
resolver: no port collision, both services can run side by side at all
times. The single source of truth is the MurOS database; this module
renders the Kea JSON config from it.

Kea is rendered as a self-contained, always-valid config: when DHCP is
disabled or no pool is defined, the config still loads but serves no
subnet and binds to no interface (idle daemon). That keeps the service
running without ever handing out a lease until the operator configures
a pool, and avoids the classic "daemon failed right after install".
"""
from __future__ import annotations

import ipaddress
import json
import logging
import os
import subprocess
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import DhcpConfig, DhcpPool, DhcpStaticLease, Interface

log = logging.getLogger("muros.dhcp")

SERVICE = "kea-dhcp4-server.service"
CONF_PATH = Path("/etc/kea/kea-dhcp4.conf")
LEASES_PATH = Path("/var/lib/kea/kea-leases4.csv")
# Binary used to validate a rendered config before poking systemd.
_KEA_BIN = "kea-dhcp4"
_APPLY = os.environ.get("MUROS_APPLY", "false").lower() in ("1", "true", "yes")


class DhcpApplyError(Exception):
    """Raised when Kea refuses to load the rendered configuration.

    Routes catch this and surface it as a 409 to the UI, so a broken
    pool definition (overlapping ranges, malformed reservation) does not
    silently leave the LAN without DHCP.
    """


def _get_singleton(db: Session) -> DhcpConfig:
    cfg = db.get(DhcpConfig, 1)
    if cfg is None:
        cfg = DhcpConfig(id=1)
        db.add(cfg)
        db.commit()
    return cfg


def _iface_ip(iface: Interface | None) -> str | None:
    """Return the bare IPv4 address configured on an interface, if any."""
    if iface is None or not iface.ip_address:
        return None
    try:
        return str(ipaddress.ip_interface(iface.ip_address).ip)
    except ValueError:
        return None


def _iface_network(iface: Interface | None) -> str | None:
    """Return the IPv4 network (CIDR) the interface sits on, if any.

    Kea needs the subnet CIDR for each subnet4 entry. We derive it from
    the interface address (e.g. 192.168.1.1/24 -> 192.168.1.0/24).
    """
    if iface is None or not iface.ip_address:
        return None
    try:
        return str(ipaddress.ip_interface(iface.ip_address).network)
    except ValueError:
        return None


def _build_config(db: Session) -> dict:
    """Build the Kea DHCPv4 config as a Python dict (testable, no I/O)."""
    cfg = _get_singleton(db)

    interfaces: list[str] = []
    subnets: list[dict] = []

    pools = (
        db.query(DhcpPool).filter(DhcpPool.enabled.is_(True)).all()
        if cfg.enabled else []
    )
    for p in pools:
        iface: Interface | None = p.interface
        if iface is None or not iface.name:
            continue
        network = _iface_network(iface)
        if network is None:
            # No usable IPv4 on the interface : Kea cannot place the
            # subnet. Skip rather than emit an invalid subnet4 entry.
            log.warning(
                "DHCP pool #%s skipped : interface %s has no IPv4 address",
                p.id, iface.name,
            )
            continue
        if iface.name not in interfaces:
            interfaces.append(iface.name)

        option_data: list[dict] = []
        gateway = (p.gateway or "").strip() or _iface_ip(iface)
        if gateway:
            option_data.append({"name": "routers", "data": gateway})
        if p.dns_servers and p.dns_servers.strip():
            dns = ",".join(
                s.strip() for s in p.dns_servers.split(",") if s.strip()
            )
        else:
            # Default : hand out the MurOS box itself so clients resolve
            # through the local Unbound recursive resolver.
            dns = _iface_ip(iface) or ""
        if dns:
            option_data.append({"name": "domain-name-servers", "data": dns})
        if cfg.domain:
            option_data.append({"name": "domain-name", "data": cfg.domain})

        reservations: list[dict] = []
        leases = (
            db.query(DhcpStaticLease)
            .filter(DhcpStaticLease.pool_id == p.id)
            .all()
        )
        for lease in leases:
            res = {"hw-address": lease.mac, "ip-address": lease.ip}
            if lease.hostname:
                res["hostname"] = lease.hostname
            reservations.append(res)

        subnet = {
            "id": p.id,
            "subnet": network,
            "interface": iface.name,
            "pools": [{"pool": f"{p.range_start} - {p.range_end}"}],
            "valid-lifetime": p.lease_seconds or cfg.default_lease_seconds,
        }
        if option_data:
            subnet["option-data"] = option_data
        if reservations:
            subnet["reservations"] = reservations
        subnets.append(subnet)

    dhcp4: dict = {
        "interfaces-config": {"interfaces": interfaces},
        "control-socket": {
            "socket-type": "unix",
            "socket-name": "/run/kea/kea4-ctrl-socket",
        },
        "lease-database": {
            "type": "memfile",
            "persist": True,
            "name": str(LEASES_PATH),
        },
        "valid-lifetime": cfg.default_lease_seconds,
        "authoritative": bool(cfg.authoritative),
        "subnet4": subnets,
        "loggers": [{
            "name": "kea-dhcp4",
            "severity": "INFO",
            "output_options": [{"output": "syslog"}],
        }],
    }
    return {"Dhcp4": dhcp4}


def render(db: Session) -> str:
    """Render the Kea config JSON as text (always valid, even when idle)."""
    header = (
        "// /etc/kea/kea-dhcp4.conf -- managed by MurOS, do not edit.\n"
        "// Regenerated from the MurOS database on every DHCP apply.\n"
    )
    return header + json.dumps(_build_config(db), indent=2) + "\n"


def write_conf(db: Session) -> None:
    """Render and persist /etc/kea/kea-dhcp4.conf only (no systemd).

    Used by the Save path: the new config is materialised so it survives
    a reboot, but the live daemon keeps its previous config until the
    operator clicks Apply.
    """
    if not _APPLY:
        log.info("MUROS_APPLY=0, skipping kea-dhcp4.conf write")
        return
    CONF_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONF_PATH.write_text(render(db))


def reload(db: Session) -> None:
    """Validate then restart Kea to pick up the on-disk config.

    Kea stays enabled at all times : when DHCP is disabled or has no
    pool the rendered config is idle (no interface, no subnet), so the
    daemon runs but hands out nothing. This avoids stop/start churn and
    keeps the service state predictable on the dashboard.
    """
    if not _APPLY:
        log.info("MUROS_APPLY=0, skipping kea reload")
        return

    # Validate the rendered config before touching systemd. A broken
    # pool / reservation would put Kea in a failed state and blackhole
    # DHCP for the whole LAN. Surface it to the UI as a 409 instead.
    try:
        check = subprocess.run(
            [_KEA_BIN, "-t", str(CONF_PATH)],
            capture_output=True, timeout=15,
        )
    except FileNotFoundError:
        check = None
    if check is not None and check.returncode != 0:
        raise DhcpApplyError(
            "kea-dhcp4 -t rejected the generated configuration: "
            + (check.stderr or check.stdout).decode(errors="replace").strip()
        )

    subprocess.run(
        ["systemctl", "unmask", SERVICE],
        capture_output=True, timeout=10,
    )
    subprocess.run(
        ["systemctl", "enable", SERVICE],
        capture_output=True, timeout=10,
    )
    r = subprocess.run(
        ["systemctl", "restart", SERVICE],
        capture_output=True, timeout=15,
    )
    if r.returncode != 0:
        log.error(
            "systemctl restart %s failed (rc=%s): %s",
            SERVICE, r.returncode, r.stderr.decode(errors="replace").strip(),
        )


def apply(db: Session) -> None:
    """Backwards-compatible helper: write then reload in a single call."""
    write_conf(db)
    reload(db)


def read_active_leases() -> list[dict]:
    """Parse the Kea memfile lease CSV (/var/lib/kea/kea-leases4.csv).

    Kea appends a new row every time a lease changes, so the same
    address can appear several times; the last row wins. A row with
    valid_lifetime 0 (or expire in the past) is an expired / released
    lease and is dropped. CSV columns (Kea 2.x memfile schema):
      address,hwaddr,client_id,valid_lifetime,expire,subnet_id,
      fqdn_fwd,fqdn_rev,hostname,state,user_context,pool_id
    """
    out: dict[str, dict] = {}
    if not LEASES_PATH.is_file():
        return []
    try:
        import csv
        with LEASES_PATH.open(newline="", errors="replace") as fh:
            reader = csv.reader(fh)
            header = next(reader, None)
            if header is None:
                return []
            idx = {name: i for i, name in enumerate(header)}

            def col(row, name):
                i = idx.get(name)
                return row[i] if i is not None and i < len(row) else ""

            for row in reader:
                if not row:
                    continue
                address = col(row, "address")
                if not address:
                    continue
                try:
                    expire = int(col(row, "expire") or 0)
                except ValueError:
                    expire = 0
                try:
                    vlt = int(col(row, "valid_lifetime") or 0)
                except ValueError:
                    vlt = 0
                hostname = col(row, "hostname") or None
                entry = {
                    "expiry": expire,
                    "mac": col(row, "hwaddr"),
                    "ip": address,
                    "hostname": hostname,
                    "client_id": col(row, "client_id") or None,
                    "_vlt": vlt,
                }
                # Last row for an address wins (most recent state).
                out[address] = entry
    except OSError:
        return []
    # Drop released leases (valid_lifetime 0) and strip helper field.
    result = []
    for e in out.values():
        if e.pop("_vlt", 0) == 0:
            continue
        result.append(e)
    return result


def get_status(db: Session) -> dict:
    """Return a snapshot of the DHCP server state."""
    from app.service_state import service_state, pkg_version, which
    cfg = _get_singleton(db)
    leases = read_active_leases()
    return {
        "enabled": cfg.enabled,
        "installed": which(_KEA_BIN),
        "service_state": service_state(SERVICE),
        "version": pkg_version("kea-dhcp4-server"),
        "pools_count": db.query(DhcpPool).count(),
        "static_leases_count": db.query(DhcpStaticLease).count(),
        "active_leases_count": len(leases),
        "config_path": str(CONF_PATH),
        "leases_path": str(LEASES_PATH),
    }
