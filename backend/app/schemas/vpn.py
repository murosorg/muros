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



# --- VPN : WireGuard ---

class WireGuardInterface(BaseModel):
    name: str
    peers: int
    listen_port: int | None


class WireGuardStatus(BaseModel):
    installed: bool
    version: str | None
    interfaces: list[WireGuardInterface]
    service_active: bool
    service_state: str = "unknown"


class WireGuardInstallResult(BaseModel):
    installed: bool
    already_present: list[str]
    newly_installed: list[str]
    output_tail: str


# --- VPN : IPsec (strongSwan) ---

class IpsecSa(BaseModel):
    name: str
    state: str
    details: str


class IpsecStatus(BaseModel):
    installed: bool
    version: str | None
    service_active: bool
    service_state: str = "unknown"
    service_name: str | None
    active_sas: list[IpsecSa]
    # Global toggle (singleton id=1). Defaults True for compat with
    # older deployments where the row may not exist yet.
    globally_enabled: bool = True


class IpsecGlobalConfig(BaseModel):
    """Global IPsec server toggle (singleton id=1)."""
    enabled: bool


class IpsecInstallResult(BaseModel):
    installed: bool
    already_present: list[str]
    newly_installed: list[str]
    output_tail: str


# --- WireGuard : config + peers ---

class WireGuardConfigIn(BaseModel):
    enabled: bool = False
    interface_name: str = Field(default="wg0", min_length=1, max_length=15, pattern=r"^[a-zA-Z][a-zA-Z0-9_-]*$")
    address_cidr: str = ""
    listen_port: int = Field(default=51820, ge=1, le=65535)
    private_key: str = ""
    public_key: str = ""
    mtu: int | None = Field(default=None, ge=576, le=9000)
    public_endpoint: str = Field(default="", max_length=255)


class WireGuardConfigOut(WireGuardConfigIn):
    id: int

    model_config = ConfigDict(from_attributes=True)


class WireGuardPeerIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    public_key: str = Field(min_length=1)
    preshared_key: str | None = None
    allowed_ips: str = Field(min_length=1)
    # Routes pushed to the client (its [Peer] AllowedIPs). Empty -> default
    # full tunnel "0.0.0.0/0, ::/0" applied at export time.
    client_allowed_ips: str = ""
    endpoint: str | None = None
    persistent_keepalive: int = Field(default=0, ge=0, le=65535)
    description: str | None = None
    enabled: bool = True


class WireGuardPresharedKeyOut(BaseModel):
    # Reponse de POST /api/wireguard/psk : une cle WG generee (base64 32 octets)
    # a coller dans le champ Preshared key d'un peer (optionnel mais recommande).
    preshared_key: str


class WireGuardPeerOut(WireGuardPeerIn):
    id: int

    model_config = ConfigDict(from_attributes=True)


class WireGuardKeypair(BaseModel):
    private_key: str
    public_key: str


class WireGuardApplyResult(BaseModel):
    message: str
    interface: str | None = None
    config_preview: str | None = None


class WireGuardPeerExport(BaseModel):
    config_text: str
    qr_svg: str | None = None


# --- IPsec : connexions ---

class IpsecConnectionIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    auth_mode: str = "psk"  # "psk" ou "cert"
    local_addrs: str = "%any"
    remote_addrs: str
    local_id: str | None = None
    remote_id: str | None = None
    psk: str = ""
    local_cert_id: int | None = None
    remote_cert_id: int | None = None
    local_ts: str = "0.0.0.0/0"
    remote_ts: str = "0.0.0.0/0"
    ike_proposals: str = "aes256-sha256-modp2048"
    esp_proposals: str = "aes256-sha256"
    start_action: str = "start"
    description: str | None = None
    enabled: bool = True


class IpsecConnectionOut(IpsecConnectionIn):
    id: int

    model_config = ConfigDict(from_attributes=True)


class IpsecApplyResult(BaseModel):
    message: str
    service: str | None = None
    swanctl_output: str | None = None
    conf_preview: str | None = None


class IpsecCaOut(BaseModel):
    id: int
    subject_cn: str
    subject_o: str
    cert_pem: str
    validity_days: int
    created_at: datetime
    expires_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class IpsecCaGenerate(BaseModel):
    subject_cn: str = "MurOS Root CA"
    subject_o: str = "MurOS"
    validity_days: int = 3650


class IpsecCertOut(BaseModel):
    id: int
    name: str
    subject_cn: str
    san: str | None
    cert_pem: str
    is_local: bool
    serial: str
    revoked: bool
    revoked_at: datetime | None
    validity_days: int
    created_at: datetime
    expires_at: datetime | None
    # On expose has_key (presence d'une cle privee) mais pas la cle elle-meme.
    has_key: bool

    model_config = ConfigDict(from_attributes=True)


class IpsecCertGenerate(BaseModel):
    name: str
    subject_cn: str
    san: str | None = None
    validity_days: int = 825
    is_local: bool = True


class IpsecCertImport(BaseModel):
    name: str
    cert_pem: str


