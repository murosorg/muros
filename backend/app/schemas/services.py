# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Schemas Pydantic pour les network services : DHCP (dnsmasq), DNS (Unbound)."""
import ipaddress
import re
from typing import Annotated, Literal
from pydantic import AfterValidator, BaseModel, ConfigDict, Field

DnsRecordType = Literal["A", "AAAA", "CNAME", "TXT", "MX", "SRV", "PTR"]

_MAC_RE = re.compile(r"^([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$")
_FQDN_RE = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9\-\.]{0,253}[a-zA-Z0-9])?$")


def _ip(v: str) -> str:
    ipaddress.ip_address(v)
    return v


def _mac(v: str) -> str:
    if not _MAC_RE.match(v):
        raise ValueError("MAC must be aa:bb:cc:dd:ee:ff")
    return v.lower()


def _fqdn(v: str) -> str:
    if not _FQDN_RE.match(v):
        raise ValueError("Invalid hostname")
    return v


def _cidr_csv(v: str) -> str:
    if not v:
        return v
    for cidr in [c.strip() for c in v.split(",") if c.strip()]:
        ipaddress.ip_network(cidr, strict=False)
    return v


# --- DHCP ----------------------------------------------------------------

class DhcpConfigOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    enabled: bool
    authoritative: bool
    default_lease_seconds: int
    domain: str | None = None


class DhcpConfigIn(BaseModel):
    enabled: bool
    authoritative: bool = True
    default_lease_seconds: int = Field(default=43200, ge=60, le=2_592_000)  # 1min .. 30j
    domain: str | None = Field(default=None, max_length=255)


class DhcpPoolOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    interface_id: int
    range_start: str
    range_end: str
    gateway: str | None = None
    dns_servers: str | None = None
    lease_seconds: int | None = None
    enabled: bool
    comment: str | None = None


class DhcpPoolIn(BaseModel):
    interface_id: int
    range_start: Annotated[str, AfterValidator(_ip)]
    range_end: Annotated[str, AfterValidator(_ip)]
    gateway: str | None = None
    dns_servers: str | None = None  # CSV d'IPs
    lease_seconds: int | None = Field(default=None, ge=60, le=2_592_000)
    enabled: bool = True
    comment: str | None = Field(default=None, max_length=255)


class DhcpStaticLeaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    pool_id: int
    mac: str
    ip: str
    hostname: str | None = None
    comment: str | None = None


class DhcpStaticLeaseIn(BaseModel):
    pool_id: int
    mac: Annotated[str, AfterValidator(_mac)]
    ip: Annotated[str, AfterValidator(_ip)]
    hostname: str | None = Field(default=None, max_length=255)
    comment: str | None = Field(default=None, max_length=255)


# --- DNS recursive (Unbound) ----------------------------------------------

class DnsConfigOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    enabled: bool
    allow_query_cidrs: str
    dnssec: bool
    prefetch: bool
    forwarders: str | None = None
    use_as_system_resolver: bool = False


class DnsConfigIn(BaseModel):
    enabled: bool
    allow_query_cidrs: Annotated[str, AfterValidator(_cidr_csv)] = "127.0.0.0/8"
    dnssec: bool = True
    prefetch: bool = True
    forwarders: str | None = None  # CSV of IPs, empty -> pure recursive
    use_as_system_resolver: bool = False


class DhcpStatus(BaseModel):
    enabled: bool
    installed: bool
    service_state: str
    version: str | None = None
    pools_count: int
    static_leases_count: int
    active_leases_count: int
    config_path: str
    leases_path: str


class DhcpActiveLease(BaseModel):
    expiry: int
    mac: str
    ip: str
    hostname: str | None = None
    client_id: str | None = None


class DnsStatus(BaseModel):
    enabled: bool
    installed: bool
    service_state: str
    version: str | None = None
    records_count: int
    system_resolver_active: bool
    config_path: str


class DnsLocalRecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    record_type: DnsRecordType
    name: str
    value: str
    comment: str | None = None


class DnsLocalRecordIn(BaseModel):
    record_type: DnsRecordType = "A"
    name: Annotated[str, AfterValidator(_fqdn)]
    value: Annotated[str, AfterValidator(_ip)]
    comment: str | None = Field(default=None, max_length=255)
