# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Schemas Pydantic pour l'API (validation et serialisation)."""
import ipaddress
import re
from typing import Annotated, Literal
from pydantic import AfterValidator, BaseModel, Field, field_validator, model_validator

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



# --- TLS UI ---

class TlsStatus(BaseModel):
    present: bool
    subject_cn: str | None
    issuer_cn: str | None
    san: list[str]
    not_before: str | None
    not_after: str | None
    days_remaining: int | None
    fingerprint_sha256: str | None
    is_self_signed: bool | None
    key_present: bool
    error: str | None = None


class TlsUploadIn(BaseModel):
    cert_pem: str
    key_pem: str


class TlsRegenerateIn(BaseModel):
    subject_cn: str | None = None
    san: list[str] | None = None
    validity_days: int = 3650


class TlsApplyResult(BaseModel):
    applied: bool
    message: str
    pending_apply_id: int | None = None
    rollback_timeout_seconds: int | None = None


# --- SSH ---

class SshConfigIn(BaseModel):
    """SSH config for a firewall appliance.

    No `enabled` flag is exposed any more : on a MurOS box the drop-in
    is always present, the operator chooses *how* sshd behaves (port,
    listen address, auth methods, root policy) but cannot toggle off
    the MurOS-managed configuration as a whole. Removing the drop-in
    would silently fall back to Debian defaults (port 22, password
    auth, root login by password) which is not a state we want to
    encourage from the UI.
    """
    port: int = Field(default=22, ge=1, le=65535)
    listen_address: str = Field(default="0.0.0.0", min_length=1, max_length=255)
    # Plages sshd raisonnables : entre 1 et 10 tentatives, intervalle 0-3600s.
    max_auth_tries: int = Field(default=3, ge=1, le=10)
    client_alive_interval: int = Field(default=300, ge=0, le=3600)
    client_alive_count_max: int = Field(default=2, ge=0, le=10)
    # Root login policy and authentication methods. Defaults are the
    # MurOS hardened baseline : root reachable by SSH key only, password
    # auth disabled, pubkey auth enabled.
    permit_root_login: Literal["yes", "no", "prohibit-password"] = "prohibit-password"
    password_authentication: bool = False
    pubkey_authentication: bool = True
    confirm_loopback: bool = False
    skip_rollback: bool = False

    @model_validator(mode="after")
    def _check_loopback(self):
        addr = (self.listen_address or "").strip()
        if (addr.startswith("127.") or addr == "::1") and not self.confirm_loopback:
            raise ValueError(
                "Adresse d'ecoute SSH loopback refusee : tu vas te lock-out. "
                "Coche 'confirmer loopback' si c'est volontaire."
            )
        return self


class SshConfigOut(SshConfigIn):
    id: int

    class Config:
        from_attributes = True
        # Le model DB a des champs en plus (permit_root_login, etc.) qu'on
        # ignore silencieusement ici.
        extra = "ignore"


class SshStatus(BaseModel):
    sshd_installed: bool
    service_active: bool
    service_state: str = "unknown"
    version: str | None
    dropin_present: bool
    dropin_path: str
    admin_disabled: bool = False


class SshServiceToggleIn(BaseModel):
    """Body for POST /api/ssh/service/toggle: flip sshd on or off.

    `enabled=False` runs `systemctl disable --now ssh` and persists
    `admin_disabled=True` in DB. `enabled=True` runs
    `systemctl enable --now ssh` and clears the flag. Reboots respect
    the flag (no auto re-enable when admin_disabled=True).
    """
    enabled: bool


class SshServiceToggleResult(BaseModel):
    applied: bool
    admin_disabled: bool
    service_active: bool
    message: str


class SshApplyResult(BaseModel):
    applied: bool
    message: str
    preview: str | None = None
    pending_apply_id: int | None = None
    rollback_timeout_seconds: int | None = None


class SshRootPasswordIn(BaseModel):
    new_password: str
    # Mot de passe MurOS UI de l'admin courant, exige comme verrou.
    # On ne demande pas l'ancien mdp root (souvent inconnu sur cloud-init),
    # mais on exige que l'admin se reauthentifie avec son mdp UI.
    current_ui_password: str


class SshRootPasswordResult(BaseModel):
    applied: bool
    message: str


class SshInstallResult(BaseModel):
    installed: bool
    already_present: list[str]
    newly_installed: list[str]
    output_tail: str


class SshAuthorizedKey(BaseModel):
    type: str
    key_b64: str
    comment: str
    fingerprint: str
    line: int = 0


class SshKeyAdd(BaseModel):
    key_text: str


class SshKeyAddResult(BaseModel):
    added: bool
    fingerprint: str | None = None
    message: str | None = None


class ListenAddressOut(BaseModel):
    label: str
    address: str
    interface: str
    loopback: bool


# --- HTTP / nginx config ---

class HttpConfigIn(BaseModel):
    listen_address: str = Field(default="0.0.0.0", min_length=1, max_length=255)
    port_https: int = Field(default=443, ge=1, le=65535)
    port_http: int = Field(default=80, ge=1, le=65535)
    # Garde-fou : refuse une adresse loopback (127.x ou ::1) sans confirmation
    # explicite, sinon on lock-out l'admin qui se connecte depuis le LAN.
    confirm_loopback: bool = False
    # Si True : pas de pending_apply, pas de rollback. A utiliser quand
    # l'admin sait qu'il va changer d'IP/d'interface et ne pourra donc
    # plus confirmer depuis la session courante.
    skip_rollback: bool = False

    @field_validator("listen_address")
    @classmethod
    def _no_silent_loopback(cls, v: str, info):
        # On valide la combinaison apres validation de tous les champs via
        # model_validator. Ici on se contente de normaliser.
        return v.strip() or "0.0.0.0"

    @model_validator(mode="after")
    def _check_loopback(self):
        addr = (self.listen_address or "").strip()
        if (addr.startswith("127.") or addr == "::1") and not self.confirm_loopback:
            raise ValueError(
                "Adresse d'ecoute loopback (127.x / ::1) refusee : "
                "tu vas te lock-out de l'UI. Coche 'confirmer loopback' "
                "si c'est volontaire (acces uniquement via tunnel SSH)."
            )
        return self
    redirect_http_to_https: bool = True


class HttpConfigOut(HttpConfigIn):
    id: int

    class Config:
        from_attributes = True


class HttpApplyResult(BaseModel):
    applied: bool
    message: str
    preview: str | None = None
    pending_apply_id: int | None = None
    rollback_timeout_seconds: int | None = None


