# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""MurOS data models.

Concepts:
- Zone: logical group of interfaces (wan, lan, dmz, ...)
- Interface: physical or virtual network interface, attached to a zone
- FirewallRule: filtering rule (input/forward/output chain)
- NatRule: translation rule (masquerade, snat, dnat)
"""
from datetime import datetime, timezone
from sqlalchemy import String, Integer, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    # Web UI access gate. The MurOS web UI and SSH share the system PAM
    # stack, so any local Linux account can in theory pass authentication.
    # ui_access decides whether that account is allowed into the web UI:
    # only 'root' is granted by default, every other account stays locked
    # out until root explicitly enables it from the Access > Users page.
    ui_access: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # TOTP two-factor authentication (RFC 6238). totp_secret holds the
    # base32 shared secret; totp_enabled is only set to True once the user
    # has confirmed a valid code during enrolment. When enabled, the login
    # flow requires a second step (the 6-digit code) after PAM validates
    # the password.
    totp_secret: Mapped[str | None] = mapped_column(String(64))
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_login: Mapped[datetime | None] = mapped_column(DateTime)


class Zone(Base):
    __tablename__ = "zones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    # True when the DB row diverges from the live nft ruleset. Cleared
    # on successful firewall apply. Drives the "N pending" badge on the
    # Zones / Filter rules / NAT pages.
    dirty: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    interfaces: Mapped[list["Interface"]] = relationship(back_populates="zone")


class Interface(Base):
    __tablename__ = "interfaces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String(255))
    zone_id: Mapped[int | None] = mapped_column(ForeignKey("zones.id", ondelete="SET NULL"))

    # Interface type:
    # - 'physical': real NIC (eth0, ens3...), MurOS does not create it
    # - 'vlan'    : 802.1q VLAN interface, MurOS creates it via `ip link add ... type vlan`
    type: Mapped[str] = mapped_column(String(16), nullable=False, default="physical")
    parent_interface: Mapped[str | None] = mapped_column(String(32))  # eth0 for a VLAN eth0.100
    vlan_id: Mapped[int | None] = mapped_column(Integer)              # 1-4094

    # IP configuration: 'static', 'dhcp' or 'none' (do not configure)
    ip_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="none")
    ip_address: Mapped[str | None] = mapped_column(String(64))    # CIDR if static
    gateway: Mapped[str | None] = mapped_column(String(64))
    dns_servers: Mapped[str | None] = mapped_column(String(255))  # comma-separated list
    mtu: Mapped[int | None] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # dirty=True: change in DB not yet applied to the kernel (cf POST /api/network/apply)
    dirty: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, server_default="1")
    # pending_delete=True: VLAN marked for deletion, finalized on apply
    # (symmetric with VLAN add, which is also deferred to apply).
    pending_delete: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, server_default="0")

    zone: Mapped[Zone | None] = relationship(back_populates="interfaces")


class FirewallRule(Base):
    __tablename__ = "firewall_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # forward = traffic crossing the firewall (lan -> wan, dmz -> lan, ...)
    # input = traffic destined to the firewall itself
    # output = traffic emitted by the firewall
    chain: Mapped[str] = mapped_column(String(16), nullable=False, default="forward")

    action: Mapped[str] = mapped_column(String(16), nullable=False)  # accept, drop, reject

    src_zone_id: Mapped[int | None] = mapped_column(ForeignKey("zones.id", ondelete="SET NULL"))
    dst_zone_id: Mapped[int | None] = mapped_column(ForeignKey("zones.id", ondelete="SET NULL"))

    src_address: Mapped[str | None] = mapped_column(String(64))   # 192.168.1.0/24, any
    dst_address: Mapped[str | None] = mapped_column(String(64))

    protocol: Mapped[str | None] = mapped_column(String(8))       # tcp, udp, icmp
    src_port: Mapped[str | None] = mapped_column(String(64))      # 22 ou 1024-2048 ou 22,80,443
    dst_port: Mapped[str | None] = mapped_column(String(64))

    log: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    comment: Mapped[str | None] = mapped_column(String(255))

    # See Zone.dirty. Set on every create/update/delete/reorder, cleared
    # by POST /api/firewall/apply once nft is reloaded successfully.
    dirty: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # nftables rate limit: e.g. "5/minute" or "100/second burst 200".
    # If set, the compiler adds `limit rate <value>` before the action.
    # Useful for SSH anti-bruteforce, ICMP anti-flood, DNS throttling.
    rate_limit: Mapped[str | None] = mapped_column(String(64))

    # Groups (optional). If set, they take precedence over the equivalent
    # string fields (src_address, dst_address, dst_port/protocol).
    # The compiler expands the group into an inline nftables set.
    service_group_id: Mapped[int | None] = mapped_column(
        ForeignKey("service_groups.id", ondelete="SET NULL"))
    src_address_group_id: Mapped[int | None] = mapped_column(
        ForeignKey("address_groups.id", ondelete="SET NULL"))
    dst_address_group_id: Mapped[int | None] = mapped_column(
        ForeignKey("address_groups.id", ondelete="SET NULL"))

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    src_zone: Mapped[Zone | None] = relationship(foreign_keys=[src_zone_id])
    dst_zone: Mapped[Zone | None] = relationship(foreign_keys=[dst_zone_id])
    service_group: Mapped["ServiceGroup | None"] = relationship(
        foreign_keys=[service_group_id])
    src_address_group: Mapped["AddressGroup | None"] = relationship(
        foreign_keys=[src_address_group_id])
    dst_address_group: Mapped["AddressGroup | None"] = relationship(
        foreign_keys=[dst_address_group_id])


class ServiceGroup(Base):
    """Service group (ports + protocol) reusable across rules.

    Example: 'LDAP' = tcp/389 + tcp/636, 'AD' = tcp/389 + tcp/636 +
    tcp/3268 + tcp/3269 + tcp/88 + udp/88 + tcp/445.
    """
    __tablename__ = "service_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    ports: Mapped[list["ServiceGroupPort"]] = relationship(
        back_populates="group", cascade="all, delete-orphan", lazy="joined",
        order_by="ServiceGroupPort.id")


class ServiceGroupPort(Base):
    """A port (or range) belonging to a service group."""
    __tablename__ = "service_group_ports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(
        ForeignKey("service_groups.id", ondelete="CASCADE"), nullable=False)
    protocol: Mapped[str] = mapped_column(String(8), nullable=False)  # tcp, udp
    port: Mapped[str] = mapped_column(String(32), nullable=False)  # '80' or '1024-2048'

    group: Mapped[ServiceGroup] = relationship(back_populates="ports")


class AddressGroup(Base):
    """Address group (IP, CIDR) reusable across rules.

    Example: 'LAN admin' = 192.168.10.0/24, 10.0.0.0/8.
    """
    __tablename__ = "address_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    entries: Mapped[list["AddressGroupEntry"]] = relationship(
        back_populates="group", cascade="all, delete-orphan", lazy="joined",
        order_by="AddressGroupEntry.id")


class AddressGroupEntry(Base):
    """An address (IP or CIDR) belonging to an address group."""
    __tablename__ = "address_group_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(
        ForeignKey("address_groups.id", ondelete="CASCADE"), nullable=False)
    value: Mapped[str] = mapped_column(String(64), nullable=False)  # CIDR or IP

    group: Mapped[AddressGroup] = relationship(back_populates="entries")


class StaticRoute(Base):
    __tablename__ = "static_routes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    destination: Mapped[str] = mapped_column(String(64), nullable=False)  # CIDR or "default"
    gateway: Mapped[str | None] = mapped_column(String(64))
    interface_id: Mapped[int | None] = mapped_column(ForeignKey("interfaces.id", ondelete="SET NULL"))
    metric: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    comment: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    dirty: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, server_default="1")

    interface: Mapped[Interface | None] = relationship()


class WanGateway(Base):
    """Multi-WAN failover: one WAN gateway = one internet uplink.

    The muros-wan-monitor daemon probes `monitoring_target` every
    `interval_s` through the interface (`-I` option) and counts
    consecutive failures. Beyond `failures_threshold`, the WAN goes
    `down` and the monitor rewrites the default route via the next WAN
    that is UP (lowest priority). The return to UP is confirmed by
    `failures_threshold` consecutive successful probes (anti-flap).

    Runtime status is stored directly on the row to avoid a secondary
    table and to serve it to the UI through the same REST GET.
    """

    __tablename__ = "wan_gateways"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    interface_id: Mapped[int] = mapped_column(
        ForeignKey("interfaces.id", ondelete="CASCADE"), nullable=False
    )
    gateway: Mapped[str] = mapped_column(String(64), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    monitoring_target: Mapped[str] = mapped_column(
        String(64), default="1.1.1.1", nullable=False
    )
    interval_s: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    failures_threshold: Mapped[int] = mapped_column(
        Integer, default=3, nullable=False
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    comment: Mapped[str | None] = mapped_column(String(255))
    # Runtime status, updated by the monitor. Not indexed, rarely read.
    status: Mapped[str] = mapped_column(
        String(16), default="unknown", nullable=False, server_default="unknown"
    )
    consecutive_failures: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, server_default="0"
    )
    consecutive_successes: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, server_default="0"
    )
    last_probe_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_change_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    interface: Mapped[Interface] = relationship()


class DhcpConfig(Base):
    """Singleton holding the global DHCP server settings (Kea DHCPv4).

    MurOS uses ISC Kea as a DHCP-only server; it never binds port 53, so
    it coexists with Unbound (recursive DNS) without any collision. Kea
    stays running at all times: while `enabled=False` (or no pool is
    defined) the rendered config is idle and serves nothing. Each apply
    regenerates /etc/kea/kea-dhcp4.conf and restarts kea-dhcp4-server.
    """
    __tablename__ = "dhcp_config"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # When True, dnsmasq replies DHCPNAK to clients holding a lease from
    # another DHCP server on the same subnet. Safe to enable once MurOS
    # is the only DHCP on the segment.
    authoritative: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    default_lease_seconds: Mapped[int] = mapped_column(Integer, default=43200, nullable=False)  # 12h
    domain: Mapped[str | None] = mapped_column(String(255))


class DhcpPool(Base):
    """One DHCP range bound to a single interface (one subnet).

    Kea derives the subnet CIDR from the interface address, so a pool is
    tied to exactly one interface; we enforce a unique constraint at the
    column level and re-check at the API layer to return a clean 400
    instead of a 500.
    """
    __tablename__ = "dhcp_pools"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    interface_id: Mapped[int] = mapped_column(
        ForeignKey("interfaces.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    range_start: Mapped[str] = mapped_column(String(64), nullable=False)
    range_end: Mapped[str] = mapped_column(String(64), nullable=False)
    # Optional gateway pushed to clients (DHCP option routers). Empty ->
    # MurOS uses the interface IP itself (the common case).
    gateway: Mapped[str | None] = mapped_column(String(64))
    # CSV of DNS servers handed to clients. Empty -> MurOS pushes the
    # interface IP, which resolves through Unbound on the box. Standard
    # PME case.
    dns_servers: Mapped[str | None] = mapped_column(String(512))
    lease_seconds: Mapped[int | None] = mapped_column(Integer)  # NULL = inherit global default
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    comment: Mapped[str | None] = mapped_column(String(255))

    interface: Mapped[Interface] = relationship()


class DhcpStaticLease(Base):
    """Static MAC-to-IP reservation served as a Kea host reservation.

    Lets fixed hosts (printers, NAS, servers) always receive the same IP
    over DHCP without touching their local config.
    """
    __tablename__ = "dhcp_static_leases"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pool_id: Mapped[int] = mapped_column(
        ForeignKey("dhcp_pools.id", ondelete="CASCADE"), nullable=False
    )
    mac: Mapped[str] = mapped_column(String(32), nullable=False)
    ip: Mapped[str] = mapped_column(String(64), nullable=False)
    hostname: Mapped[str | None] = mapped_column(String(255))
    comment: Mapped[str | None] = mapped_column(String(255))

    pool: Mapped[DhcpPool] = relationship()


class DnsConfig(Base):
    """Singleton holding the recursive DNS settings (Unbound).

    Unbound validates DNSSEC by default. Forwarders are optional: when
    empty Unbound performs full recursion from the root servers (the
    recommended default unless the upstream ISP filters outbound :53).
    """
    __tablename__ = "dns_config"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # CIDRs allowed to query (LAN typically). CSV. Empty = refuse all,
    # which is the Unbound default. Always set at least 127.0.0.0/8 so
    # the box itself can resolve when used as system resolver.
    allow_query_cidrs: Mapped[str] = mapped_column(
        String(1024), default="127.0.0.0/8", nullable=False
    )
    dnssec: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    prefetch: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Forwarders : when non empty Unbound forwards there instead of
    # recursing (useful behind an ISP that blocks outbound port 53).
    forwarders: Mapped[str | None] = mapped_column(String(1024))  # CSV of IPs
    # When True, MurOS itself uses Unbound as its system resolver
    # (/etc/resolv.conf -> nameserver 127.0.0.1 + fallback). A fallback
    # resolver is always appended so apt/curl keep working if Unbound
    # is stopped. Opt-in : default False.
    use_as_system_resolver: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # DHCP <-> DNS integration: when True, MurOS publishes the DHCP
    # reservations (and current dynamic leases) as local DNS records under
    # `lease_domain`, so LAN clients resolve each other by hostname
    # (e.g. nas.lan -> 192.168.1.10). Static reservations are DB-driven
    # and deterministic; dynamic leases are read from the Kea lease file
    # whenever the DNS config is applied.
    register_dhcp_leases: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    lease_domain: Mapped[str] = mapped_column(String(63), default="lan", nullable=False)


class DnsLocalRecord(Base):
    """Local DNS records served by Unbound (authoritative local-zone).

    Lets the admin map firewall.local -> 192.168.1.1 or nas.local ->
    192.168.1.10 without touching public DNS. v1.2 supports A/AAAA only,
    other types come later if real demand shows up.
    """
    __tablename__ = "dns_local_records"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    record_type: Mapped[str] = mapped_column(String(8), default="A", nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    value: Mapped[str] = mapped_column(String(255), nullable=False)
    comment: Mapped[str | None] = mapped_column(String(255))


class NtpConfig(Base):
    """Singleton holding the NTP (chrony) server-mode settings.

    chrony always runs as a time client (it keeps the box clock in sync
    from the configured upstream servers). When `serve_lan` is True (the
    default, OPNsense-like), MurOS additionally turns chrony into an NTP
    server for the LAN: it emits an `allow <subnet>` directive for every
    LAN-side network (every static interface whose zone is not a WAN
    zone). The WAN is never served, to avoid NTP reflection/amplification
    abuse. The upstream server list itself lives in the chrony drop-in
    `/etc/chrony/conf.d/muros.conf`.
    """
    __tablename__ = "ntp_config"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    serve_lan: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # When False, chrony is stopped and disabled at boot. Time sync is then
    # off until the admin re-enables it from the NTP page. Default on.
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class RaConfig(Base):
    """IPv6 Router Advertisements (radvd) for the LAN.

    When enabled, MurOS advertises itself as the IPv6 router on the chosen
    LAN interface so clients autoconfigure an address (SLAAC) and a default
    route. The advertised /64 prefix is derived from the interface's own
    IPv6 address. The M (managed) and O (other-config) flags tell clients
    whether to also use DHCPv6. This is the IPv6 counterpart of the DHCP
    server (which only serves IPv4).
    """
    __tablename__ = "ra_config"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    interface: Mapped[str | None] = mapped_column(String(32))
    # M flag: clients obtain their address via DHCPv6 instead of SLAAC.
    managed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # O flag: clients use DHCPv6 for "other" info (e.g. DNS) on top of SLAAC.
    other_config: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Advertise the firewall as the IPv6 recursive resolver (RDNSS option).
    advertise_dns: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class NatRule(Base):
    __tablename__ = "nat_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # masquerade = auto SNAT to the outgoing interface IP (typically LAN -> WAN)
    # snat       = explicit SNAT to a given IP
    # dnat       = redirection (port forwarding) to an internal IP/port
    type: Mapped[str] = mapped_column(String(16), nullable=False)

    # Interface involved (egress for SNAT/masquerade, ingress for DNAT)
    interface_id: Mapped[int | None] = mapped_column(ForeignKey("interfaces.id", ondelete="SET NULL"))

    src_address: Mapped[str | None] = mapped_column(String(64))
    dst_address: Mapped[str | None] = mapped_column(String(64))  # public IP for DNAT
    protocol: Mapped[str | None] = mapped_column(String(8))
    dst_port: Mapped[str | None] = mapped_column(String(64))     # external port for DNAT

    # For SNAT: replacement source IP
    # For DNAT: internal target IP
    redirect_to_ip: Mapped[str | None] = mapped_column(String(64))
    redirect_to_port: Mapped[str | None] = mapped_column(String(64))

    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    comment: Mapped[str | None] = mapped_column(String(255))

    # See Zone.dirty. NAT rules are in the same nft ruleset as filter
    # rules, so a single Apply on /api/firewall/apply clears both.
    dirty: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    interface: Mapped[Interface | None] = relationship()


class HaConfig(Base):
    """High-availability singleton config.

    A single row is stored (id=1). The UI reads/writes this singleton.
    """
    __tablename__ = "ha_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # 'primary' or 'secondary': sets the base VRRP priority
    role: Mapped[str] = mapped_column(String(16), default="primary", nullable=False)
    # IP of the other node (used by conntrackd)
    peer_address: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    # Interface dedicated to conntrackd sync (cross-link recommended)
    sync_interface: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    # conntrack sync is always on: without it, a failover breaks every
    # existing TCP connection, which defeats the purpose of HA. The field
    # stays in the DB so the migration does not break, but the UI no
    # longer exposes it and Pydantic forces True on write.
    conntrack_sync: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # preempt = the primary takes back over as soon as it returns (recommended)
    preempt: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow,
    )


class FirewallApplyState(Base):
    """Singleton driving the firewall "Apply" badge.

    The pending counter is computed by summing `dirty=True` across
    firewall_rules / nat_rules / zones. Problem: when the LAST rule of a
    chain (or the last zone, or the last NAT rule) is DELETED, there is
    no row left to flag dirty. The kernel still holds the old ruleset,
    though, so the admin must still click Apply.

    This singleton receives the global `dirty=True` in that case, and
    more generally whenever the mutation is destructive (delete /
    cascade). The `total` counter of /pending adds 1 if this singleton
    is dirty, and POST /apply clears everything in one go.
    """
    __tablename__ = "firewall_apply_state"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    dirty: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_applied_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)


class WireGuardConfig(Base):
    """Singleton config for the WireGuard interface `wg0`.

    A single interface is managed by default, which covers 90% of cases.
    If multiple interfaces are needed later, this will be turned into a
    collection.
    """
    __tablename__ = "wireguard_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # WG interface name (default wg0). Must be <= 15 chars.
    interface_name: Mapped[str] = mapped_column(String(15), default="wg0", nullable=False)
    # Firewall IP on the WG tunnel, CIDR format (e.g. 10.10.0.1/24).
    address_cidr: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    # UDP listen port (default 51820).
    listen_port: Mapped[int] = mapped_column(Integer, default=51820, nullable=False)
    # Server private key (base64). Generated from the UI.
    private_key: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    # Derived public key (rendered by the UI but stored to avoid recompute).
    public_key: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    # Optional MTU (WG default = 1420).
    mtu: Mapped[int | None] = mapped_column(Integer)
    # Public endpoint advertised to clients (FQDN or public IP, no port).
    # Used to fill the Endpoint = host:port line in exported client configs.
    # Empty -> a <FIREWALL-PUBLIC-IP> placeholder is rendered instead.
    public_endpoint: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class WireGuardPeer(Base):
    """WireGuard peer: a road-warrior client or a remote site."""
    __tablename__ = "wireguard_peers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    # Peer public key (base64, exactly 44 chars).
    public_key: Mapped[str] = mapped_column(String(64), nullable=False)
    # Optional pre-shared key (PSK), adds an extra security layer.
    preshared_key: Mapped[str | None] = mapped_column(String(64))
    # Server-side AllowedIPs: networks reachable through this peer.
    # E.g. road-warrior: 10.10.0.2/32. Site: 10.10.0.2/32, 192.168.42.0/24.
    allowed_ips: Mapped[str] = mapped_column(String(255), nullable=False)
    # CLIENT-side AllowedIPs: networks the client will route into the tunnel.
    # This is what appears in the [Peer] section of the config exported to
    # the client (hence what the client can reach). Default "0.0.0.0/0, ::/0"
    # = full tunnel. For a split tunnel, set e.g. "10.10.0.0/24,
    # 192.168.1.0/24". Empty field -> falls back to the full-tunnel default.
    client_allowed_ips: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    # Remote endpoint (host:port), optional for road-warriors.
    endpoint: Mapped[str | None] = mapped_column(String(128))
    # Persistent keepalive in seconds (25 recommended behind NAT).
    persistent_keepalive: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    description: Mapped[str | None] = mapped_column(String(255))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class IpsecGlobalConfig(Base):
    """Singleton (id=1) carrying the global IPsec service toggle.

    Lets operators stop the IPsec server entirely from the UI without
    having to disable every connection one by one. When `enabled` is
    False, apply_config always tears down strongswan and removes its
    on-disk conf, regardless of how many connections are marked active.
    """
    __tablename__ = "ipsec_global_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow,
    )


class IpsecConnection(Base):
    """Site-to-site IPsec/IKEv2 connection (strongSwan/swanctl).

    Supports 2 authentication modes:
      - psk: pre-shared key (psk field set)
      - cert: X.509 certificate authentication (local_cert_id +
        optionally remote_cert_id to validate the peer identity)
    """
    __tablename__ = "ipsec_connections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Unique connection name (becomes the key in swanctl.conf).
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    # Auth mode: "psk" or "cert"
    auth_mode: Mapped[str] = mapped_column(String(8), default="psk", nullable=False)
    # Local IP/host (or %any to listen on every IP).
    local_addrs: Mapped[str] = mapped_column(String(128), default="%any", nullable=False)
    # Remote IP/host (FQDN or IP, or %any for client access).
    remote_addrs: Mapped[str] = mapped_column(String(128), nullable=False)
    # IKE identities (default = addresses).
    local_id: Mapped[str | None] = mapped_column(String(128))
    remote_id: Mapped[str | None] = mapped_column(String(128))
    # Pre-shared key (used only if auth_mode=psk).
    psk: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    # FK to IpsecCert: local cert (cert mode) or null (psk mode).
    local_cert_id: Mapped[int | None] = mapped_column(Integer)
    # FK to IpsecCert: expected remote cert (cert mode).
    # If null in cert mode, we only validate against the CA (no pinned cert).
    remote_cert_id: Mapped[int | None] = mapped_column(Integer)
    # Child traffic selectors: local/remote networks covered by the tunnel.
    # Comma-separated CIDR format: 192.168.1.0/24,192.168.2.0/24
    local_ts: Mapped[str] = mapped_column(String(255), default="0.0.0.0/0", nullable=False)
    remote_ts: Mapped[str] = mapped_column(String(255), default="0.0.0.0/0", nullable=False)
    # IKE and ESP cipher proposals. Modern defaults.
    ike_proposals: Mapped[str] = mapped_column(
        String(255), default="aes256-sha256-modp2048", nullable=False,
    )
    esp_proposals: Mapped[str] = mapped_column(
        String(255), default="aes256-sha256", nullable=False,
    )
    # Start mode: "start" (initiate), "trap" (on traffic), "passive" (wait).
    start_action: Mapped[str] = mapped_column(String(16), default="start", nullable=False)
    description: Mapped[str | None] = mapped_column(String(255))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class IpsecCa(Base):
    """Built-in root certificate authority.

    Singleton (id=1). Generated on the first switch to cert mode from the
    UI. Private key stored in clear in the DB (the DB itself is 0600).
    """
    __tablename__ = "ipsec_ca"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    subject_cn: Mapped[str] = mapped_column(String(128), default="MurOS Root CA", nullable=False)
    subject_o: Mapped[str] = mapped_column(String(128), default="MurOS", nullable=False)
    cert_pem: Mapped[str] = mapped_column(String(4096), default="", nullable=False)
    key_pem: Mapped[str] = mapped_column(String(8192), default="", nullable=False)
    validity_days: Mapped[int] = mapped_column(Integer, default=3650, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)


class IpsecCert(Base):
    """X.509 certificate for IPsec.

    Two uses:
      - is_local=True: cert + private key generated by MurOS for this firewall
        (used as local.certs in swanctl)
      - is_local=False: cert imported from a remote peer for validation
        (used as remote.cacerts or pinned identity)
    """
    __tablename__ = "ipsec_certs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    subject_cn: Mapped[str] = mapped_column(String(128), nullable=False)
    # Subject Alternative Names (e.g. "DNS:fw.example.com,IP:203.0.113.5").
    san: Mapped[str | None] = mapped_column(String(512))
    cert_pem: Mapped[str] = mapped_column(String(4096), nullable=False)
    # Private key (PEM) if MurOS generated the pair. None for imported certs.
    key_pem: Mapped[str | None] = mapped_column(String(8192))
    is_local: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    validity_days: Mapped[int] = mapped_column(Integer, default=825, nullable=False)
    serial: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)


class NotificationConfig(Base):
    """Singleton SMTP config for email alerts."""
    __tablename__ = "notification_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    smtp_host: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    smtp_port: Mapped[int] = mapped_column(Integer, default=587, nullable=False)
    smtp_user: Mapped[str | None] = mapped_column(String(128))
    smtp_password: Mapped[str | None] = mapped_column(String(255))
    use_tls: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    from_addr: Mapped[str] = mapped_column(String(128), default="muros@localhost", nullable=False)
    # Destinataires separes par virgule.
    to_addrs: Mapped[str] = mapped_column(String(1024), default="", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class NotificationRule(Base):
    """Alert rule: an event type + throttle."""
    __tablename__ = "notification_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Logical identifier of the event type (stable key).
    event_type: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Minimum throttle between 2 alerts of the same type, in minutes.
    throttle_minutes: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    description: Mapped[str | None] = mapped_column(String(255))


class NotificationLog(Base):
    """History of sent alerts (last 50 kept, rotation)."""
    __tablename__ = "notification_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(String(2048), nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    error: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class SshConfig(Base):
    """Singleton SSH server config (drop-in `/etc/ssh/sshd_config.d/muros.conf`).

    The MurOS drop-in sets secure defaults (root no, password no). The
    admin can adjust them from the UI, keeping the active-SSH-session
    safeguard (a warning is logged if you cut off your own access).
    """
    __tablename__ = "ssh_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    port: Mapped[int] = mapped_column(Integer, default=22, nullable=False)
    # prohibit-password = root may log in over SSH with a public key but
    # never with a password. The web UI and SSH share the same account and
    # the default administrator is 'root', so root must be able to open an
    # SSH session once the admin enables SSH and uploads a key. Password
    # login for root stays refused; the operator can still tighten this to
    # 'no' or loosen it to 'yes' from the UI.
    permit_root_login: Mapped[str] = mapped_column(String(16), default="prohibit-password", nullable=False)
    password_authentication: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    pubkey_authentication: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    max_auth_tries: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    client_alive_interval: Mapped[int] = mapped_column(Integer, default=300, nullable=False)
    client_alive_count_max: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    # Listen address (empty or 0.0.0.0 = every interface)
    listen_address: Mapped[str] = mapped_column(String(64), default="0.0.0.0", nullable=False)
    # Comma-separated lists (empty = no restriction)
    allow_users: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    allow_groups: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    deny_users: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    # If false: the drop-in is removed (back to Debian defaults)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # admin_disabled : the operator explicitly stopped sshd from the UI.
    # Distinct from "service inactive by accident" so the Monitoring page
    # can label it 'disabled by admin' instead of raising a red alert,
    # and so a reboot will NOT re-enable the daemon (systemctl disable
    # is enforced by the apply path when this flag flips to True).
    admin_disabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class HttpConfig(Base):
    """Singleton config for the web interface (nginx): HTTP/HTTPS listen.

    nginx listens on listen_address:port_https in HTTPS (TLS cert) and,
    if redirect_http_to_https is true, on listen_address:port_http in
    HTTP with a 301 redirect to HTTPS.
    """
    __tablename__ = "http_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    listen_address: Mapped[str] = mapped_column(String(64), default="0.0.0.0", nullable=False)
    port_https: Mapped[int] = mapped_column(Integer, default=443, nullable=False)
    port_http: Mapped[int] = mapped_column(Integer, default=80, nullable=False)
    redirect_http_to_https: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class SnmpConfig(Base):
    """Singleton config for snmpd (read-only, v2c community)."""
    __tablename__ = "snmp_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    # SNMP enabled by default: expected on a firewall appliance (monitoring,
    # supervision). Listening restricted to private LANs via allowed_networks
    # below, read-only 'public' community. The admin can disable or harden it
    # from the UI.
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    port: Mapped[int] = mapped_column(Integer, default=161, nullable=False)
    community: Mapped[str] = mapped_column(String(64), default="public", nullable=False)
    # Allowed networks (comma-separated CIDR). Default: private LANs.
    allowed_networks: Mapped[str] = mapped_column(
        String(512), default="10.0.0.0/8,172.16.0.0/12,192.168.0.0/16", nullable=False,
    )
    syscontact: Mapped[str] = mapped_column(String(128), default="admin@localhost", nullable=False)
    syslocation: Mapped[str] = mapped_column(String(128), default="MurOS firewall", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class HaSyncConfig(Base):
    """Config singleton de la synchronisation de config entre les 2 noeuds HA."""
    __tablename__ = "ha_sync_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Peer URL (other node), e.g. https://muros-backup.local
    peer_url: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    # Token shared between the 2 nodes (long secret, identical on both sides).
    peer_token: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    # auto = push after each apply, manual = button only
    sync_mode: Mapped[str] = mapped_column(String(16), default="auto", nullable=False)
    # Do we verify the peer's TLS cert? (false for snakeoil certs by default)
    verify_tls: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class PendingApply(Base):
    """Apply en attente de confirmation pour rollback automatique.

    Quand l'admin modifie une conf risquee (port SSH, listen HTTP), on
    enregistre l'ancienne conf ici, on applique la nouvelle, et on
    declenche un timer. Si l'admin ne confirme pas dans le delai, on
    re-applique l'ancienne conf (rollback) pour eviter le lock-out.
    """
    __tablename__ = "pending_apply"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    apply_type: Mapped[str] = mapped_column(String(16), nullable=False)  # 'http' | 'ssh'
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    old_config_json: Mapped[str] = mapped_column(String(4096), nullable=False)
    new_config_summary: Mapped[str | None] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    # pending / confirmed / rolled_back / rollback_failed
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime)
    rolled_back_at: Mapped[datetime | None] = mapped_column(DateTime)
    rollback_error: Mapped[str | None] = mapped_column(String(512))


class AuditLog(Base):
    """Trace de toutes les actions d'ecriture (POST/PUT/PATCH/DELETE) effectuees
    via l'UI. Rotation auto (garde les 5000 derniers).
    """
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    username: Mapped[str | None] = mapped_column(String(64))
    method: Mapped[str] = mapped_column(String(8))
    path: Mapped[str] = mapped_column(String(256))
    status_code: Mapped[int] = mapped_column(Integer)
    client_ip: Mapped[str | None] = mapped_column(String(45))
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    # Description courte deduite du path (ex: "Modification regle firewall")
    action_summary: Mapped[str | None] = mapped_column(String(128))


class HaSyncLog(Base):
    """Historique des syncs HA (50 derniers gardes, rotation)."""
    __tablename__ = "ha_sync_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)  # push/receive/test
    success: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    error: Mapped[str | None] = mapped_column(String(512))
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    db_size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    triggered_by: Mapped[str] = mapped_column(String(32), default="manual", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class HaVip(Base):
    """IP virtuelle VRRP partagee entre les deux noeuds HA."""
    __tablename__ = "ha_vips"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # VRID: 1-255, must be unique on the L2 segment
    vrid: Mapped[int] = mapped_column(Integer, nullable=False)
    # Interface where the VIP is exposed (eth0, lan, ...)
    interface: Mapped[str] = mapped_column(String(32), nullable=False)
    # VIP in CIDR format (e.g. 192.0.2.10/24)
    vip_cidr: Mapped[str] = mapped_column(String(64), nullable=False)
    # VRRP password (8 chars max, truncated by keepalived)
    auth_pass: Mapped[str] = mapped_column(String(32), default="muros", nullable=False)
    # Priority override (otherwise role-dependent default)
    priority: Mapped[int | None] = mapped_column(Integer)
    description: Mapped[str | None] = mapped_column(String(255))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ServiceApplyState(Base):
    """Tracks the apply state of a managed system service.

    One row per service name (e.g. 'dhcp', 'dns', 'snmp', 'wireguard',
    'ipsec', 'ha', 'ssh', 'http', 'notifications'). `dirty=True` means
    the on-disk config has been regenerated by a recent Save action but
    the systemd daemon has not been restarted / reloaded yet. The yellow
    Apply button in the page header surfaces this flag, mirroring the
    pattern already in place for the firewall (firewall_rules.dirty).
    """

    __tablename__ = "service_apply_state"

    name: Mapped[str] = mapped_column(String(32), primary_key=True)
    dirty: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_applied_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_marked_dirty_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ServiceApplyLog(Base):
    """Audit trail of Save / Apply actions on managed services.

    One row per Save (action='save') and per Apply (action='apply'),
    with the actor user id when available. Used to answer "who
    changed dnsmasq config last and when did anyone restart it ?",
    which we used to be unable to answer with only a boolean dirty
    flag.

    Kept lightweight on purpose : the summary column holds a short
    human-readable label rather than a full diff. Pages that need
    detailed audit (firewall rules) should use their own dedicated
    log tables.
    """

    __tablename__ = "service_apply_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(16), nullable=False)  # 'save' | 'apply'
    actor_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actor_username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False, index=True)


class RollbackTicketRow(Base):
    """Persistent storage for rollback tickets that must survive a
    process restart.

    The unified rollback manager (:mod:`app.rollback`) mirrors persistent
    tickets here so the rollback action can be replayed after a backend
    crash or a reboot. Since Python callables cannot be serialised, the
    actual revert logic is held by a named handler registered at module
    load (e.g. ``http``, ``ssh``, ``tls``, ``interface``, ``route``) and
    invoked at replay time with the ticket's :attr:`detail_json` as input.

    In-memory tickets that do not need to survive restarts (e.g. an nft
    apply, whose rollback closure can simply die with the process and
    the running ruleset will be discarded with it anyway) are not
    mirrored here.
    """

    __tablename__ = "rollback_tickets"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    handler_name: Mapped[str | None] = mapped_column(String(64))
    detail_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="pending", index=True)
    # pending | committed | rolled_back | rollback_failed
    message: Mapped[str | None] = mapped_column(String(512))


class SystemSetting(Base):
    """Generic key/value store for MurOS-wide settings.

    Used for cross-cutting knobs that do not belong to any specific
    feature config table. First user: ``apply_confirm_timeout`` (the
    countdown, in seconds, before an unconfirmed Apply is rolled back).
    """

    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(255), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False,
    )
