# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Schemas Pydantic des groupes de services et d'adresses.

Utilises par la page Pare-feu > Services et reference depuis les regles
firewall (FirewallRule.service_group_id, src_address_group_id,
dst_address_group_id).
"""
from __future__ import annotations

import ipaddress
import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Protocol = Literal["tcp", "udp"]

_PORT_RE = re.compile(r"^\d+(-\d+)?$")


class ServiceGroupPortIn(BaseModel):
    protocol: Protocol
    port: str = Field(min_length=1, max_length=32)

    @field_validator("port")
    @classmethod
    def _check_port(cls, v: str) -> str:
        v = v.strip()
        if not _PORT_RE.match(v):
            raise ValueError(
                "invalid port format. Expected : '80' ou '1024-2048'"
            )
        if "-" in v:
            lo, hi = (int(x) for x in v.split("-", 1))
            if not (1 <= lo <= hi <= 65535):
                raise ValueError("invalid port range (1..65535, lo <= hi)")
        else:
            if not 1 <= int(v) <= 65535:
                raise ValueError("invalid port (1..65535)")
        return v


class ServiceGroupPortOut(ServiceGroupPortIn):
    id: int
    model_config = ConfigDict(from_attributes=True)


class ServiceGroupBase(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=255)

    @field_validator("name")
    @classmethod
    def _check_name(cls, v: str) -> str:
        v = v.strip()
        if not re.match(r"^[A-Za-z0-9_.+/ -]+$", v):
            raise ValueError(
                "invalid name: alphanumeric, space, dot, dash, slash, plus, underscore only"
            )
        return v


class ServiceGroupCreate(ServiceGroupBase):
    ports: list[ServiceGroupPortIn] = Field(default_factory=list)


class ServiceGroupUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=255)
    ports: list[ServiceGroupPortIn] | None = None


class ServiceGroupOut(ServiceGroupBase):
    id: int
    created_at: datetime
    ports: list[ServiceGroupPortOut] = Field(default_factory=list)
    model_config = ConfigDict(from_attributes=True)


class AddressGroupEntryIn(BaseModel):
    value: str = Field(min_length=1, max_length=64)

    @field_validator("value")
    @classmethod
    def _check_value(cls, v: str) -> str:
        v = v.strip()
        # Accepte IPv4, IPv6, CIDR v4, CIDR v6
        try:
            if "/" in v:
                ipaddress.ip_network(v, strict=False)
            else:
                ipaddress.ip_address(v)
        except ValueError as exc:
            raise ValueError(f"invalid address : {exc}")
        return v


class AddressGroupEntryOut(AddressGroupEntryIn):
    id: int
    model_config = ConfigDict(from_attributes=True)


class AddressGroupBase(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=255)

    @field_validator("name")
    @classmethod
    def _check_name(cls, v: str) -> str:
        v = v.strip()
        if not re.match(r"^[A-Za-z0-9_.+/ -]+$", v):
            raise ValueError(
                "invalid name: alphanumeric, space, dot, dash, slash, plus, underscore only"
            )
        return v


class AddressGroupCreate(AddressGroupBase):
    entries: list[AddressGroupEntryIn] = Field(default_factory=list)


class AddressGroupUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=255)
    entries: list[AddressGroupEntryIn] | None = None


class AddressGroupOut(AddressGroupBase):
    id: int
    created_at: datetime
    entries: list[AddressGroupEntryOut] = Field(default_factory=list)
    model_config = ConfigDict(from_attributes=True)
