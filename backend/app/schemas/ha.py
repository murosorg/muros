# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Schemas Pydantic pour l'API (validation et serialisation)."""
import ipaddress
import re
from datetime import datetime
from typing import Annotated, Literal
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator

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



# --- Haute dispo (HA) ---
class HaConfigOut(BaseModel):
    enabled: bool
    role: Literal["primary", "secondary"]
    peer_address: str
    sync_interface: str
    conntrack_sync: bool
    preempt: bool

    model_config = ConfigDict(from_attributes=True)


class HaConfigIn(BaseModel):
    enabled: bool = False
    role: Literal["primary", "secondary"] = "primary"
    peer_address: str = ""
    sync_interface: str = ""
    # conntrack_sync est non-modifiable depuis l'UI : sans synchro des
    # sessions, un failover casse toutes les connexions TCP existantes,
    # ce qui defait l'interet du HA active/passif. On accepte le champ
    # en entree pour la compat des anciens clients, mais on force True
    # cote validation (Pydantic).
    conntrack_sync: bool = True
    preempt: bool = True

    @field_validator("conntrack_sync")
    @classmethod
    def _force_conntrack_sync(cls, _v: bool) -> bool:
        return True

    model_config = ConfigDict(extra="ignore")


class HaVipOut(BaseModel):
    id: int
    vrid: int
    interface: str
    vip_cidr: str
    auth_pass: str
    priority: int | None = None
    description: str | None = None
    enabled: bool

    model_config = ConfigDict(from_attributes=True)


class HaVipIn(BaseModel):
    # vrid : Virtual Router ID, plage protocole VRRP : 1-255.
    vrid: int = Field(ge=1, le=255)
    interface: str = Field(min_length=1, max_length=15)
    vip_cidr: str = Field(min_length=1)
    # VRRPv2 limite l'auth password a 8 chars max.
    auth_pass: str = Field(default="muros", min_length=1, max_length=8)
    # Plage usuelle keepalived : 1-254 (255 = master force).
    priority: int | None = Field(default=None, ge=1, le=254)
    description: str | None = None
    enabled: bool = True

    model_config = ConfigDict(extra="ignore")


class HaApplyResult(BaseModel):
    applied: bool
    dry_run: bool
    message: str


class HaStatusOut(BaseModel):
    keepalived_active: bool
    keepalived_state: str = "unknown"
    conntrackd_active: bool
    conntrackd_state: str = "unknown"
    keepalived_installed: bool
    conntrackd_installed: bool
    keepalived_version: str | None = None
    conntrackd_version: str | None = None
    vrrp_instances: list[dict]
    conntrack_stats: dict


class HaInstallResult(BaseModel):
    installed: bool
    already_present: list[str]
    newly_installed: list[str]
    output_tail: str


class HaSyncConfigIn(BaseModel):
    enabled: bool = False
    peer_url: str = ""
    peer_token: str = ""
    sync_mode: str = "auto"
    verify_tls: bool = False


class HaSyncConfigOut(HaSyncConfigIn):
    id: int

    class Config:
        from_attributes = True


class HaSyncLogOut(BaseModel):
    id: int
    direction: str
    success: bool
    error: str | None
    duration_ms: int
    db_size_bytes: int
    triggered_by: str
    created_at: datetime

    class Config:
        from_attributes = True


class HaSyncRole(BaseModel):
    role: str  # MASTER / BACKUP / FAULT / STANDALONE
    writable: bool


class HaSyncPushResult(BaseModel):
    success: bool
    duration_ms: int
    db_size_bytes: int


class HaSyncTestResult(BaseModel):
    success: bool
    peer_role: str | None = None
    peer_version: str | None = None
    error: str | None = None


class HaSyncPingOut(BaseModel):
    """Reponse de GET /api/sync/ping : role VRRP + version backend."""
    role: str  # 'master' | 'backup' | 'unknown'
    version: str


class HaSyncToken(BaseModel):
    token: str


