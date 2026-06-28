#!/usr/bin/env php
<?php

/*
 * MurOS nftables ruleset generator.
 *
 * Reads the OPNsense-style configuration (config.xml) and emits a
 * standalone nftables script for the packet filter. This is the Debian
 * replacement for the FreeBSD pf path in filter.inc: where the original
 * serialises filter rules to pf text and loads them with `pfctl -f`,
 * MurOS serialises to nft and loads them with `nft -f`.
 *
 * Covered so far:
 *   - pass / block / reject, IPv4 (inet) and IPv6 (inet6)
 *   - per-interface ingress match (interface key -> device)
 *   - tcp / udp / icmp, source and destination address/network and ports
 *   - host / network / port aliases compiled to named sets (with timeout
 *     based expiry for external aliases)
 *   - block-private / block-bogons martian drops on flagged interfaces
 *   - a mandatory anti-lockout allowance (ssh + web to the firewall)
 *   - NAT: automatic/hybrid outbound masquerade, advanced outbound rules
 *     (source/destination match, No-NAT exclusions, fixed source port),
 *     destination NAT port forwards (matched on the published destination
 *     and optional source, with No-rdr exclusions and their associated
 *     forward pass) and 1:1 NAT for single IPv4 hosts
 *
 * Not yet handled (kept on the roadmap): IPv6 NPt and subnet 1:1 netmap,
 * policy based routing (route-to/reply-to), traffic shaping/dummynet, and
 * the finer pf state options.
 *
 * Usage: nft_build.php [config.xml]   (defaults to /conf/config.xml)
 * The ruleset is written to stdout.
 */

const ANTI_LOCKOUT_PORTS = ['22', '80', '443'];

const BLOCK_PRIVATE_V4 = [
    '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16',
    '127.0.0.0/8', '169.254.0.0/16', '100.64.0.0/10',
];
const BLOCK_PRIVATE_V6 = ['::1/128', 'fc00::/7', 'fe80::/10'];
const BLOCK_BOGON_V4 = [
    '0.0.0.0/8', '192.0.0.0/24', '192.0.2.0/24', '198.18.0.0/15',
    '198.51.100.0/24', '203.0.113.0/24', '240.0.0.0/4',
];
const BLOCK_BOGON_V6 = ['::/128', '2001:db8::/32', '2001:10::/28'];

/* An interface name is safe to use bare only when it is plain word
 * characters. VLAN (eth0.100), bridge/bond (br-lan) and wildcards must be
 * quoted or `nft -f` rejects the whole ruleset. */
function ifname_token(string $name): string
{
    return preg_match('/^[A-Za-z0-9_]+$/', $name) ? $name : '"' . $name . '"';
}

function fmt_ports(?string $ports): ?string
{
    $ports = trim((string)$ports);
    if ($ports === '') {
        return null;
    }
    /* OPNsense stores ranges as "from:to"; nft wants "from-to". Only emit
     * numeric ports and numeric ranges: anything else (an unresolved alias
     * name, a service keyword) is dropped so it can never produce a token
     * that would make `nft -f` reject the whole ruleset. */
    $ports = str_replace(':', '-', $ports);
    $valid = [];
    foreach (array_filter(array_map('trim', explode(',', $ports)), 'strlen') as $item) {
        if (preg_match('/^\d+(-\d+)?$/', $item)) {
            $valid[] = $item;
        }
    }
    if (empty($valid)) {
        return null;
    }
    return count($valid) === 1 ? $valid[0] : '{ ' . implode(', ', $valid) . ' }';
}

function fmt_addr_set(array $values): string
{
    return count($values) === 1 ? $values[0] : '{ ' . implode(', ', $values) . ' }';
}

/* Classify a single alias entry into an IPv4 address/CIDR, an IPv6
 * address/CIDR or a port (single or range). Hostnames, nested aliases and
 * other unsupported tokens are silently ignored: the resulting set simply
 * does not include them, so a reference still resolves to a valid (possibly
 * empty) named set instead of breaking the whole ruleset. */
function classify_alias_token(string $token, array &$al): void
{
    $token = trim($token);
    if ($token === '') {
        return;
    }
    if (preg_match('/^\d+[:\-]\d+$/', $token)) {
        $al['port'][] = str_replace(':', '-', $token);
        return;
    }
    if (ctype_digit($token)) {
        $al['port'][] = $token;
        return;
    }
    $ipPart = (strpos($token, '/') !== false) ? substr($token, 0, strpos($token, '/')) : $token;
    if (filter_var($ipPart, FILTER_VALIDATE_IP, FILTER_FLAG_IPV6)) {
        $al['v6'][] = $token;
        return;
    }
    if (filter_var($ipPart, FILTER_VALIDATE_IP, FILTER_FLAG_IPV4)) {
        $al['v4'][] = $token;
    }
}

/* Read firewall aliases from both the modern (OPNsense/Firewall/Alias) and
 * the legacy (aliases) locations and turn each into address and/or port
 * element lists. Returns name => [v4, v6, port, type, hasaddr, hasport]. */
function build_aliases(SimpleXMLElement $cfg): array
{
    $aliases = [];
    $collect = function (string $name, string $type, array $tokens, int $expire = 0) use (&$aliases) {
        if (!preg_match('/^[A-Za-z0-9_]+$/', $name)) {
            return;
        }
        if (!isset($aliases[$name])) {
            $aliases[$name] = ['v4' => [], 'v6' => [], 'port' => [], 'type' => $type, 'expire' => $expire];
        }
        foreach ($tokens as $token) {
            classify_alias_token($token, $aliases[$name]);
        }
    };

    /* "external" aliases are filled at runtime through the API (add_table.py)
     * and may carry an expire timeout in seconds. On FreeBSD pf those entries
     * were aged out by a periodic `pfctl -T expire` cron job; on nftables we
     * instead give the set a default per-element timeout (flags interval,
     * timeout) so every entry auto-expires natively without a cron. Parse the
     * value here, rounding up to whole seconds (nft has no sub-second grain). */
    $expire_seconds = function ($value): int {
        $seconds = (float)trim((string)$value);
        return $seconds > 0 ? (int)ceil($seconds) : 0;
    };

    if (isset($cfg->OPNsense->Firewall->Alias->aliases->alias)) {
        foreach ($cfg->OPNsense->Firewall->Alias->aliases->alias as $a) {
            if (isset($a->enabled) && trim((string)$a->enabled) === '0') {
                continue;
            }
            $collect(
                trim((string)$a->name),
                trim((string)$a->type),
                preg_split('/[\s,]+/', trim((string)$a->content)) ?: [],
                $expire_seconds($a->expire ?? 0)
            );
        }
    }
    if (isset($cfg->aliases->alias)) {
        foreach ($cfg->aliases->alias as $a) {
            $collect(
                trim((string)$a->name),
                trim((string)$a->type),
                preg_split('/[\s,]+/', trim((string)$a->address)) ?: [],
                $expire_seconds($a->expire ?? 0)
            );
        }
    }

    foreach ($aliases as &$al) {
        $isPort = ($al['type'] === 'port') || (!empty($al['port']) && empty($al['v4']) && empty($al['v6']));
        $al['hasport'] = $isPort;
        $al['hasaddr'] = !$isPort;
    }
    unset($al);

    return $aliases;
}

/* Reference to the address set for an alias in a given family, or null when
 * the name is not an address alias. */
function alias_addr_ref(string $name, string $family, array $aliases): ?string
{
    if (isset($aliases[$name]) && $aliases[$name]['hasaddr']) {
        return '@' . $name . ($family === 'ip6' ? '_v6' : '_v4');
    }
    return null;
}

/* Resolve a port specification that may be a literal port/range/list or the
 * name of a port alias (then referenced as a named set). */
function resolve_port(?string $ports, array $aliases): ?string
{
    $value = trim((string)$ports);
    if ($value === '') {
        return null;
    }
    if (isset($aliases[$value]) && $aliases[$value]['hasport']) {
        return '@' . $value . '_p';
    }
    return fmt_ports($value);
}

/* Whether a source/destination block carries the "invert match" flag. */
function ep_negated(?SimpleXMLElement $ep): bool
{
    if ($ep === null) {
        return false;
    }
    $value = strtolower(trim((string)($ep->not ?? '')));
    return !in_array($value, ['', '0', 'false', 'no'], true);
}

/* Emit the `set` definitions for every alias. Address aliases always get
 * both an IPv4 and an IPv6 set (possibly empty) so references in either
 * family resolve; port aliases get an inet_service set. */
function alias_set_lines(array $aliases): array
{
    $lines = [];
    foreach ($aliases as $name => $al) {
        /*
         * "external" aliases with an expire value get a default per-element
         * timeout so entries pushed in through the API age out on their own,
         * replacing the FreeBSD `pfctl -T expire` cron. The timeout flag is
         * combined with interval so CIDR ranges keep working.
         */
        $expire = ($al['type'] ?? '') === 'external' ? (int)($al['expire'] ?? 0) : 0;
        $setFlags = $expire > 0 ? 'flags interval,timeout;' : 'flags interval;';
        if ($al['hasaddr'] ?? false) {
            foreach (['v4' => 'ipv4_addr', 'v6' => 'ipv6_addr'] as $fam => $atype) {
                $lines[] = '    set ' . $name . '_' . $fam . ' {';
                $lines[] = '        type ' . $atype . ';';
                $lines[] = '        ' . $setFlags;
                if ($expire > 0) {
                    $lines[] = '        timeout ' . $expire . 's;';
                }
                /*
                 * auto-merge lets overlapping/adjacent entries coalesce instead
                 * of being rejected. Dynamic aliases (GeoIP, URL tables) are
                 * refreshed in place by update_tables.py and frequently contain
                 * overlapping CIDRs, which would otherwise abort the load.
                 */
                $lines[] = '        auto-merge;';
                if (!empty($al[$fam])) {
                    $lines[] = '        elements = { ' . implode(', ', array_unique($al[$fam])) . ' }';
                }
                $lines[] = '    }';
            }
        }
        if (!empty($al['hasport'])) {
            $lines[] = '    set ' . $name . '_p {';
            $lines[] = '        type inet_service;';
            $lines[] = '        flags interval;';
            if (!empty($al['port'])) {
                $lines[] = '        elements = { ' . implode(', ', array_unique($al['port'])) . ' }';
            }
            $lines[] = '    }';
        }
    }
    return $lines;
}

/* Map an interface configuration key (wan, lan, optX) to its device and,
 * when statically configured, to its IPv4/IPv6 CIDR. */
function build_interfaces(SimpleXMLElement $cfg): array
{
    $out = [];
    if (!isset($cfg->interfaces)) {
        return $out;
    }
    foreach ($cfg->interfaces->children() as $key => $node) {
        $dev = trim((string)$node->if);
        if ($dev === '') {
            continue;
        }
        $entry = ['device' => $dev, 'cidr4' => null, 'cidr6' => null,
                  'ip4' => null, 'ip6' => null,
                  'blockpriv' => !empty((string)$node->blockpriv),
                  'blockbogons' => !empty((string)$node->blockbogons)];
        $ip4 = trim((string)$node->ipaddr);
        $sub4 = trim((string)$node->subnet);
        if (filter_var($ip4, FILTER_VALIDATE_IP, FILTER_FLAG_IPV4)) {
            /* host address of the interface, used to match "<key>ip" tokens
             * (the "WAN address" target of a port forward, for instance). */
            $entry['ip4'] = $ip4;
            if ($sub4 !== '') {
                $entry['cidr4'] = network_of($ip4, (int)$sub4);
            }
        }
        $ip6 = trim((string)$node->ipaddrv6);
        $sub6 = trim((string)$node->subnetv6);
        if (filter_var($ip6, FILTER_VALIDATE_IP, FILTER_FLAG_IPV6)) {
            $entry['ip6'] = $ip6;
            if ($sub6 !== '') {
                $entry['cidr6'] = $ip6 . '/' . $sub6;
            }
        }
        $out[(string)$key] = $entry;
    }
    return $out;
}

/* Return the network CIDR for an IPv4 address and prefix length. */
function network_of(string $ip, int $prefix): string
{
    $long = ip2long($ip);
    $mask = $prefix === 0 ? 0 : (~((1 << (32 - $prefix)) - 1)) & 0xFFFFFFFF;
    return long2ip($long & $mask) . '/' . $prefix;
}

/* Resolve a <source>/<destination> block to an nft address token, or null
 * when it cannot be expressed yet (dynamic address, alias, etc.). */
function resolve_endpoint(?SimpleXMLElement $ep, string $family, array $ifaces, array $aliases = []): ?string
{
    if ($ep === null || isset($ep->any)) {
        return null;
    }
    $addr = trim((string)$ep->address);
    if ($addr !== '') {
        /* address may be a literal IP/CIDR or the name of an address alias. */
        $ref = alias_addr_ref($addr, $family, $aliases);
        return $ref !== null ? $ref : $addr;
    }
    $net = trim((string)$ep->network);
    if ($net === '') {
        return null;
    }
    /* network is an interface key; use its statically known subnet. */
    if (isset($ifaces[$net])) {
        return $family === 'ip6' ? $ifaces[$net]['cidr6'] : $ifaces[$net]['cidr4'];
    }
    /* "<key>ip" refers to the single host address of an interface (the
     * "WAN address" a port forward is usually published on). Resolve it to
     * the statically configured address; dynamic interfaces stay null. */
    if (substr($net, -2) === 'ip' && isset($ifaces[substr($net, 0, -2)])) {
        $itf = $ifaces[substr($net, 0, -2)];
        return $family === 'ip6' ? $itf['ip6'] : $itf['ip4'];
    }
    /* literal CIDR stored directly in network. */
    if (strpos($net, '/') !== false) {
        return $net;
    }
    /* network may also carry an alias name in some configurations. */
    return alias_addr_ref($net, $family, $aliases);
}

function martian_lines(array $ifaces): array
{
    $lines = [];
    foreach ($ifaces as $key => $itf) {
        $ifn = ifname_token($itf['device']);
        if ($itf['blockpriv']) {
            $lines[] = "        iifname $ifn ip saddr " . fmt_addr_set(BLOCK_PRIVATE_V4)
                . " counter drop comment \"block-private v4 $key\"";
            $lines[] = "        iifname $ifn ip6 saddr " . fmt_addr_set(BLOCK_PRIVATE_V6)
                . " counter drop comment \"block-private v6 $key\"";
        }
        if ($itf['blockbogons']) {
            $lines[] = "        iifname $ifn ip saddr " . fmt_addr_set(BLOCK_BOGON_V4)
                . " counter drop comment \"block-bogon v4 $key\"";
            $lines[] = "        iifname $ifn ip6 saddr " . fmt_addr_set(BLOCK_BOGON_V6)
                . " counter drop comment \"block-bogon v6 $key\"";
        }
    }
    return $lines;
}

/* Identify which interface keys act as WAN (uplink). OPNsense marks an
 * uplink by attaching a gateway or by requesting a dynamic address; the
 * conventional key is "wan". We collect every match so multi-WAN still
 * gets outbound NAT. */
function wan_devices(SimpleXMLElement $cfg, array $ifaces): array
{
    $wan = [];
    foreach ($ifaces as $key => $itf) {
        $node = $cfg->interfaces->$key ?? null;
        $isWan = $key === 'wan';
        if ($node !== null) {
            $ip4 = trim((string)$node->ipaddr);
            $gw = trim((string)$node->gateway);
            if ($ip4 === 'dhcp' || $ip4 === 'pppoe' || $gw !== '') {
                $isWan = true;
            }
        }
        if ($isWan) {
            $wan[$key] = $itf['device'];
        }
    }
    return $wan;
}

/* Build the NAT chains (source NAT / masquerade and destination NAT port
 * forwards) from the <nat> section. Returns prerouting lines, postrouting
 * lines and the filter passes that must accompany port forwards (traffic
 * is evaluated by the forward hook after dnat rewrote the destination). */
function build_nat(SimpleXMLElement $cfg, array $ifaces, array $wanDevs, array $aliases = []): array
{
    $pre = [];
    $post = [];
    $autoMasq = [];
    $passes = [];

    $localNets = [];
    foreach ($ifaces as $key => $itf) {
        if (!isset($wanDevs[$key]) && $itf['cidr4'] !== null) {
            $localNets[] = $itf['cidr4'];
        }
    }

    $mode = isset($cfg->nat->outbound->mode) ? trim((string)$cfg->nat->outbound->mode) : 'automatic';

    /* automatic / hybrid: masquerade internal networks leaving each WAN. */
    if (($mode === 'automatic' || $mode === 'hybrid') && $wanDevs && $localNets) {
        foreach ($wanDevs as $dev) {
            $autoMasq[] = '        oifname ' . ifname_token($dev) . ' ip saddr ' . fmt_addr_set($localNets)
                . ' counter masquerade comment "auto outbound nat"';
        }
    }

    /* advanced / hybrid: explicit outbound rules. No-NAT rules are collected
     * separately so they can be evaluated before any translating rule: a
     * "return" in the postrouting chain stops NAT processing for the matched
     * traffic (the equivalent of a pf "no nat" rule), which is how traffic to
     * a remote VPN subnet is kept un-translated. */
    $noNat = [];
    if ($mode !== 'disabled' && isset($cfg->nat->outbound->rule)) {
        foreach ($cfg->nat->outbound->rule as $r) {
            if (isset($r->disabled)) {
                continue;
            }
            $dev = $ifaces[trim((string)$r->interface)]['device'] ?? null;
            if ($dev === null) {
                continue;
            }
            $parts = ['oifname ' . ifname_token($dev)];
            $src = resolve_endpoint($r->source ?? null, 'ip', $ifaces, $aliases);
            if ($src !== null) {
                $parts[] = "ip saddr $src";
            }
            $dst = resolve_endpoint($r->destination ?? null, 'ip', $ifaces, $aliases);
            if ($dst !== null) {
                $parts[] = "ip daddr $dst";
            }
            $proto = strtolower(trim((string)$r->protocol));
            $hasL4 = false;
            if ($proto === 'tcp' || $proto === 'udp') {
                $dport = resolve_port((string)($r->destination->port ?? ''), $aliases);
                if ($dport !== null) {
                    $parts[] = "$proto dport $dport";
                    $hasL4 = true;
                }
            }

            if (!empty((string)($r->nonat ?? ''))) {
                $noNat[] = '        ' . implode(' ', $parts) . ' counter return comment "no nat"';
                continue;
            }

            $target = trim((string)$r->target);
            if (filter_var($target, FILTER_VALIDATE_IP, FILTER_FLAG_IPV4)) {
                /* A natport translates the source port to a fixed value; with
                 * static-port (or no natport) we leave it to conntrack, which
                 * preserves the original source port whenever it can. */
                $natport = trim((string)$r->natport);
                if ($natport !== '' && ($proto === 'tcp' || $proto === 'udp')) {
                    /* nft only accepts a port in the snat target after a
                     * transport protocol match, so add one when the rule does
                     * not already carry a dport clause. */
                    if (!$hasL4) {
                        $parts[] = "meta l4proto $proto";
                    }
                    $verb = "snat to $target:$natport";
                } else {
                    $verb = "snat to $target";
                }
            } else {
                $verb = 'masquerade';
            }
            $post[] = '        ' . implode(' ', $parts) . " counter $verb comment \"outbound nat\"";
        }
    }

    /* port forwards: destination NAT plus an associated forward pass. */
    $noRdr = [];
    if (isset($cfg->nat->rule)) {
        foreach ($cfg->nat->rule as $r) {
            if (isset($r->disabled)) {
                continue;
            }
            $dev = $ifaces[trim((string)$r->interface)]['device'] ?? null;
            $proto = strtolower(trim((string)$r->protocol)) ?: 'tcp';
            if (!in_array($proto, ['tcp', 'udp'], true)) {
                continue;
            }
            $extPort = resolve_port((string)($r->{'destination'}->port ?? ''), $aliases);
            $localPort = trim((string)$r->{'local-port'});
            /* Match the published destination (typically the WAN address) and,
             * when set, the allowed source. Without the destination match the
             * forward would hijack traffic aimed at any address reachable on
             * the ingress interface, not just the one it is published on. */
            $dst = resolve_endpoint($r->destination ?? null, 'ip', $ifaces, $aliases);
            $src = resolve_endpoint($r->source ?? null, 'ip', $ifaces, $aliases);
            $parts = [];
            if ($dev !== null) {
                $parts[] = 'iifname ' . ifname_token($dev);
            }
            if ($src !== null) {
                $parts[] = "ip saddr $src";
            }
            if ($dst !== null) {
                $parts[] = "ip daddr $dst";
            }
            $parts[] = "$proto";
            if ($extPort !== null) {
                $parts[] = "dport $extPort";
            }

            /* A no-rdr rule excludes matching traffic from redirection (for
             * example to let a management subnet reach a service directly
             * while everyone else is forwarded). Emit it as a return in the
             * prerouting chain, ordered before the dnat rules. It carries no
             * translation target, so handle it before the target check. */
            if (!empty((string)($r->nordr ?? ''))) {
                $noRdr[] = '        ' . implode(' ', $parts) . ' counter return comment "no rdr"';
                continue;
            }

            $target = trim((string)$r->target);
            if (!filter_var($target, FILTER_VALIDATE_IP, FILTER_FLAG_IPV4)) {
                continue;
            }
            $to = $localPort !== '' ? "$target:$localPort" : $target;
            $pre[] = '        ' . implode(' ', $parts) . " dnat to $to comment \"port forward\"";

            /* let the rewritten flow through the (drop-policy) forward hook. */
            $fp = ["ip daddr $target", $proto];
            if ($localPort !== '') {
                $fp[] = "dport " . fmt_ports($localPort);
            } elseif ($extPort !== null) {
                $fp[] = "dport $extPort";
            }
            $passes[] = '        ' . implode(' ', $fp) . ' ct status dnat counter accept comment "port forward pass"';
        }
    }

    /* 1:1 NAT (binat): bidirectional mapping between an external address and
     * an internal one. Inbound rewrites destination (external -> internal),
     * outbound rewrites source (internal -> external), plus a forward pass so
     * the rewritten inbound flow crosses the drop-policy forward hook. Only
     * single IPv4 hosts are handled for now (subnet netmap and IPv6 NPt are
     * left on the roadmap). */
    if (isset($cfg->nat->onetoone)) {
        foreach ($cfg->nat->onetoone as $r) {
            if (isset($r->disabled)) {
                continue;
            }
            $dev = $ifaces[trim((string)$r->interface)]['device'] ?? null;
            if ($dev === null) {
                continue;
            }
            $external = trim((string)$r->external);
            $internal = resolve_endpoint($r->source ?? null, 'ip', $ifaces, $aliases);
            if (!filter_var($external, FILTER_VALIDATE_IP, FILTER_FLAG_IPV4)) {
                continue;
            }
            if ($internal === null || !filter_var($internal, FILTER_VALIDATE_IP, FILTER_FLAG_IPV4)) {
                continue;
            }
            $peer = resolve_endpoint($r->destination ?? null, 'ip', $ifaces, $aliases);

            $preParts = ['iifname ' . ifname_token($dev), "ip daddr $external"];
            if ($peer !== null) {
                $preParts[] = "ip saddr $peer";
            }
            $pre[] = '        ' . implode(' ', $preParts) . " dnat to $internal comment \"1:1 nat inbound\"";

            $postParts = ['oifname ' . ifname_token($dev), "ip saddr $internal"];
            if ($peer !== null) {
                $postParts[] = "ip daddr $peer";
            }
            $post[] = '        ' . implode(' ', $postParts) . " snat to $external comment \"1:1 nat outbound\"";

            $passes[] = "        ip daddr $internal ct status dnat counter accept comment \"1:1 nat pass\"";
        }
    }

    /* Ordering in the postrouting chain matters: No-NAT "return" rules first
     * so excluded traffic is never translated, then the specific source NAT
     * (manual outbound rules and 1:1 NAT), and finally the broad automatic
     * masquerade which would otherwise claim addresses that should follow a
     * more specific mapping. */
    $post = array_merge($noNat, $post, $autoMasq);

    /* No-rdr returns must be evaluated before any dnat rule so excluded
     * traffic is never redirected. */
    $pre = array_merge($noRdr, $pre);

    return ['pre' => $pre, 'post' => $post, 'passes' => $passes];
}

/* Translate the comma-separated pf icmp type names stored in config.xml into
 * an nft type match ("icmp type { ... }" / "icmpv6 type { ... }"). Unknown or
 * unmappable names are dropped silently so we never emit an invalid keyword.
 * Returns null when nothing maps, meaning the rule should match every icmp
 * type (no type clause). */
function icmp_type_match(string $names, string $family): ?string
{
    // pf icmp type name -> nft icmp type name.
    static $v4 = [
        'echorep' => 'echo-reply', 'echoreq' => 'echo-request',
        'unreach' => 'destination-unreachable', 'squench' => 'source-quench',
        'redir' => 'redirect', 'routeradv' => 'router-advertisement',
        'routersol' => 'router-solicitation', 'timex' => 'time-exceeded',
        'paramprob' => 'parameter-problem', 'timereq' => 'timestamp-request',
        'timerep' => 'timestamp-reply', 'inforeq' => 'info-request',
        'inforep' => 'info-reply', 'maskreq' => 'address-mask-request',
        'maskrep' => 'address-mask-reply',
    ];
    // pf icmp6 type name -> nft icmpv6 type name.
    static $v6 = [
        'unreach' => 'destination-unreachable', 'toobig' => 'packet-too-big',
        'timex' => 'time-exceeded', 'paramprob' => 'parameter-problem',
        'echoreq' => 'echo-request', 'echorep' => 'echo-reply',
        'groupqry' => 'mld-listener-query', 'listqry' => 'mld-listener-query',
        'grouprep' => 'mld-listener-report', 'listenrep' => 'mld-listener-report',
        'groupterm' => 'mld-listener-done', 'listendone' => 'mld-listener-done',
        'routersol' => 'nd-router-solicit', 'routeradv' => 'nd-router-advert',
        'neighbrsol' => 'nd-neighbor-solicit', 'neighbradv' => 'nd-neighbor-advert',
        'redir' => 'nd-redirect', 'routrrenum' => 'router-renumbering',
    ];
    $map = $family === 'ip6' ? $v6 : $v4;
    $kw = $family === 'ip6' ? 'icmpv6' : 'icmp';

    $out = [];
    foreach (preg_split('/[\s,]+/', strtolower(trim($names))) as $name) {
        if ($name === '' || $name === 'any') {
            continue;
        }
        if (isset($map[$name]) && !in_array($map[$name], $out, true)) {
            $out[] = $map[$name];
        }
    }
    if (empty($out)) {
        return null;
    }
    if (count($out) === 1) {
        return "$kw type {$out[0]}";
    }
    return "$kw type { " . implode(', ', $out) . ' }';
}

/* Translate a single <rule> into one nft statement, or null when the rule
 * uses a feature this iteration does not handle yet. */
function rule_line(SimpleXMLElement $rule, array $ifaces, array $aliases = []): ?string
{
    if (isset($rule->disabled)) {
        return null;
    }
    $type = trim((string)$rule->type) ?: 'pass';
    $verdict = ['pass' => 'accept', 'block' => 'drop', 'reject' => 'reject'][$type] ?? null;
    if ($verdict === null) {
        return null;
    }

    $ipproto = trim((string)$rule->ipprotocol) ?: 'inet';
    $family = $ipproto === 'inet6' ? 'ip6' : 'ip';

    $parts = [];

    /* ingress/egress interface match. */
    $dir = trim((string)$rule->direction) ?: 'in';
    $ifkey = trim((string)$rule->interface);
    if ($ifkey !== '' && isset($ifaces[$ifkey])) {
        $kw = $dir === 'out' ? 'oifname' : 'iifname';
        $parts[] = "$kw " . ifname_token($ifaces[$ifkey]['device']);
    }

    /* layer 4 protocol. */
    $proto = strtolower(trim((string)$rule->protocol));
    $l4 = null;
    if ($proto === 'tcp' || $proto === 'udp') {
        $l4 = $proto;
    } elseif ($proto === 'tcp/udp') {
        $l4 = 'th';
    } elseif ($proto === 'icmp') {
        $parts[] = $family === 'ip6' ? 'meta l4proto ipv6-icmp' : 'ip protocol icmp';
        $icmpNames = $family === 'ip6'
            ? trim((string)$rule->icmp6type)
            : trim((string)$rule->icmptype);
        if ($icmpNames !== '') {
            $icmpMatch = icmp_type_match($icmpNames, $family);
            if ($icmpMatch !== null) {
                $parts[] = $icmpMatch;
            }
        }
    } elseif ($proto !== '' && $proto !== 'any') {
        /* Other IP protocols (VPN passthrough, routing, multicast...). Map to
         * the IANA protocol number, which `meta l4proto` always accepts, so we
         * never risk an unknown keyword. Unrecognised protocols are skipped. */
        $protoNumbers = [
            'igmp' => 2, 'ipencap' => 4, 'ipv6' => 41, 'gre' => 47, 'esp' => 50,
            'ah' => 51, 'ospf' => 89, 'pim' => 103, 'vrrp' => 112, 'carp' => 112,
            'pfsync' => 240, 'sctp' => 132, 'etherip' => 97, 'l2tp' => 115,
        ];
        if (isset($protoNumbers[$proto])) {
            $parts[] = 'meta l4proto ' . $protoNumbers[$proto];
        }
    }

    /* source / destination addresses. */
    $hasL3 = $proto === 'icmp';
    $saddr = resolve_endpoint($rule->source ?? null, $family, $ifaces, $aliases);
    if ($saddr !== null) {
        $parts[] = "$family saddr " . (ep_negated($rule->source ?? null) ? '!= ' : '') . $saddr;
        $hasL3 = true;
    }
    $daddr = resolve_endpoint($rule->destination ?? null, $family, $ifaces, $aliases);
    if ($daddr !== null) {
        $parts[] = "$family daddr " . (ep_negated($rule->destination ?? null) ? '!= ' : '') . $daddr;
        $hasL3 = true;
    }

    /* Keep the rule scoped to its address family even when no L3 address
     * match could be expressed (e.g. a dynamically addressed interface):
     * an inet6 rule must never match IPv4 traffic and vice versa. */
    if (!$hasL3) {
        array_unshift($parts, $family === 'ip6' ? 'meta nfproto ipv6' : 'meta nfproto ipv4');
    }

    /* ports (tcp/udp only). */
    if ($l4 !== null) {
        $sport = resolve_port((string)($rule->source->port ?? ''), $aliases);
        $dport = resolve_port((string)($rule->destination->port ?? ''), $aliases);
        if ($l4 === 'th') {
            $parts[] = 'meta l4proto { tcp, udp }';
            if ($sport !== null) {
                $parts[] = "th sport $sport";
            }
            if ($dport !== null) {
                $parts[] = "th dport $dport";
            }
        } else {
            if ($sport !== null) {
                $parts[] = "$l4 sport $sport";
            }
            if ($dport !== null) {
                $parts[] = "$l4 dport $dport";
            }
            if ($sport === null && $dport === null) {
                $parts[] = "meta l4proto $l4";
            }
        }
    }

    /* TCP flags. config.xml stores tcpflags1 (flags that must be set) and
     * tcpflags2 (the mask, i.e. the flags examined), both comma-separated
     * lowercase names, plus tcpflags_any (match any combination). nft uses
     * "tcp flags & (mask) == set". Only emitted for plain tcp rules with a
     * non-empty mask and set, to avoid ambiguous matches. */
    if ($proto === 'tcp' && !isset($rule->tcpflags_any)) {
        // pf/OPNsense flag name -> nft flag name (ECE is "ecn" in nft).
        $flagMap = [
            'fin' => 'fin', 'syn' => 'syn', 'rst' => 'rst', 'psh' => 'psh',
            'ack' => 'ack', 'urg' => 'urg', 'ece' => 'ecn', 'cwr' => 'cwr',
        ];
        $mapFlags = function (string $csv) use ($flagMap): array {
            $out = [];
            foreach (preg_split('/[\s,]+/', strtolower(trim($csv))) as $f) {
                if ($f !== '' && isset($flagMap[$f]) && !in_array($flagMap[$f], $out, true)) {
                    $out[] = $flagMap[$f];
                }
            }
            return $out;
        };
        $setFlags = $mapFlags((string)$rule->tcpflags1);
        $maskFlags = $mapFlags((string)$rule->tcpflags2);
        if (!empty($setFlags) && !empty($maskFlags)) {
            $parts[] = 'tcp flags & (' . implode('|', $maskFlags) . ') == '
                . implode('|', $setFlags);
        }
    }

    $descr = trim((string)$rule->descr);
    $descr = preg_replace('/[^\x20-\x7E]/', '', $descr);
    $descr = str_replace('"', "'", $descr);

    $stmt = trim(implode(' ', $parts));
    $line = '        ' . ($stmt === '' ? '' : $stmt . ' ') . "counter $verdict";
    if ($descr !== '') {
        $line .= " comment \"$descr\"";
    }
    return $line;
}

/* Resolve a NetworkAliasField value (source_net / destination_net of an MVC
 * model rule) for one address family. Returns ['expr' => nft token|null,
 * 'ok' => bool]. "ok" is false when the value is a literal address that
 * belongs to the other family, signalling that no rule line should be
 * produced for the current family. A null expr means "any" (no L3 match). */
function mvc_resolve_net(string $value, string $family, array $ifaces, array $aliases): array
{
    $value = trim($value);
    if ($value === '' || strtolower($value) === 'any') {
        return ['expr' => null, 'ok' => true];
    }
    $literals = [];
    $ref = null;
    foreach (preg_split('/[\s,]+/', $value) ?: [] as $tok) {
        $tok = trim($tok);
        if ($tok === '') {
            continue;
        }
        $aliasRef = alias_addr_ref($tok, $family, $aliases);
        if ($aliasRef !== null) {
            $ref = $aliasRef;
            continue;
        }
        if (isset($ifaces[$tok])) {
            /* an interface keyword resolves to its statically known subnet */
            $cidr = $family === 'ip6' ? $ifaces[$tok]['cidr6'] : $ifaces[$tok]['cidr4'];
            if ($cidr !== null) {
                $literals[] = $cidr;
            }
            continue;
        }
        $ipPart = strpos($tok, '/') !== false ? substr($tok, 0, strpos($tok, '/')) : $tok;
        $isv6 = filter_var($ipPart, FILTER_VALIDATE_IP, FILTER_FLAG_IPV6) !== false;
        $isv4 = filter_var($ipPart, FILTER_VALIDATE_IP, FILTER_FLAG_IPV4) !== false;
        if ($isv6 && $family === 'ip6') {
            $literals[] = $tok;
        } elseif ($isv4 && $family === 'ip') {
            $literals[] = $tok;
        } elseif ($isv4 || $isv6) {
            /* literal belongs to the other family: skip this family entirely */
            return ['expr' => null, 'ok' => false];
        }
        /* unknown tokens (hostnames, dynamic refs) are ignored */
    }
    if (!empty($literals)) {
        $expr = count($literals) === 1 ? $literals[0] : '{ ' . implode(', ', array_unique($literals)) . ' }';
        return ['expr' => $expr, 'ok' => true];
    }
    return ['expr' => $ref, 'ok' => true];
}

/* Translate one MVC model firewall rule (OPNsense/Firewall/Filter) into nft
 * rule lines. The model schema differs from the legacy <filter><rule> one
 * (action/source_net/destination_net, multiple interfaces, inet46), so it
 * gets its own translator. A rule may yield several lines: an "any" (inet46)
 * rule is emitted once per family. Every line carries the rule uuid as its
 * comment so per-rule counters can be mapped back to the GUI. */
function mvc_rule_line(SimpleXMLElement $rule, array $ifaces, array $aliases): array
{
    if (trim((string)($rule->enabled ?? '1')) === '0') {
        return [];
    }
    $action = strtolower(trim((string)$rule->action)) ?: 'pass';
    $verdict = ['pass' => 'accept', 'block' => 'drop', 'reject' => 'reject'][$action] ?? null;
    if ($verdict === null) {
        return [];
    }

    $uuid = trim((string)$rule['uuid']);
    $ipproto = trim((string)$rule->ipprotocol) ?: 'inet';
    $families = $ipproto === 'inet6' ? ['ip6'] : ($ipproto === 'inet46' ? ['ip', 'ip6'] : ['ip']);

    /* interface match, shared across families */
    $dir = trim((string)$rule->direction) ?: 'in';
    $kw = $dir === 'out' ? 'oifname' : 'iifname';
    $devs = [];
    foreach (preg_split('/[\s,]+/', trim((string)$rule->interface)) ?: [] as $ik) {
        $ik = trim($ik);
        if ($ik !== '' && isset($ifaces[$ik])) {
            $devs[] = $ifaces[$ik]['device'];
        }
    }
    $ifExpr = null;
    if (!empty($devs)) {
        $neg = trim((string)($rule->interfacenot ?? '0')) === '1' ? '!= ' : '';
        $devs = array_values(array_unique($devs));
        if (count($devs) === 1 && $neg === '') {
            $ifExpr = "$kw " . ifname_token($devs[0]);
        } else {
            $ifExpr = "$kw $neg" . '{ ' . implode(', ', array_map('ifname_token', $devs)) . ' }';
        }
    }

    $proto = strtolower(trim((string)$rule->protocol));
    $sport = resolve_port((string)($rule->source_port ?? ''), $aliases);
    $dport = resolve_port((string)($rule->destination_port ?? ''), $aliases);
    $sneg = trim((string)($rule->source_not ?? '0')) === '1' ? '!= ' : '';
    $dneg = trim((string)($rule->destination_not ?? '0')) === '1' ? '!= ' : '';

    $lines = [];
    foreach ($families as $family) {
        $parts = [];
        if ($ifExpr !== null) {
            $parts[] = $ifExpr;
        }

        $hasL3 = false;
        /* layer 4 protocol */
        $l4 = null;
        if ($proto === 'tcp' || $proto === 'udp') {
            $l4 = $proto;
        } elseif ($proto === 'tcp/udp') {
            $l4 = 'th';
        } elseif ($proto === 'icmp') {
            if ($family === 'ip6') {
                $parts[] = 'meta l4proto ipv6-icmp';
                $names = trim((string)$rule->icmp6type);
            } else {
                $parts[] = 'ip protocol icmp';
                $names = trim((string)$rule->icmptype);
            }
            $hasL3 = true;
            if ($names !== '') {
                $icmpMatch = icmp_type_match($names, $family);
                if ($icmpMatch !== null) {
                    $parts[] = $icmpMatch;
                }
            }
        } elseif ($proto !== '' && $proto !== 'any') {
            $protoNumbers = [
                'igmp' => 2, 'ipencap' => 4, 'ipv6' => 41, 'gre' => 47, 'esp' => 50,
                'ah' => 51, 'ospf' => 89, 'pim' => 103, 'vrrp' => 112, 'carp' => 112,
                'pfsync' => 240, 'sctp' => 132, 'etherip' => 97, 'l2tp' => 115,
            ];
            if (isset($protoNumbers[$proto])) {
                $parts[] = 'meta l4proto ' . $protoNumbers[$proto];
            }
        }

        /* source / destination addresses */
        $s = mvc_resolve_net((string)($rule->source_net ?? ''), $family, $ifaces, $aliases);
        if (!$s['ok']) {
            continue;
        }
        $d = mvc_resolve_net((string)($rule->destination_net ?? ''), $family, $ifaces, $aliases);
        if (!$d['ok']) {
            continue;
        }
        if ($s['expr'] !== null) {
            $parts[] = "$family saddr $sneg" . $s['expr'];
            $hasL3 = true;
        }
        if ($d['expr'] !== null) {
            $parts[] = "$family daddr $dneg" . $d['expr'];
            $hasL3 = true;
        }
        if (!$hasL3) {
            array_unshift($parts, $family === 'ip6' ? 'meta nfproto ipv6' : 'meta nfproto ipv4');
        }

        /* ports (tcp/udp only) */
        if ($l4 !== null) {
            if ($l4 === 'th') {
                $parts[] = 'meta l4proto { tcp, udp }';
                if ($sport !== null) {
                    $parts[] = "th sport $sneg$sport";
                }
                if ($dport !== null) {
                    $parts[] = "th dport $dneg$dport";
                }
            } else {
                if ($sport !== null) {
                    $parts[] = "$l4 sport $sneg$sport";
                }
                if ($dport !== null) {
                    $parts[] = "$l4 dport $dneg$dport";
                }
                if ($sport === null && $dport === null) {
                    $parts[] = "meta l4proto $l4";
                }
            }
        }

        /* logging prefixes the verdict; nft logs then continues to the verdict.
         * The prefix carries the metadata the firewall log viewer needs that is
         * not otherwise present in the kernel netfilter log line: the rule action
         * and the rule uuid (resolved back to its description by read_log.py).
         * Format: "muros,<action>,<uuid> " (kept well under the 64 byte limit). */
        $log = '';
        if (trim((string)($rule->log ?? '0')) === '1') {
            $log = 'log prefix "muros,' . $action . ',' . $uuid . ' " ';
        }
        $stmt = trim(implode(' ', $parts));
        $line = '        ' . ($stmt === '' ? '' : $stmt . ' ') . $log . "counter $verdict";
        if ($uuid !== '') {
            $line .= " comment \"$uuid\"";
        }
        $lines[] = $line;
    }

    return $lines;
}

/* ----------------------------------------------------------------- */

$path = $argv[1] ?? '/conf/config.xml';
if (!is_readable($path)) {
    fwrite(STDERR, "cannot read configuration: $path\n");
    exit(1);
}
$cfg = simplexml_load_file($path);
if ($cfg === false) {
    fwrite(STDERR, "cannot parse configuration: $path\n");
    exit(1);
}

$ifaces = build_interfaces($cfg);
$aliases = build_aliases($cfg);

/* CARP/VRRP high availability: keepalived owns the virtual addresses and its
 * VRRP adverts (IP protocol 112) reach this host either as multicast
 * (224.0.0.18 / ff02::12) or, in unicast HA mode, addressed to the firewall
 * itself. The default-deny input chain would otherwise drop them and every
 * node would elect itself master (split brain). Emit an automatic accept in
 * the input chain whenever at least one CARP virtual IP is configured. */
$carp_enabled = false;
if (isset($cfg->virtualip->vip)) {
    foreach ($cfg->virtualip->vip as $vip) {
        if ((string)$vip->mode === 'carp') {
            $carp_enabled = true;
            break;
        }
    }
}

/* Default block logging. OPNsense logs packets that fall through to the
 * default deny unless the operator clears the option (stored as
 * syslog/nologdefaultblock). The logged drop is appended at the end of the
 * input and forward chains so it runs after filter_rules returns, replacing
 * the silent "policy drop". The "muros,block,default" prefix is what
 * read_log.py turns into the firewall log viewer / dashboard widget records. */
$default_block_drop = !isset($cfg->syslog->nologdefaultblock)
    ? '        log prefix "muros,block,default " counter drop comment "default deny rule"'
    : '        counter drop comment "default deny rule"';

$rules = [];
/* legacy <filter><rule> entries */
if (isset($cfg->filter)) {
    foreach ($cfg->filter->rule as $rule) {
        $line = rule_line($rule, $ifaces, $aliases);
        if ($line !== null) {
            $rules[] = $line;
        }
    }
}
/* MVC model rules (OPNsense/Firewall/Filter), as managed by the
 * "Firewall: Rules" GUI. These are sorted by their sequence field before
 * translation so evaluation order matches what the operator configured. */
if (isset($cfg->OPNsense->Firewall->Filter->rules->rule)) {
    $mvcRules = [];
    foreach ($cfg->OPNsense->Firewall->Filter->rules->rule as $rule) {
        $mvcRules[] = $rule;
    }
    usort($mvcRules, function ($a, $b) {
        return ((int)$a->sequence) <=> ((int)$b->sequence);
    });
    foreach ($mvcRules as $rule) {
        foreach (mvc_rule_line($rule, $ifaces, $aliases) as $line) {
            $rules[] = $line;
        }
    }
}

$wanDevs = wan_devices($cfg, $ifaces);
$nat = build_nat($cfg, $ifaces, $wanDevs, $aliases);
$aliasSets = alias_set_lines($aliases);

$martians = martian_lines($ifaces);
$lockout = '        tcp dport ' . fmt_addr_set(ANTI_LOCKOUT_PORTS)
    . ' counter accept comment "anti-lockout (ssh/web)"';

$out = [];
$out[] = '#!/usr/sbin/nft -f';
$out[] = '# Generated by MurOS nft_build.php. Do not edit by hand.';
$out[] = 'flush ruleset';
$out[] = '';
$out[] = 'table inet muros {';
foreach ($aliasSets as $line) {
    $out[] = $line;
}
if (!empty($aliasSets)) {
    $out[] = '';
}
$out[] = '    chain input {';
$out[] = '        type filter hook input priority 0; policy drop;';
$out[] = '        iif "lo" accept';
$out[] = '        ct state established,related accept';
$out[] = '        ct state invalid counter drop';
$out[] = '        meta l4proto ipv6-icmp accept comment "IPv6 neighbor discovery"';
$out[] = '        ip protocol icmp accept';
if ($carp_enabled) {
    $out[] = '        meta l4proto 112 counter accept comment "CARP/VRRP adverts"';
}
// Anti-lockout is emitted before the martian/block-private rules on purpose:
// when the management interface sits on a private network (a common
// WAN-on-LAN setup), block-private would otherwise drop new management
// connections and lock the operator out of the box.
$out[] = $lockout;
foreach ($martians as $m) {
    $out[] = $m;
}
$out[] = '        jump filter_rules';
$out[] = $default_block_drop;
$out[] = '    }';
$out[] = '';
$out[] = '    chain forward {';
$out[] = '        type filter hook forward priority 0; policy drop;';
$out[] = '        ct state established,related accept';
$out[] = '        ct state invalid counter drop';
foreach ($martians as $m) {
    $out[] = $m;
}
foreach ($nat['passes'] as $p) {
    $out[] = $p;
}
$out[] = '        jump filter_rules';
$out[] = $default_block_drop;
$out[] = '    }';
$out[] = '';
$out[] = '    chain output {';
$out[] = '        type filter hook output priority 0; policy accept;';
$out[] = '    }';
$out[] = '';
if (!empty($nat['pre'])) {
    $out[] = '    chain prerouting {';
    $out[] = '        type nat hook prerouting priority dstnat; policy accept;';
    foreach ($nat['pre'] as $line) {
        $out[] = $line;
    }
    $out[] = '    }';
    $out[] = '';
}
if (!empty($nat['post'])) {
    $out[] = '    chain postrouting {';
    $out[] = '        type nat hook postrouting priority srcnat; policy accept;';
    foreach ($nat['post'] as $line) {
        $out[] = $line;
    }
    $out[] = '    }';
    $out[] = '';
}
$out[] = '    chain filter_rules {';
if (empty($rules)) {
    $out[] = '        # no translatable user rules';
} else {
    foreach ($rules as $r) {
        $out[] = $r;
    }
}
$out[] = '    }';
$out[] = '}';
$out[] = '';

echo implode("\n", $out);
