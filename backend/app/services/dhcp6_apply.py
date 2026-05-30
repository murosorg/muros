# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Generation of /etc/kea/kea-dhcp6.conf + reload of the Kea DHCPv6 server.

IPv6 counterpart of dhcp_apply. MurOS runs kea-dhcp6-server as a stateful
DHCPv6 server, for LANs where SLAAC alone is not enough. Like the IPv4
side, the rendered config is always valid: when DHCPv6 is disabled or has
no pool, the daemon runs idle (no interface, no subnet) instead of
failing. The single source of truth is the MurOS database.

Clients only request a DHCPv6 address when the Router Advertisement M
(managed) flag is set on the interface, so this works hand in hand with
the RA page.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import os
import subprocess
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import Dhcp6Config, Dhcp6Pool, Interface

log = logging.getLogger("muros.dhcp6")

SERVICE = "kea-dhcp6-server.service"
CONF_PATH = Path("/etc/kea/kea-dhcp6.conf")
LEASES_PATH = Path("/var/lib/kea/kea-leases6.csv")
_KEA_BIN = "kea-dhcp6"
_APPLY = os.environ.get("MUROS_APPLY", "false").lower() in ("1", "true", "yes")


class Dhcp6ApplyError(Exception):
    """Raised when Kea refuses to load the rendered DHCPv6 configuration."""


def _get_singleton(db: Session) -> Dhcp6Config:
    cfg = db.get(Dhcp6Config, 1)
    if cfg is None:
        cfg = Dhcp6Config(id=1)
        db.add(cfg)
        db.commit()
    return cfg


def _iface_v6(iface: Interface | None) -> str | None:
    """Return the bare IPv6 address configured on an interface, if any."""
    if iface is None or not iface.ip_address:
        return None
    try:
        parsed = ipaddress.ip_interface(iface.ip_address)
    except ValueError:
        return None
    return str(parsed.ip) if parsed.version == 6 else None


def subnet_from_range(range_start: str) -> str | None:
    """Return the /64 prefix containing range_start (e.g. 2001:db8:1::/64)."""
    try:
        addr = ipaddress.IPv6Address(range_start.strip())
    except ipaddress.AddressValueError:
        return None
    return str(ipaddress.IPv6Network((addr, 64), strict=False))


def _build_config(db: Session) -> dict:
    """Build the Kea DHCPv6 config as a Python dict (testable, no I/O)."""
    cfg = _get_singleton(db)
    interfaces: list[str] = []
    subnets: list[dict] = []

    pools = (
        db.query(Dhcp6Pool).filter(Dhcp6Pool.enabled.is_(True)).all()
        if cfg.enabled else []
    )
    for p in pools:
        iface: Interface | None = p.interface
        if iface is None or not iface.name:
            continue
        network = subnet_from_range(p.range_start)
        if network is None:
            log.warning("DHCPv6 pool #%s skipped: invalid range_start %r",
                        p.id, p.range_start)
            continue
        if iface.name not in interfaces:
            interfaces.append(iface.name)

        option_data: list[dict] = []
        if p.dns_servers and p.dns_servers.strip():
            dns = ",".join(s.strip() for s in p.dns_servers.split(",") if s.strip())
        else:
            dns = _iface_v6(iface) or ""
        if dns:
            option_data.append({"name": "dns-servers", "data": dns})

        subnet = {
            "id": p.id,
            "subnet": network,
            "interface": iface.name,
            "pools": [{"pool": f"{p.range_start} - {p.range_end}"}],
            "valid-lifetime": p.lease_seconds or cfg.default_lease_seconds,
        }
        if option_data:
            subnet["option-data"] = option_data
        subnets.append(subnet)

    dhcp6: dict = {
        "interfaces-config": {"interfaces": interfaces},
        "control-socket": {
            "socket-type": "unix",
            "socket-name": "/run/kea/kea6-ctrl-socket",
        },
        "lease-database": {
            "type": "memfile",
            "persist": True,
            "name": str(LEASES_PATH),
        },
        "valid-lifetime": cfg.default_lease_seconds,
        "subnet6": subnets,
        "loggers": [{
            "name": "kea-dhcp6",
            "severity": "INFO",
            "output_options": [{"output": "syslog"}],
        }],
    }
    return {"Dhcp6": dhcp6}


def render(db: Session) -> str:
    """Render the Kea DHCPv6 config JSON as text (always valid, even idle)."""
    header = (
        "// /etc/kea/kea-dhcp6.conf -- managed by MurOS, do not edit.\n"
        "// Regenerated from the MurOS database on every DHCPv6 apply.\n"
    )
    return header + json.dumps(_build_config(db), indent=2) + "\n"


def write_conf(db: Session) -> None:
    """Render and persist /etc/kea/kea-dhcp6.conf only (no systemd)."""
    if not _APPLY:
        log.info("MUROS_APPLY=0, skipping kea-dhcp6.conf write")
        return
    CONF_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONF_PATH.write_text(render(db))


def reload(db: Session) -> None:
    """Validate then restart Kea DHCPv6 to pick up the on-disk config."""
    if not _APPLY:
        log.info("MUROS_APPLY=0, skipping kea-dhcp6 reload")
        return
    try:
        check = subprocess.run(
            [_KEA_BIN, "-t", str(CONF_PATH)],
            capture_output=True, timeout=15,
        )
    except FileNotFoundError:
        check = None
    if check is not None and check.returncode != 0:
        raise Dhcp6ApplyError(
            "kea-dhcp6 -t rejected the generated configuration: "
            + (check.stderr or check.stdout).decode(errors="replace").strip()
        )
    subprocess.run(["systemctl", "unmask", SERVICE], capture_output=True, timeout=10)
    subprocess.run(["systemctl", "enable", SERVICE], capture_output=True, timeout=10)
    r = subprocess.run(["systemctl", "restart", SERVICE], capture_output=True, timeout=15)
    if r.returncode != 0:
        log.error("systemctl restart %s failed (rc=%s): %s", SERVICE, r.returncode,
                  r.stderr.decode(errors="replace").strip())


def apply(db: Session) -> None:
    """Backwards-compatible helper: write then reload in a single call."""
    write_conf(db)
    reload(db)


def read_active_leases() -> list[dict]:
    """Parse the Kea DHCPv6 memfile lease CSV (/var/lib/kea/kea-leases6.csv).

    Kea6 memfile columns: address,duid,valid_lifetime,expire,subnet_id,
    pref_lifetime,lease_type,iaid,prefix_len,fqdn_fwd,fqdn_rev,hostname,
    state,user_context,hwaddr,pool_id. Last row per address wins; expired
    and released (valid_lifetime 0) leases are dropped.
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
                out[address] = {
                    "expiry": expire,
                    "duid": col(row, "duid"),
                    "ip": address,
                    "hostname": col(row, "hostname") or None,
                    "_vlt": vlt,
                }
    except OSError:
        return []
    import time
    now = int(time.time())
    result = []
    for e in out.values():
        vlt = e.pop("_vlt", 0)
        if vlt == 0:
            continue
        if e.get("expiry") and e["expiry"] < now:
            continue
        result.append(e)
    return result


def get_status(db: Session) -> dict:
    """Return a snapshot of the DHCPv6 server state."""
    from app.service_state import service_state, pkg_version, which
    cfg = _get_singleton(db)
    leases = read_active_leases()
    return {
        "enabled": cfg.enabled,
        "installed": which(_KEA_BIN),
        "service_state": service_state(SERVICE),
        "version": pkg_version("kea-dhcp6-server"),
        "pools_count": db.query(Dhcp6Pool).count(),
        "active_leases_count": len(leases),
        "config_path": str(CONF_PATH),
        "leases_path": str(LEASES_PATH),
    }
