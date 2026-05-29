# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Schemas Pydantic pour l'API (validation et serialisation)."""
import ipaddress
import re
from datetime import datetime
from typing import Annotated, Literal
from pydantic import AfterValidator, BaseModel, ConfigDict

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



# --- Backups (snapshots de configuration) ---
class BackupOut(BaseModel):
    name: str
    size_bytes: int
    created_at: str
    label: str = ""
    manifest: dict = {}


class BackupCreateRequest(BaseModel):
    label: str | None = None


class BackupRestoreResult(BaseModel):
    restored: str
    db_restored: bool
    extracted_to: str
    manifest: dict = {}


# --- NTP ---
class NtpStatusOut(BaseModel):
    model_config = ConfigDict(extra="allow")
    available: bool


class NtpServersIn(BaseModel):
    servers: list[str]


class NtpServersOut(BaseModel):
    servers: list[str]
    config_path: str


# --- DNS ---
class DnsConfigOut(BaseModel):
    # MurOS ecrit /etc/resolv.conf directement (mode fichier plat). Pas
    # de gestion systemd-resolved, pas de drop-in, pas de detection de
    # backend : on est seul maitre du fichier.
    resolvers: list[str]
    search_domains: list[str] = []
    config_path: str = "/etc/resolv.conf"


class DnsConfigIn(BaseModel):
    resolvers: list[str]
    search_domains: list[str] = []


# --- Updates ---
class UpdatePackageOut(BaseModel):
    name: str
    new_version: str
    current_version: str


class UpdateStatusOut(BaseModel):
    last_check_at: str | None = None
    packages: list[UpdatePackageOut]
    packages_count: int
    apt_available: bool


class UpdateInstallResult(BaseModel):
    installed: bool
    output_tail: str
    snapshot: dict | None = None


class MurosUpdateStatusOut(BaseModel):
    apt_available: bool
    installed: str | None = None
    candidate: str | None = None
    upgrade_available: bool
    pending_packages: list[UpdatePackageOut] = []
    last_check_at: str | None = None
    deb_url: str | None = None
    release_notes: str | None = None
    release_published_at: str | None = None


# --- Hardening sysctl ---
class HardeningItemOut(BaseModel):
    key: str
    recommended: str
    current: str | None
    managed_by_muros: bool
    ok: bool
    description: str = ""
    category: str = "Securite"


class HardeningStatusOut(BaseModel):
    items: list[HardeningItemOut]
    hardened: bool
    dropin_path: str
    dropin_exists: bool
    apply_enabled: bool


# --- Backup distant (rsync) ---
class BackupRemoteConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")
    enabled: bool = False
    host: str = ""
    user: str = ""
    port: int = 22
    path: str = ""
    ssh_key_path: str = ""
    last_push_at: str | None = None
    last_error: str | None = None


class BackupRemoteConfigIn(BaseModel):
    enabled: bool | None = None
    host: str | None = None
    user: str | None = None
    port: int | None = None
    path: str | None = None
    ssh_key_path: str | None = None


class BackupPushResult(BaseModel):
    pushed: bool
    dry_run: bool
    message: str
    command: str | None = None
    output_tail: list[str] | None = None


class BackupRemoteTestResult(BaseModel):
    ok: bool
    dry_run: bool
    message: str


class SshKeyOut(BaseModel):
    exists: bool = False
    generated: bool = False
    dry_run: bool = False
    message: str = ""
    key_path: str
    public_key: str


class SshKeyGenerateRequest(BaseModel):
    force: bool = False


# --- Pending changes (rollback temporise) ---
class PendingChangeOut(BaseModel):
    id: str
    # ``nftables`` is included because the unified rollback manager now
    # also handles the firewall apply ticket; the /api/pending endpoint
    # still filters its output to the network-level kinds, but the
    # schema accepts all of them so a future caller can list everything
    # in one place without bumping the contract.
    kind: Literal["interface", "route", "vlan", "nftables"]
    description: str
    started_at: str
    expires_at: str
    timeout_seconds: int
    state: Literal["pending", "committed", "rolled_back", "rollback_failed"]
    message: str | None = None
    detail: dict = {}
    persistent: bool = False



# --- System actions (reboot / shutdown) ---

class SystemActionResult(BaseModel):
    scheduled: bool
    message: str


class DiagCommandResult(BaseModel):
    command: str
    returncode: int
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool


class DiagPingIn(BaseModel):
    target: str
    count: int = 4


class DiagTracerouteIn(BaseModel):
    target: str
    max_hops: int = 20


class DiagDnsIn(BaseModel):
    target: str
    record_type: str = "A"
    resolver: str | None = None


class DiagCaptureIn(BaseModel):
    interface: str
    count: int = 50
    filter_expr: str | None = None


class DiagConntrackIn(BaseModel):
    filter: str | None = None
    limit: int = 200


class DiagPortTestIn(BaseModel):
    target: str
    port: int
    protocol: str = "tcp"  # tcp | udp
    timeout: int = 5


class DiagPublicIpIn(BaseModel):
    # auto = let curl pick (default), v4 = force -4, v6 = force -6.
    family: str = "auto"


class SystemServiceOut(BaseModel):
    unit: str
    display_name: str
    page: str
    category: str
    status: str  # active / inactive / failed / activating / unknown / disabled_by_admin
    admin_disabled: bool = False


class AuditLogOut(BaseModel):
    id: int
    timestamp: datetime
    user_id: int | None
    username: str | None
    method: str
    path: str
    status_code: int
    client_ip: str | None
    duration_ms: int
    action_summary: str | None

    class Config:
        from_attributes = True


