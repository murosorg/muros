# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Schemas Pydantic pour l'API (validation et serialisation)."""
import ipaddress
import re
from datetime import datetime
from typing import Annotated, Literal
from pydantic import AfterValidator, BaseModel, Field

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
        # Accepte IP simple ou reseau CIDR, v4 ou v6
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



# --- Notifications ---

class NotificationConfigIn(BaseModel):
    enabled: bool = False
    smtp_host: str = Field(default="", max_length=255)
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_user: str | None = None
    smtp_password: str | None = None
    use_tls: bool = True
    from_addr: str = Field(default="muros@localhost", max_length=255)
    to_addrs: str = ""


class NotificationConfigOut(NotificationConfigIn):
    id: int

    class Config:
        from_attributes = True


class NotificationRuleOut(BaseModel):
    id: int
    event_type: str
    enabled: bool
    throttle_minutes: int
    description: str | None

    class Config:
        from_attributes = True


class NotificationRuleUpdate(BaseModel):
    enabled: bool
    # Throttle anti-flood : 0 = pas de limite, max 1440 (24h).
    throttle_minutes: int = Field(ge=0, le=1440)


class NotificationLogOut(BaseModel):
    id: int
    event_type: str
    subject: str
    body: str
    success: bool
    error: str | None
    created_at: datetime

    class Config:
        from_attributes = True


class NotificationTestResult(BaseModel):
    sent: bool
    reason: str | None = None


# --- SNMP ---

class SnmpConfigIn(BaseModel):
    enabled: bool = False
    port: int = Field(default=161, ge=1, le=65535)
    # community v2c : on impose au moins 4 caracteres (la valeur 'public'
    # passe mais l'UI doit suggerer de la changer).
    community: str = Field(default="public", min_length=4, max_length=255)
    allowed_networks: str = "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"
    syscontact: str = Field(default="admin@localhost", max_length=255)
    syslocation: str = Field(default="MurOS firewall", max_length=255)


class SnmpConfigOut(SnmpConfigIn):
    id: int

    class Config:
        from_attributes = True


class SnmpStatus(BaseModel):
    installed: bool
    snmpd_installed: bool
    snmp_tools_installed: bool
    service_active: bool
    service_state: str = "unknown"
    version: str | None


class SnmpInstallResult(BaseModel):
    installed: bool
    already_present: list[str]
    newly_installed: list[str]
    output_tail: str


class SnmpApplyResult(BaseModel):
    message: str
    service: str | None = None
    conf_preview: str | None = None
