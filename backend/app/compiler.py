"""Rule compiler: SQLAlchemy models -> nftables ruleset (text).

The output is a complete and standalone nftables script, applied with:
    nft -f <file>
"""
import ipaddress

from sqlalchemy.orm import Session, joinedload

from app import models


def _zone_interfaces(zone: models.Zone | None) -> list[str]:
    if zone is None:
        return []
    return [i.name for i in zone.interfaces]


def _format_ports(ports: str | None) -> str | None:
    """22 -> 22 ; 22,80 -> { 22, 80 } ; 1000-2000 -> 1000-2000."""
    if not ports:
        return None
    ports = ports.strip()
    if "," in ports:
        items = [p.strip() for p in ports.split(",") if p.strip()]
        return "{ " + ", ".join(items) + " }"
    return ports


def _format_addr_set(values: list[str]) -> str:
    """Formate une liste d'IP/CIDR en set nft inline."""
    if len(values) == 1:
        return values[0]
    return "{ " + ", ".join(values) + " }"


def _compile_addresses_zones(rule: models.FirewallRule) -> str:
    """nft match clause for zones and addresses. Empty if no selector."""
    parts: list[str] = []

    src_ifs = _zone_interfaces(rule.src_zone)
    dst_ifs = _zone_interfaces(rule.dst_zone)

    if src_ifs:
        if len(src_ifs) == 1:
            parts.append(f"iifname {src_ifs[0]}")
        else:
            parts.append("iifname { " + ", ".join(src_ifs) + " }")

    if dst_ifs:
        if len(dst_ifs) == 1:
            parts.append(f"oifname {dst_ifs[0]}")
        else:
            parts.append("oifname { " + ", ".join(dst_ifs) + " }")

    # Address selectors are emitted by _addr_variants() so they can be
    # family-aware (ip vs ip6): the filter table is `inet`, so an `ip
    # saddr` clause only matches IPv4 and would silently skip IPv6.
    return " ".join(parts)


def _addr_family(value: str) -> int | None:
    """Return 4 or 6 for an address/CIDR/range value, or None if unknown."""
    v = value.split("-", 1)[0].split("/", 1)[0].strip()
    try:
        return ipaddress.ip_address(v).version
    except ValueError:
        try:
            return ipaddress.ip_network(value, strict=False).version
        except ValueError:
            return None


def _addr_variants(rule: models.FirewallRule) -> list[str]:
    """Family-aware source/destination address clauses for a rule.

    Returns one clause string per address family in play so the same DB
    rule filters both IPv4 (`ip saddr/daddr`) and IPv6 (`ip6 saddr/daddr`)
    inside the `inet` table. A rule with no address yields one empty
    clause (match-all). A rule mixing v4 and v6 (e.g. a group holding
    both) yields one variant per family.
    """
    def _values(group, single):
        if group and group.entries:
            return [e.value for e in group.entries]
        return [single] if single else []

    src = _values(rule.src_address_group, rule.src_address)
    dst = _values(rule.dst_address_group, rule.dst_address)
    if not src and not dst:
        return [""]

    def _by_family(vals):
        buckets: dict[int, list[str]] = {4: [], 6: []}
        for v in vals:
            fam = _addr_family(v)
            if fam in (4, 6):
                buckets[fam].append(v)
        return buckets

    s, d = _by_family(src), _by_family(dst)
    families = [f for f in (4, 6) if s[f] or d[f]]

    variants: list[str] = []
    for fam in families:
        kw = "ip6" if fam == 6 else "ip"
        parts: list[str] = []
        if s[fam]:
            parts.append(f"{kw} saddr {_format_addr_set(s[fam]) if len(s[fam]) > 1 else s[fam][0]}")
        if d[fam]:
            parts.append(f"{kw} daddr {_format_addr_set(d[fam]) if len(d[fam]) > 1 else d[fam][0]}")
        variants.append(" ".join(parts))
    return variants or [""]


def _compile_proto_ports(rule: models.FirewallRule) -> list[str]:
    """Renvoie la (ou les) portion(s) proto+ports d'une regle.

    Si rule.service_group est renseigne, on retourne une variante par
    protocole present dans le groupe (nft ne permet pas de mixer tcp/udp
    dans un seul set dport). Sinon, comportement classique avec les
    champs protocol/dst_port/src_port.
    """
    if rule.service_group and rule.service_group.ports:
        by_proto: dict[str, list[str]] = {}
        for p in rule.service_group.ports:
            by_proto.setdefault(p.protocol, []).append(p.port)
        out: list[str] = []
        for proto, ports in by_proto.items():
            port_set = ("{ " + ", ".join(ports) + " }") if len(ports) > 1 else ports[0]
            out.append(f"{proto} dport {port_set}")
        return out

    proto = rule.protocol
    sport = _format_ports(rule.src_port)
    dport = _format_ports(rule.dst_port)

    if proto == "icmp":
        # Match both ICMP (v4) and ICMPv6 in the inet table, so a single
        # "allow ICMP" rule covers ping/diagnostics on both families
        # instead of silently leaving IPv6 ICMP unmatched.
        return ["meta l4proto { icmp, ipv6-icmp }"]

    # Protocole "any" (ou absent) : si des ports sont renseignes, on
    # genere une ligne par protocole port-compatible (tcp + udp). Sinon,
    # aucun selecteur (la regle s'applique a tous les protocoles).
    if not proto or proto == "any":
        if sport or dport:
            out: list[str] = []
            for p in ("tcp", "udp"):
                bits: list[str] = []
                if sport:
                    bits.append(f"{p} sport {sport}")
                if dport:
                    bits.append(f"{p} dport {dport}")
                out.append(" ".join(bits))
            return out
        return [""]

    if proto in ("tcp", "udp"):
        bits = []
        if sport:
            bits.append(f"{proto} sport {sport}")
        if dport:
            bits.append(f"{proto} dport {dport}")
        if not sport and not dport:
            bits.append(f"ip protocol {proto}")
        return [" ".join(bits)]
    return [""]


def _compile_rule(rule: models.FirewallRule) -> list[str]:
    """Return 1 to N nft lines for one DB rule.

    A rule that references a service_group with several protocols
    (e.g. AD = tcp + udp) yields several lines, one per protocol.

    Empty-zone guard: when a rule is scoped to a zone that currently has
    no member interface, it must match nothing. Without this the zone
    clause would simply be omitted from the output (see
    _compile_addresses_zones) and the rule would wrongly match every
    interface. Concretely, the default "allow LAN to firewall" seed rule
    would behave as "allow from anywhere", including the WAN, as long as
    the LAN zone is not wired yet. This mirrors OPNsense, where an
    unassigned interface simply carries no rule. A src_zone/dst_zone of
    None means "any" and is intentionally not affected.
    """
    if rule.src_zone is not None and not _zone_interfaces(rule.src_zone):
        return []
    if rule.dst_zone is not None and not _zone_interfaces(rule.dst_zone):
        return []

    zone_part = _compile_addresses_zones(rule)
    addr_variants = _addr_variants(rule)
    proto_variants = _compile_proto_ports(rule)
    action = rule.action

    lines: list[str] = []
    for addr_part in addr_variants:
      for proto_part in proto_variants:
        match = " ".join(p for p in (zone_part, addr_part, proto_part) if p)
        pieces = [match] if match else []
        if rule.rate_limit:
            pieces.append(_format_rate_limit(rule.rate_limit))
        if rule.log:
            # Enriched prefix to identify the action and the rule in
            # journalctl. Format: "[muros DROP r=123 input] ". The
            # kernel allows up to ~127 chars, we stay well below.
            # action_token is uppercased to visually match the historic
            # iptables convention (DROP / REJECT / ACCEPT).
            action_token = action.split()[0].upper() if action else "?"
            chain_token = rule.chain or "?"
            log_prefix = f'[muros {action_token} r={rule.id} {chain_token}] '
            pieces.append(f'log prefix "{log_prefix}"')
        # Always emit a counter so /api/firewall/stats can surface live
        # packets/bytes per rule in the UI. The cost is negligible
        # (single u64+u64 atomic increment) and the value is reset on
        # ruleset reload, which matches the apply-as-source-of-truth
        # model: the counter measures what happened since the last
        # Apply click.
        pieces.append("counter")
        pieces.append(action)
        line = " ".join(p for p in pieces if p)
        # Comment is always emitted with the [muros r=<id>] marker so
        # firewall_stats can map nft entries back to DB rule ids. The
        # user-provided comment is kept verbatim after the marker.
        user_comment = (rule.comment or "").replace('"', "'")
        marker = f"[muros r={rule.id}]"
        comment = f"{marker} {user_comment}".strip()
        line += f' comment "{comment}"'
        lines.append(f"        {line}")
    return lines


def _format_rate_limit(raw: str) -> str:
    """Translate '5/minute burst 10' into 'limit rate 5/minute burst 10 packets'.

    The format is already validated by the schema, we just normalize
    here for the nft output ('packets' keyword is mandatory after
    burst).
    """
    s = raw.strip()
    if " burst " in s:
        rate, burst = s.split(" burst ", 1)
        return f"limit rate {rate.strip()} burst {burst.strip()} packets"
    return f"limit rate {s}"


def _local_ipv4_addresses(db: Session) -> list[str]:
    """Host IPv4 addresses currently assigned to the firewall interfaces.

    Used to protect the firewall's own addresses from being captured
    by a wildcard DNAT rule (e.g. a "publish port 443 to 10.10.0.20"
    rule that would otherwise also intercept requests sent to the
    management IP and lock the operator out of the UI).
    """
    addrs: list[str] = []
    try:
        ifaces = db.query(models.Interface).all()
    except Exception:  # noqa: BLE001
        return []
    for iface in ifaces:
        cidr = (iface.ip_address or "").strip()
        if not cidr:
            continue
        host = cidr.split("/", 1)[0].strip()
        # Keep IPv4 only here: the NAT table is ip (v4). IPv6 cases
        # are handled separately and would not match in `ip daddr`.
        if not host or ":" in host:
            continue
        if host not in addrs:
            addrs.append(host)
    return addrs


def _compile_nat(rule: models.NatRule, local_v4: list[str] | None = None) -> tuple[str, str] | None:
    """Return (chain, line) or None if the rule is invalid."""
    iface = rule.interface.name if rule.interface else None

    # NAT counter + comment marker, same idea as filter rules.
    user_comment = (rule.comment or "").replace('"', "'")
    marker = f"[muros nat={rule.id}]"
    nat_comment = f"{marker} {user_comment}".strip()

    if rule.type == "masquerade":
        if not iface:
            return None
        parts = [f"oifname {iface}"]
        if rule.src_address:
            parts.append(f"ip saddr {rule.src_address}")
        parts.append("counter")
        parts.append("masquerade")
        line = "        " + " ".join(parts)
        line += f' comment "{nat_comment}"'
        return ("postrouting", line)

    if rule.type == "snat":
        if not iface or not rule.redirect_to_ip:
            return None
        parts = [f"oifname {iface}"]
        if rule.src_address:
            parts.append(f"ip saddr {rule.src_address}")
        parts.append("counter")
        parts.append(f"snat to {rule.redirect_to_ip}")
        line = "        " + " ".join(parts)
        line += f' comment "{nat_comment}"'
        return ("postrouting", line)

    if rule.type == "dnat":
        if not rule.redirect_to_ip:
            return None
        parts = []
        if iface:
            parts.append(f"iifname {iface}")
        if rule.dst_address:
            parts.append(f"ip daddr {rule.dst_address}")
        elif local_v4:
            # Safety net: when the operator did not pin the DNAT to a
            # specific destination, exclude the firewall's own IPv4
            # addresses so traffic to the management UI (and to other
            # local services like SSH) is never silently redirected to
            # the published backend. Without this, a "publish 443 to
            # web-prod" rule also captures requests aimed at the
            # firewall itself and locks the admin out.
            parts.append(f"ip daddr != {_format_addr_set(local_v4)}")
        proto = rule.protocol or "tcp"
        dport = _format_ports(rule.dst_port)
        if dport:
            parts.append(f"{proto} dport {dport}")
        target = rule.redirect_to_ip
        if rule.redirect_to_port:
            target += f":{rule.redirect_to_port}"
        parts.append("counter")
        parts.append(f"dnat to {target}")
        line = "        " + " ".join(parts)
        line += f' comment "{nat_comment}"'
        return ("prerouting", line)

    return None


def _wireguard_auto_rules(db: Session) -> tuple[list[str], list[str]]:
    """Forward + NAT lines that make a default WireGuard tunnel work.

    Returns (forward_lines, postrouting_lines). Both are empty when no
    WG config exists or the tunnel is disabled, so the generated
    ruleset stays identical to pre-VPN behavior.
    """
    try:
        cfg = db.get(models.WireGuardConfig, 1)
    except Exception:  # noqa: BLE001
        return [], []
    if cfg is None or not cfg.enabled:
        return [], []
    iface = (cfg.interface_name or "wg0").strip()
    if not iface:
        return [], []
    forward = [
        # TCP MSS clamping in both directions so large responses
        # (UI bundles, downloads...) survive the WG encapsulation.
        # Without this PMTU discovery breaks silently on cellular
        # links and the operator sees random "page loads forever"
        # symptoms while small TLS handshakes succeed.
        f'        iifname "{iface}" tcp flags syn tcp option maxseg size set rt mtu',
        f'        oifname "{iface}" tcp flags syn tcp option maxseg size set rt mtu',
        f'        iifname "{iface}" accept',
        f'        oifname "{iface}" accept',
    ]
    postrouting: list[str] = []
    try:
        import ipaddress
        net = ipaddress.ip_interface(cfg.address_cidr).network
        postrouting.append(
            f'        ip saddr {net} oifname != "{iface}" masquerade'
        )
    except (ValueError, TypeError, AttributeError):
        pass
    return forward, postrouting


def compile_ruleset(db: Session) -> str:
    """Generate the full nftables ruleset from the database."""
    rules: list[models.FirewallRule] = (
        db.query(models.FirewallRule)
        .options(
            joinedload(models.FirewallRule.src_zone).selectinload(models.Zone.interfaces),
            joinedload(models.FirewallRule.dst_zone).selectinload(models.Zone.interfaces),
        )
        .filter(models.FirewallRule.enabled.is_(True))
        .order_by(models.FirewallRule.chain, models.FirewallRule.position, models.FirewallRule.id)
        .all()
    )
    nats: list[models.NatRule] = (
        db.query(models.NatRule)
        .options(joinedload(models.NatRule.interface))
        .filter(models.NatRule.enabled.is_(True))
        .order_by(models.NatRule.position, models.NatRule.id)
        .all()
    )

    chains: dict[str, list[str]] = {"input": [], "forward": [], "output": []}
    for r in rules:
        # _compile_rule peut retourner plusieurs lignes (cas service_group
        # multi-protocoles).
        chains.setdefault(r.chain, []).extend(_compile_rule(r))

    local_v4 = _local_ipv4_addresses(db)
    nat_chains: dict[str, list[str]] = {"prerouting": [], "postrouting": []}
    for n in nats:
        compiled = _compile_nat(n, local_v4=local_v4)
        if compiled:
            chain, line = compiled
            nat_chains[chain].append(line)

    # WireGuard: when the tunnel is enabled, transparently allow forward
    # traffic through the WG interface and masquerade the tunnel subnet
    # towards any non-WG egress (typically WAN). This is what makes the
    # default "full tunnel" client config actually reach the internet
    # without the operator having to add a NAT rule or a forward rule
    # by hand. The lines are injected ahead of user-defined rules so
    # the tunnel keeps working even if the forward chain ends in drop.
    wg_lines_forward, wg_lines_postrouting = _wireguard_auto_rules(db)
    if wg_lines_forward:
        chains["forward"] = wg_lines_forward + chains["forward"]
    if wg_lines_postrouting:
        nat_chains["postrouting"] = wg_lines_postrouting + nat_chains["postrouting"]

    out: list[str] = []
    out.append("#!/usr/sbin/nft -f")
    out.append("# Genere par MurOS - ne pas editer manuellement")
    out.append("flush ruleset")
    out.append("")
    out.append("table inet filter {")
    out.append("    chain input {")
    out.append("        type filter hook input priority filter; policy drop;")
    out.append("        iif lo accept")
    out.append("        ct state established,related accept")
    out.append("        ct state invalid drop")
    # IPv4 ICMP policy is intentionally NOT hard-coded here so the
    # admin can block / log / limit ICMP from the UI. The seed installs
    # an "accept icmp" rule (position 30) which gives the same default
    # behavior, but stays editable from Filter rules.
    # IPv6 ICMP is kept hard-coded because dropping it wholesale breaks
    # NDP/SLAAC and disables IPv6 entirely. A future option could
    # expose a more granular IPv6 ICMP control (NDP vs echo).
    out.append("        ip6 nexthdr icmpv6 accept")
    for line in chains["input"]:
        out.append(line)
    out.append("    }")
    out.append("")
    out.append("    chain forward {")
    out.append("        type filter hook forward priority filter; policy drop;")
    out.append("        ct state established,related accept")
    out.append("        ct state invalid drop")
    # Allow transit ICMPv6: NDP and especially Packet-Too-Big are required
    # for routed IPv6 to work at all (PMTUD breaks silently otherwise).
    # Mirrors the input chain's icmpv6 baseline.
    out.append("        ip6 nexthdr icmpv6 accept")
    for line in chains["forward"]:
        out.append(line)
    out.append("    }")
    out.append("")
    out.append("    chain output {")
    out.append("        type filter hook output priority filter; policy accept;")
    for line in chains["output"]:
        out.append(line)
    out.append("    }")
    out.append("}")
    out.append("")
    out.append("table ip nat {")
    out.append("    chain prerouting {")
    out.append("        type nat hook prerouting priority dstnat;")
    for line in nat_chains["prerouting"]:
        out.append(line)
    out.append("    }")
    out.append("")
    out.append("    chain postrouting {")
    out.append("        type nat hook postrouting priority srcnat;")
    for line in nat_chains["postrouting"]:
        out.append(line)
    out.append("    }")
    out.append("}")

    return "\n".join(out) + "\n"
