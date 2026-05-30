# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Schemas Pydantic pour l'API (validation et serialisation)."""
import ipaddress
import re
from datetime import datetime
from typing import Annotated, Literal
from pydantic import AfterValidator, BaseModel

Action = Literal["accept", "drop", "reject"]
Chain = Literal["input", "forward", "output"]
Protocol = Literal["tcp", "udp", "icmp", "any"]
NatType = Literal["masquerade", "snat", "dnat"]
IpMode = Literal["none", "static", "dhcp"]


_PORT_RE = re.compile(r"^\d+(-\d+)?(,\d+(-\d+)?)*$")


def _validate_address(v: str | None) -> str | None:
    if v is None or v == "":
        return None
    try:
        # Accepts a single IP or a CIDR network, v4 or v6
        ipaddress.ip_network(v, strict=False)
        return v
    except ValueError:
        raise ValueError(f"Invalid IP/CIDR address : {v!r}")


def _validate_port(v: str | None) -> str | None:
    if v is None or v == "":
        return None
    if not _PORT_RE.fullmatch(v.replace(" ", "")):
        raise ValueError(f"Invalid port : {v!r}. Formats: 22, 22-80, 22,80,443")
    return v.replace(" ", "")


Address = Annotated[str | None, AfterValidator(_validate_address)]
Port = Annotated[str | None, AfterValidator(_validate_port)]



# --- Metrics ---
class MemoryInfoOut(BaseModel):
    total_bytes: int
    available_bytes: int
    used_bytes: int
    used_percent: float


class SwapInfoOut(BaseModel):
    total_bytes: int
    used_bytes: int
    used_percent: float


class DiskInfoOut(BaseModel):
    mount: str
    total_bytes: int
    used_bytes: int
    used_percent: float


class NetInterfaceStatsOut(BaseModel):
    name: str
    operstate: str = "unknown"
    rx_bytes: int
    tx_bytes: int
    rx_packets: int
    tx_packets: int
    rx_errors: int
    tx_errors: int
    rx_dropped: int
    tx_dropped: int


class ConntrackInfoOut(BaseModel):
    current: int
    max: int
    used_percent: float


class MetricsSummaryOut(BaseModel):
    timestamp: datetime
    cpu_usage_percent: float
    cpu_cores: int
    memory: MemoryInfoOut
    swap: SwapInfoOut
    load: list[float]
    uptime_seconds: int
    disks: list[DiskInfoOut]
    interfaces: list[NetInterfaceStatsOut]
    conntrack: ConntrackInfoOut


# --- Logs ---
class FirewallLogEntryOut(BaseModel):
    timestamp: str
    message: str
    hostname: str | None = None
    syslog_identifier: str | None = None
    # Champs derives du prefixe nft "[muros <ACTION> r=<ID> <CHAIN>]"
    # quand celui-ci est present dans le message. Permet a l'UI d'afficher
    # an action badge + a link to the matching rule.
    action: str | None = None
    rule_id: int | None = None
    chain: str | None = None


class SystemLogEntryOut(BaseModel):
    timestamp: str
    unit: str
    priority: int
    message: str


