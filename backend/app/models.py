# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Modeles de donnees MurOS.

Concepts :
- Zone : groupe logique d'interfaces (wan, lan, dmz, ...)
- Interface : interface reseau physique ou virtuelle, rattachee a une zone
- FirewallRule : regle de filtrage (chain input/forward/output)
- NatRule : regle de translation (masquerade, snat, dnat)
"""
from datetime import datetime, timezone
from sqlalchemy import String, Integer, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MetricSample(Base):
    __tablename__ = "metric_samples"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow, index=True)
    cpu_usage_percent: Mapped[float] = mapped_column(default=0.0)
    memory_used_percent: Mapped[float] = mapped_column(default=0.0)
    memory_used_bytes: Mapped[int] = mapped_column(Integer, default=0)
    conntrack_current: Mapped[int] = mapped_column(Integer, default=0)
    conntrack_used_percent: Mapped[float] = mapped_column(default=0.0)
    load_1: Mapped[float] = mapped_column(default=0.0)
    load_5: Mapped[float] = mapped_column(default=0.0)
    load_15: Mapped[float] = mapped_column(default=0.0)


class InterfaceSample(Base):
    __tablename__ = "interface_samples"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow, index=True)
    interface_name: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    rx_bytes: Mapped[int] = mapped_column(Integer, default=0)
    tx_bytes: Mapped[int] = mapped_column(Integer, default=0)
    rx_packets: Mapped[int] = mapped_column(Integer, default=0)
    tx_packets: Mapped[int] = mapped_column(Integer, default=0)



class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
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

    # Type d'interface :
    # - 'physical' : carte reelle (eth0, ens3...), MurOS ne la cree pas
    # - 'vlan'     : interface VLAN 802.1q, MurOS la cree via `ip link add ... type vlan`
    type: Mapped[str] = mapped_column(String(16), nullable=False, default="physical")
    parent_interface: Mapped[str | None] = mapped_column(String(32))  # eth0 pour un VLAN eth0.100
    vlan_id: Mapped[int | None] = mapped_column(Integer)              # 1-4094

    # Configuration IP : 'static', 'dhcp' ou 'none' (ne pas configurer)
    ip_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="none")
    ip_address: Mapped[str | None] = mapped_column(String(64))    # CIDR si static
    gateway: Mapped[str | None] = mapped_column(String(64))
    dns_servers: Mapped[str | None] = mapped_column(String(255))  # liste separee par virgules
    mtu: Mapped[int | None] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # dirty=True : modif en DB pas encore appliquee au noyau (cf POST /api/network/apply)
    dirty: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, server_default="1")
    # pending_delete=True : VLAN marquee pour suppression, finalisee a l'apply
    # (symetrie avec add VLAN qui est aussi differe a l'apply).
    pending_delete: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, server_default="0")

    zone: Mapped[Zone | None] = relationship(back_populates="interfaces")


class FirewallRule(Base):
    __tablename__ = "firewall_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # forward = trafic traverse le firewall (lan -> wan, dmz -> lan, ...)
    # input = trafic destine au firewall lui-meme
    # output = trafic emis par le firewall
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

    # Rate limit nftables : ex "5/minute" ou "100/second burst 200".
    # Si renseigne, le compilateur ajoute `limit rate <valeur>` avant l'action.
    # Utile pour anti-bruteforce SSH, anti-flood ICMP, throttling DNS.
    rate_limit: Mapped[str | None] = mapped_column(String(64))

    # Groupes (optionnels). Si renseignes, ils priment sur les champs
    # str equivalents (src_address, dst_address, dst_port/protocol).
    # Le compilateur expand le groupe en set inline nftables.
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
    """Groupe de services (ports + protocole) reutilisable dans les regles.

    Exemple : 'LDAP' = tcp/389 + tcp/636, 'AD' = tcp/389 + tcp/636 +
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
    """Un port (ou range) appartenant a un groupe de services."""
    __tablename__ = "service_group_ports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(
        ForeignKey("service_groups.id", ondelete="CASCADE"), nullable=False)
    protocol: Mapped[str] = mapped_column(String(8), nullable=False)  # tcp, udp
    port: Mapped[str] = mapped_column(String(32), nullable=False)  # '80' ou '1024-2048'

    group: Mapped[ServiceGroup] = relationship(back_populates="ports")


class AddressGroup(Base):
    """Groupe d'adresses (IP, CIDR) reutilisable dans les regles.

    Exemple : 'LAN admin' = 192.168.10.0/24, 10.0.0.0/8.
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
    """Une adresse (IP ou CIDR) appartenant a un groupe d'adresses."""
    __tablename__ = "address_group_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(
        ForeignKey("address_groups.id", ondelete="CASCADE"), nullable=False)
    value: Mapped[str] = mapped_column(String(64), nullable=False)  # CIDR ou IP

    group: Mapped[AddressGroup] = relationship(back_populates="entries")


class StaticRoute(Base):
    __tablename__ = "static_routes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    destination: Mapped[str] = mapped_column(String(64), nullable=False)  # CIDR ou "default"
    gateway: Mapped[str | None] = mapped_column(String(64))
    interface_id: Mapped[int | None] = mapped_column(ForeignKey("interfaces.id", ondelete="SET NULL"))
    metric: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    comment: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    dirty: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, server_default="1")

    interface: Mapped[Interface | None] = relationship()


class WanGateway(Base):
    """Multi-WAN failover : un WAN gateway = une sortie internet.

    Le daemon muros-wan-monitor probe `monitoring_target` toutes les
    `interval_s` via l'interface (option `-I`) et compte les echecs
    consecutifs. Au-dela de `failures_threshold`, le WAN passe `down`
    et le monitor reecrit la default route via le prochain WAN UP
    (priority la plus basse). Le retour a UP est confirme par
    `failures_threshold` probes consecutives reussies (anti-flap).

    On stocke le status runtime directement sur la row pour eviter une
    table secondaire et pouvoir le servir a l'UI via le meme GET REST.
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
    # Runtime status, mis a jour par le monitor. Pas indexe, lecture rare.
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
    """Singleton holding the global DHCP server settings (dnsmasq DHCP-only).

    dnsmasq is started in DHCP-only mode (port=0). DNS resolution on the
    box is delegated to Unbound. The service is stopped while
    `enabled=False`; no lease is handed out. Each apply regenerates
    /etc/dnsmasq.d/muros.conf and reloads dnsmasq (or stops it).
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

    dnsmasq raises an error if two ranges are declared on the same
    interface; we enforce a unique constraint at the column level and
    re-check at the API layer to return a clean 400 instead of a 500.
    """
    __tablename__ = "dhcp_pools"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    interface_id: Mapped[int] = mapped_column(
        ForeignKey("interfaces.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    range_start: Mapped[str] = mapped_column(String(64), nullable=False)
    range_end: Mapped[str] = mapped_column(String(64), nullable=False)
    # Optional gateway pushed to clients. Empty -> dnsmasq derives it
    # from the interface IP itself (the common case).
    gateway: Mapped[str | None] = mapped_column(String(64))
    # CSV of DNS servers handed to clients. Empty -> dnsmasq pushes its
    # own IP, which forwards to Unbound on the box. Standard PME case.
    dns_servers: Mapped[str | None] = mapped_column(String(512))
    lease_seconds: Mapped[int | None] = mapped_column(Integer)  # NULL = inherit global default
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    comment: Mapped[str | None] = mapped_column(String(255))

    interface: Mapped[Interface] = relationship()


class DhcpStaticLease(Base):
    """Static MAC-to-IP reservation served by dnsmasq dhcp-host=.

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


class NatRule(Base):
    __tablename__ = "nat_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # masquerade = SNAT auto vers l'IP de l'interface sortante (LAN -> WAN typiquement)
    # snat       = SNAT explicite vers une IP donnee
    # dnat       = redirection (port forwarding) vers une IP/port interne
    type: Mapped[str] = mapped_column(String(16), nullable=False)

    # Interface concernee (sortie pour SNAT/masquerade, entree pour DNAT)
    interface_id: Mapped[int | None] = mapped_column(ForeignKey("interfaces.id", ondelete="SET NULL"))

    src_address: Mapped[str | None] = mapped_column(String(64))
    dst_address: Mapped[str | None] = mapped_column(String(64))  # IP publique pour DNAT
    protocol: Mapped[str | None] = mapped_column(String(8))
    dst_port: Mapped[str | None] = mapped_column(String(64))     # port externe pour DNAT

    # Pour SNAT : IP source remplacante
    # Pour DNAT : IP cible interne
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
    """Config singleton de la haute dispo.

    On stocke 1 seule ligne (id=1). L'UI lit/ecrit ce singleton.
    """
    __tablename__ = "ha_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # 'primary' ou 'secondary' : determine la priorite VRRP de base
    role: Mapped[str] = mapped_column(String(16), default="primary", nullable=False)
    # IP de l'autre noeud (utilise par conntrackd)
    peer_address: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    # Interface dediee a la sync conntrackd (cross-link recommande)
    sync_interface: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    # Sync conntrack toujours active : sans synchro, un failover casse
    # toutes les connexions TCP existantes ce qui defait l'interet du HA.
    # Le champ reste en DB pour ne pas casser la migration mais l'UI ne
    # l'expose plus et Pydantic force True a l'ecriture.
    conntrack_sync: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # preempt = le primary reprend la main des qu'il revient (recommande)
    preempt: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow,
    )


class FirewallApplyState(Base):
    """Singleton qui pilote la pastille "Apply" du firewall.

    Le compteur de pending est calcule en sommant les `dirty=True` sur
    firewall_rules / nat_rules / zones. Probleme : quand on SUPPRIME la
    derniere regle d'une chaine (ou la derniere zone, ou la derniere
    regle NAT), il n'y a plus aucune ligne a flagger dirty. Le noyau
    contient pourtant encore l'ancienne ruleset, donc l'admin doit
    quand meme cliquer Apply.

    Ce singleton recoit le `dirty=True` global dans ce cas-la, et plus
    generalement chaque fois que la mutation est destructive (delete /
    cascade). Le compteur `total` du /pending ajoute 1 si ce singleton
    est dirty, et POST /apply clear le tout en bloc.
    """
    __tablename__ = "firewall_apply_state"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    dirty: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_applied_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)


class WireGuardConfig(Base):
    """Config singleton de l'interface WireGuard `wg0`.

    On gere une seule interface par defaut, c'est suffisant pour 90% des
    cas. Si plus tard on a besoin de plusieurs interfaces, on transformera
    en collection.
    """
    __tablename__ = "wireguard_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Nom de l'interface WG (defaut wg0). Doit etre <= 15 chars.
    interface_name: Mapped[str] = mapped_column(String(15), default="wg0", nullable=False)
    # IP du firewall sur le tunnel WG, format CIDR (ex: 10.10.0.1/24).
    address_cidr: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    # Port UDP d'ecoute (defaut 51820).
    listen_port: Mapped[int] = mapped_column(Integer, default=51820, nullable=False)
    # Cle privee du serveur (base64). Generee depuis l'UI.
    private_key: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    # Cle publique derivee (rendue par l'UI mais stockee pour eviter recalcul).
    public_key: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    # MTU optionnel (defaut WG = 1420).
    mtu: Mapped[int | None] = mapped_column(Integer)
    # Public endpoint advertised to clients (FQDN or public IP, no port).
    # Used to fill the Endpoint = host:port line in exported client configs.
    # Empty -> a <FIREWALL-PUBLIC-IP> placeholder is rendered instead.
    public_endpoint: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class WireGuardPeer(Base):
    """Peer WireGuard : un client road-warrior ou un site distant."""
    __tablename__ = "wireguard_peers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    # Cle publique du peer (base64, 44 chars exactement).
    public_key: Mapped[str] = mapped_column(String(64), nullable=False)
    # Cle pre-partagee (PSK) optionnelle, ajoute une couche de securite.
    preshared_key: Mapped[str | None] = mapped_column(String(64))
    # AllowedIPs cote serveur : reseaux atteignables via ce peer.
    # Ex pour un road-warrior : 10.10.0.2/32. Pour un site : 10.10.0.2/32, 192.168.42.0/24.
    allowed_ips: Mapped[str] = mapped_column(String(255), nullable=False)
    # AllowedIPs cote CLIENT : reseaux que le client routera dans le tunnel.
    # C'est ce qui apparait dans la section [Peer] de la conf exportee au
    # client (et donc ce a quoi le client a acces). Defaut "0.0.0.0/0, ::/0"
    # = full tunnel. Pour un split tunnel, mettre par ex. "10.10.0.0/24,
    # 192.168.1.0/24". Champ vide -> on retombe sur le defaut full tunnel.
    client_allowed_ips: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    # Endpoint distant (host:port), optionnel pour les road-warriors.
    endpoint: Mapped[str | None] = mapped_column(String(128))
    # Persistent keepalive en secondes (recommande 25 derriere du NAT).
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
    """Connexion IPsec/IKEv2 site-a-site (strongSwan/swanctl).

    Supporte 2 modes d'authentification :
      - psk : pre-shared key (champ psk renseigne)
      - cert : authentification par certificats X.509 (local_cert_id +
        eventuellement remote_cert_id pour valider l'identite du peer)
    """
    __tablename__ = "ipsec_connections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Nom unique de la connexion (sera la cle dans swanctl.conf).
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    # Mode d'auth : "psk" ou "cert"
    auth_mode: Mapped[str] = mapped_column(String(8), default="psk", nullable=False)
    # IP/host local (ou %any pour ecouter sur toutes les IPs).
    local_addrs: Mapped[str] = mapped_column(String(128), default="%any", nullable=False)
    # IP/host distant (FQDN ou IP, ou %any pour acces clients).
    remote_addrs: Mapped[str] = mapped_column(String(128), nullable=False)
    # Identifiants IKE (defaut = adresses).
    local_id: Mapped[str | None] = mapped_column(String(128))
    remote_id: Mapped[str | None] = mapped_column(String(128))
    # Pre-shared key (utilisee uniquement si auth_mode=psk).
    psk: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    # FK vers IpsecCert : certificat local (mode cert) ou null (mode psk).
    local_cert_id: Mapped[int | None] = mapped_column(Integer)
    # FK vers IpsecCert : certificat distant attendu (mode cert).
    # Si null en mode cert, on valide juste contre la CA (sans cert pinne).
    remote_cert_id: Mapped[int | None] = mapped_column(Integer)
    # Traffic selectors enfants : reseaux locaux/distants couverts par le tunnel.
    # Format CIDR separe par virgules : 192.168.1.0/24,192.168.2.0/24
    local_ts: Mapped[str] = mapped_column(String(255), default="0.0.0.0/0", nullable=False)
    remote_ts: Mapped[str] = mapped_column(String(255), default="0.0.0.0/0", nullable=False)
    # Propositions de chiffrement IKE et ESP. Defauts modernes.
    ike_proposals: Mapped[str] = mapped_column(
        String(255), default="aes256-sha256-modp2048", nullable=False,
    )
    esp_proposals: Mapped[str] = mapped_column(
        String(255), default="aes256-sha256", nullable=False,
    )
    # Mode de demarrage : "start" (initie), "trap" (sur trafic), "passive" (attend).
    start_action: Mapped[str] = mapped_column(String(16), default="start", nullable=False)
    description: Mapped[str | None] = mapped_column(String(255))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class IpsecCa(Base):
    """Autorite de certification racine integree.

    Singleton (id=1). Genere lors du premier passage en mode cert depuis
    l'UI. Cle privee stockee en clair dans la DB (DB elle-meme en 0600).
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
    """Certificat X.509 pour IPsec.

    Deux usages :
      - is_local=True : cert + cle privee generes par MurOS pour ce firewall
        (utilise comme local.certs dans swanctl)
      - is_local=False : cert importe d'un peer distant pour validation
        (utilise comme remote.cacerts ou identite pinne)
    """
    __tablename__ = "ipsec_certs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    subject_cn: Mapped[str] = mapped_column(String(128), nullable=False)
    # Subject Alternative Names (ex: "DNS:fw.exemple.fr,IP:203.0.113.5").
    san: Mapped[str | None] = mapped_column(String(512))
    cert_pem: Mapped[str] = mapped_column(String(4096), nullable=False)
    # Cle privee (PEM) si MurOS a genere la paire. None pour les certs importes.
    key_pem: Mapped[str | None] = mapped_column(String(8192))
    is_local: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    validity_days: Mapped[int] = mapped_column(Integer, default=825, nullable=False)
    serial: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)


class NotificationConfig(Base):
    """Config singleton SMTP pour les alertes par mail."""
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
    """Regle d'alerte : un type d'evenement + throttle."""
    __tablename__ = "notification_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Identifiant logique du type d'evenement (cle stable).
    event_type: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Throttle minimal entre 2 alertes du meme type, en minutes.
    throttle_minutes: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    description: Mapped[str | None] = mapped_column(String(255))


class NotificationLog(Base):
    """Historique des alertes envoyees (50 derniers gardes, rotation)."""
    __tablename__ = "notification_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(String(2048), nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    error: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class SshConfig(Base):
    """Config singleton du serveur SSH (drop-in `/etc/ssh/sshd_config.d/muros.conf`).

    Le drop-in MurOS pose des defauts secures (root no, password no).
    Ici l'admin peut les ajuster depuis l'UI, en gardant la garde-fou
    de la session SSH active (on log un warning si on coupe son propre
    acces).
    """
    __tablename__ = "ssh_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    port: Mapped[int] = mapped_column(Integer, default=22, nullable=False)
    # prohibit-password = root par cle SSH OK, par mot de passe refuse
    # (defaut Debian 13 cloud-init, evite le lock-out sur install fresh)
    permit_root_login: Mapped[str] = mapped_column(String(16), default="prohibit-password", nullable=False)
    password_authentication: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    pubkey_authentication: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    max_auth_tries: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    client_alive_interval: Mapped[int] = mapped_column(Integer, default=300, nullable=False)
    client_alive_count_max: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    # Adresse d'ecoute (vide ou 0.0.0.0 = toutes les interfaces)
    listen_address: Mapped[str] = mapped_column(String(64), default="0.0.0.0", nullable=False)
    # Listes separees par virgule (vide = pas de restriction)
    allow_users: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    allow_groups: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    deny_users: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    # Si false : on supprime le drop-in (retour aux defauts Debian)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # admin_disabled : the operator explicitly stopped sshd from the UI.
    # Distinct from "service inactive by accident" so the Monitoring page
    # can label it 'disabled by admin' instead of raising a red alert,
    # and so a reboot will NOT re-enable the daemon (systemctl disable
    # is enforced by the apply path when this flag flips to True).
    admin_disabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class HttpConfig(Base):
    """Config singleton de l'interface web (nginx) : ecoute HTTP/HTTPS.

    nginx ecoute sur listen_address:port_https en HTTPS (cert TLS) et,
    si redirect_http_to_https est true, sur listen_address:port_http en
    HTTP avec redirect 301 vers HTTPS.
    """
    __tablename__ = "http_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    listen_address: Mapped[str] = mapped_column(String(64), default="0.0.0.0", nullable=False)
    port_https: Mapped[int] = mapped_column(Integer, default=443, nullable=False)
    port_http: Mapped[int] = mapped_column(Integer, default=80, nullable=False)
    redirect_http_to_https: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class SnmpConfig(Base):
    """Config singleton pour snmpd (lecture seule, community v2c)."""
    __tablename__ = "snmp_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    # SNMP active par defaut : c'est attendu sur une appliance firewall (monitoring,
    # supervision). Ecoute restreinte aux LAN prives via allowed_networks ci-dessous,
    # community 'public' en lecture seule. L'admin peut desactiver ou durcir depuis l'UI.
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    port: Mapped[int] = mapped_column(Integer, default=161, nullable=False)
    community: Mapped[str] = mapped_column(String(64), default="public", nullable=False)
    # Reseaux autorises (CIDR separes par virgule). Defaut : LAN prives.
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
    # URL du peer (autre noeud), ex: https://muros-backup.local
    peer_url: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    # Token partage entre les 2 noeuds (long secret, identique des deux cotes).
    peer_token: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    # auto = push apres chaque apply, manual = bouton uniquement
    sync_mode: Mapped[str] = mapped_column(String(16), default="auto", nullable=False)
    # On verifie le cert TLS du peer ? (false pour les cert snakeoil par defaut)
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
    # VRID : 1-255, doit etre unique sur le segment L2
    vrid: Mapped[int] = mapped_column(Integer, nullable=False)
    # Interface ou la VIP est exposee (eth0, lan, ...)
    interface: Mapped[str] = mapped_column(String(32), nullable=False)
    # VIP au format CIDR (ex: 192.0.2.10/24)
    vip_cidr: Mapped[str] = mapped_column(String(64), nullable=False)
    # Mot de passe VRRP (8 chars max, tronque par keepalived)
    auth_pass: Mapped[str] = mapped_column(String(32), default="muros", nullable=False)
    # Priorite override (sinon defaut role-dependant)
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
