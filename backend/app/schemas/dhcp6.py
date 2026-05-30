# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Pydantic schemas for the DHCPv6 server API (Kea DHCPv6)."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Dhcp6ConfigIn(BaseModel):
    enabled: bool = False
    default_lease_seconds: int = Field(default=43200, ge=60, le=2_592_000)


class Dhcp6ConfigOut(Dhcp6ConfigIn):
    id: int
    model_config = ConfigDict(from_attributes=True)


class Dhcp6PoolIn(BaseModel):
    interface_id: int
    range_start: str = Field(min_length=2, max_length=64)
    range_end: str = Field(min_length=2, max_length=64)
    dns_servers: str | None = Field(default=None, max_length=512)
    lease_seconds: int | None = Field(default=None, ge=60, le=2_592_000)
    enabled: bool = True
    comment: str | None = Field(default=None, max_length=255)


class Dhcp6PoolOut(Dhcp6PoolIn):
    id: int
    interface_name: str | None = None
    model_config = ConfigDict(from_attributes=True)


class Dhcp6Status(BaseModel):
    enabled: bool
    installed: bool
    service_state: str
    version: str | None = None
    pools_count: int
    active_leases_count: int
    config_path: str
    leases_path: str


class Dhcp6ActiveLease(BaseModel):
    expiry: int
    duid: str
    ip: str
    hostname: str | None = None
