# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Schemas Pydantic pour l'API (validation et serialisation)."""
import ipaddress
import re
from datetime import datetime
from typing import Annotated, Literal
from pydantic import AfterValidator, BaseModel, ConfigDict, Field

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



# --- Zones ---
class ZoneBase(BaseModel):
    name: str = Field(min_length=1, max_length=32)
    description: str | None = None


class ZoneCreate(ZoneBase):
    pass


class ZoneUpdate(BaseModel):
    name: str | None = None
    description: str | None = None


class ZoneOut(ZoneBase):
    id: int
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


# --- Interfaces ---
InterfaceType = Literal["physical", "vlan"]
IpMode = Literal["none", "static", "dhcp"]


class InterfaceBase(BaseModel):
    name: str = Field(min_length=1, max_length=32)
    description: str | None = None
    zone_id: int | None = None
    type: InterfaceType = "physical"
    parent_interface: str | None = Field(default=None, max_length=32)
    vlan_id: int | None = Field(default=None, ge=1, le=4094)
    ip_mode: IpMode = "none"
    ip_address: str | None = Field(default=None, max_length=64)
    gateway: str | None = Field(default=None, max_length=64)
    dns_servers: str | None = Field(default=None, max_length=255)
    mtu: int | None = Field(default=None, ge=68, le=9216)
    enabled: bool = True


class InterfaceCreate(InterfaceBase):
    pass


class InterfaceUpdate(BaseModel):
    description: str | None = None
    zone_id: int | None = None
    ip_mode: IpMode | None = None
    ip_address: str | None = None
    gateway: str | None = None
    dns_servers: str | None = None
    mtu: int | None = None
    enabled: bool | None = None


class InterfaceOut(InterfaceBase):
    id: int
    dirty: bool = False
    pending_delete: bool = False
    model_config = ConfigDict(from_attributes=True)



# --- System interfaces (lecture du systeme) ---
class SystemInterfaceOut(BaseModel):
    name: str
    state: str
    mtu: int
    mac: str | None = None
    addresses: list[str] = Field(default_factory=list)
    is_virtual: bool = False
    gateway: str | None = None



class NetworkEnvironmentOut(BaseModel):
    """Reponse de GET /api/network/environment.

    apply_enabled = MUROS_APPLY actif (effets reels sur le noyau).
    competing_managers = liste des daemons concurrents detectes
    (NetworkManager, systemd-networkd...) qui pourraient ecraser nos
    modifications. Ideal sur appliance : liste vide.
    """
    apply_enabled: bool
    competing_managers: list[str] = Field(default_factory=list)


class NetworkPendingIface(BaseModel):
    id: int
    name: str
    type: str
    ip_mode: str | None = None
    ip_address: str | None = None
    pending_delete: bool = False


class NetworkPendingRoute(BaseModel):
    id: int
    destination: str
    gateway: str | None = None
    metric: int | None = None


class NetworkPendingOut(BaseModel):
    """Changements reseau non encore appliques au noyau."""
    count: int
    interfaces: list[NetworkPendingIface] = Field(default_factory=list)
    routes: list[NetworkPendingRoute] = Field(default_factory=list)


class NetworkAdoptResult(BaseModel):
    """Resultat de POST /api/network/adopt : nombre d'objets adoptes."""
    interfaces_touched: int
    routes_touched: int
    skipped: bool


# --- Multi-WAN failover ---------------------------------------------------

WanStatus = Literal["up", "down", "unknown"]


def _validate_probe_target(v: str) -> str:
    """Cible de monitoring : IPv4 ou IPv6 valide (pas un hostname).

    On rejette les hostnames volontairement : un hostname ajoute une
    dependance DNS qui peut elle-meme tomber (et noyer le diagnostic
    'WAN down'). En multi-WAN, la cible doit etre une IP stable et
    independante des deux ISP (Cloudflare 1.1.1.1, Quad9 9.9.9.9, etc.).
    """
    try:
        ipaddress.ip_address(v)
    except ValueError:
        raise ValueError("Monitoring target must be an IP address (not a hostname)")
    return v


class WanGatewayIn(BaseModel):
    """Payload de creation / mise a jour d'un WAN gateway."""
    name: str = Field(min_length=1, max_length=64)
    interface_id: int
    gateway: Annotated[str, AfterValidator(_validate_address)]
    priority: int = Field(default=100, ge=1, le=10000)
    monitoring_target: Annotated[
        str, AfterValidator(_validate_probe_target)
    ] = "1.1.1.1"
    interval_s: int = Field(default=3, ge=1, le=60)
    failures_threshold: int = Field(default=3, ge=1, le=20)
    enabled: bool = True
    comment: str | None = Field(default=None, max_length=255)


class WanGatewayOut(BaseModel):
    """Reponse REST : id, conf + runtime status maintenu par le monitor."""
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    interface_id: int
    gateway: str
    priority: int
    monitoring_target: str
    interval_s: int
    failures_threshold: int
    enabled: bool
    comment: str | None = None
    status: WanStatus
    consecutive_failures: int
    consecutive_successes: int
    last_probe_at: datetime | None = None
    last_change_at: datetime | None = None


class WanActiveOut(BaseModel):
    """Reponse de GET /api/wan/active : quel WAN porte la default route.

    `active_id` peut etre None pendant la fenetre ou tous les WANs sont
    down (donc plus de connectivite internet). On expose `reason` pour
    aider le diagnostic depuis l'UI.
    """
    active_id: int | None = None
    active_name: str | None = None
    reason: Literal["healthy", "all_down", "disabled", "no_gateway"]
