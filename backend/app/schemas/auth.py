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



# --- Auth ---
class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    must_change_password: bool = False


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


class UserOut(BaseModel):
    id: int
    username: str
    is_admin: bool
    must_change_password: bool
    last_login: datetime | None = None
    model_config = ConfigDict(from_attributes=True)


# --- User management (root grants web UI access to Linux accounts) ---
class UserAdminOut(BaseModel):
    id: int
    username: str
    is_admin: bool
    ui_access: bool
    must_change_password: bool
    last_login: datetime | None = None
    # True when the account still exists in the system passwd database.
    # A row can outlive its Linux account (account deleted from the
    # shell); the UI greys those out so root can clean them up.
    exists_on_system: bool = True
    model_config = ConfigDict(from_attributes=True)


class UsersListOut(BaseModel):
    users: list[UserAdminOut]
    # Linux login accounts not yet granted UI access, offered in the
    # "Grant access" picker.
    grantable_accounts: list[str]


class GrantAccessRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    # Promote the account to administrator (can manage other users).
    is_admin: bool = False


class UpdateUserRequest(BaseModel):
    ui_access: bool | None = None
    is_admin: bool | None = None


class PasswordPolicyOut(BaseModel):
    # Expose les regles de mot de passe pour affichage cote UI (page
    # changement de password). On garde toutes les regles ici plutot que
    # de les hardcoder cote TSX : une seule source de verite.
    min_length: int
    require_uppercase: bool = True
    require_lowercase: bool = True
    require_digit: bool = True
    require_special: bool = True
    forbid_common: bool = True
    forbid_username: bool = True


