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



# --- Firewall rules ---
class FirewallRuleBase(BaseModel):
    position: int = 0
    chain: Chain = "forward"
    action: Action

    src_zone_id: int | None = None
    dst_zone_id: int | None = None

    src_address: Address = None
    dst_address: Address = None

    protocol: Protocol | None = None
    src_port: Port = None
    dst_port: Port = None

    log: bool = False
    enabled: bool = True
    comment: str | None = None
    rate_limit: str | None = Field(default=None, max_length=64)

    # Reference optionnelle a un groupe. Si renseigne, le compilateur
    # utilise le groupe et ignore les champs equivalents (port pour le
    # service group, address pour les address groups).
    service_group_id: int | None = None
    src_address_group_id: int | None = None
    dst_address_group_id: int | None = None

    @field_validator("rate_limit")
    @classmethod
    def _check_rate_limit(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        import re
        # Format accepte : "N/unit" optionnellement suivi de "burst M"
        # unit = second | minute | hour | day
        if not re.match(
            r"^\d+\s*/\s*(second|minute|hour|day)(\s+burst\s+\d+)?$",
            v.strip(),
        ):
            raise ValueError(
                "invalid format. Examples : '5/minute', '100/second burst 200'"
            )
        return v.strip()


class FirewallRuleCreate(FirewallRuleBase):
    pass


class FirewallRuleUpdate(BaseModel):
    position: int | None = None
    chain: Chain | None = None
    action: Action | None = None
    src_zone_id: int | None = None
    dst_zone_id: int | None = None
    src_address: Address = None
    dst_address: Address = None
    protocol: Protocol | None = None
    src_port: Port = None
    dst_port: Port = None
    log: bool | None = None
    enabled: bool | None = None
    comment: str | None = None
    rate_limit: str | None = None
    service_group_id: int | None = None
    src_address_group_id: int | None = None
    dst_address_group_id: int | None = None


class FirewallRuleOut(FirewallRuleBase):
    id: int
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class FirewallReorderIn(BaseModel):
    chain: str
    rule_ids: list[int]


# --- NAT rules ---
class NatRuleBase(BaseModel):
    position: int = 0
    type: NatType
    interface_id: int | None = None

    src_address: Address = None
    dst_address: Address = None
    protocol: Protocol | None = None
    dst_port: Port = None

    redirect_to_ip: Address = None
    redirect_to_port: Port = None

    enabled: bool = True
    comment: str | None = None


class NatRuleCreate(NatRuleBase):
    pass


class NatRuleUpdate(BaseModel):
    position: int | None = None
    type: NatType | None = None
    interface_id: int | None = None
    src_address: Address = None
    dst_address: Address = None
    protocol: Protocol | None = None
    dst_port: Port = None
    redirect_to_ip: Address = None
    redirect_to_port: Port = None
    enabled: bool | None = None
    comment: str | None = None


class NatRuleOut(NatRuleBase):
    id: int
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class NatReorderIn(BaseModel):
    rule_ids: list[int]


class FirewallCounter(BaseModel):
    packets: int = 0
    bytes: int = 0


class FirewallStatsOut(BaseModel):
    """Live nft counters per rule, indexed by DB rule id (stringified
    because JSON objects only accept string keys).

    Counters reset on every Apply (ruleset reload) and may be missing
    if nft is unreachable or the ruleset has not been applied since
    boot. In that case both maps are simply empty.
    """
    rules: dict[str, FirewallCounter] = {}
    nat: dict[str, FirewallCounter] = {}


class FirewallPendingOut(BaseModel):
    """Pending counts for the firewall ruleset.

    Exposes how many rows in firewall_rules / nat_rules / zones are
    dirty (DB ahead of the kernel). UI surfaces this on the Apply
    button and the related page headers.
    """
    rules: int
    nat: int
    zones: int
    total: int


# --- Static routes ---
def _validate_destination(v: str | None) -> str | None:
    if v is None or v == "":
        return None
    if v == "default":
        return v
    try:
        ipaddress.ip_network(v, strict=False)
        return v
    except ValueError:
        raise ValueError(f"Invalid destination : {v!r} (expected CIDR or 'default')")


Destination = Annotated[str, AfterValidator(_validate_destination)]


class StaticRouteBase(BaseModel):
    destination: Destination
    gateway: Address = None
    interface_id: int | None = None
    metric: int = Field(default=0, ge=0, le=4294967295)
    enabled: bool = True
    comment: str | None = None


class StaticRouteCreate(StaticRouteBase):
    pass


class StaticRouteUpdate(BaseModel):
    destination: Destination | None = None
    gateway: Address = None
    interface_id: int | None = None
    metric: int | None = Field(default=None, ge=0, le=4294967295)
    enabled: bool | None = None
    comment: str | None = None


class StaticRouteOut(StaticRouteBase):
    id: int
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


# --- Preview ---
class RulesetPreview(BaseModel):
    ruleset: str



# --- Apply ---
class ApplyRequest(BaseModel):
    # Default left as None so the backend resolves the value from the
    # `apply_confirm_timeout` system setting (DB-backed, configurable
    # from the UI). When the client wants to override it (e.g. for a
    # scripted apply that needs more time), it can still pass an
    # explicit value within the allowed range.
    timeout_seconds: int | None = Field(default=None, ge=10, le=600)


class ApplyStatusOut(BaseModel):
    state: Literal["idle", "pending", "committed", "rolled_back", "failed"]
    started_at: datetime | None = None
    expires_at: datetime | None = None
    timeout_seconds: int = 60
    dry_run: bool = True
    message: str | None = None


class LogsStatusOut(BaseModel):
    rules_with_log: int
    rules_with_log_enabled: int
    journalctl_available: bool
    is_root: bool


class RulesetCheckOut(BaseModel):
    ok: bool
    message: str
    ruleset: str


