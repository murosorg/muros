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
 * Iteration 1 covers the common stateful filter cases:
 *   - pass / block / reject, IPv4 (inet) and IPv6 (inet6)
 *   - per-interface ingress match (interface key -> device)
 *   - tcp / udp / icmp, source and destination address/network and ports
 *   - block-private / block-bogons martian drops on flagged interfaces
 *   - a mandatory anti-lockout allowance (ssh + web to the firewall)
 *   - NAT: automatic/hybrid outbound masquerade, advanced outbound rules
 *     and destination NAT port forwards (with their associated forward pass)
 *
 * Not yet handled (kept on the roadmap): 1:1 NAT and NPt, policy based
 * routing (route-to/reply-to), traffic shaping/dummynet, aliases as named
 * sets, and the finer pf state options.
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
    /* OPNsense stores ranges as "from:to"; nft wants "from-to". */
    $ports = str_replace(':', '-', $ports);
    if (strpos($ports, ',') !== false) {
        $items = array_filter(array_map('trim', explode(',', $ports)), 'strlen');
        return '{ ' . implode(', ', $items) . ' }';
    }
    return $ports;
}

function fmt_addr_set(array $values): string
{
    return count($values) === 1 ? $values[0] : '{ ' . implode(', ', $values) . ' }';
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
                  'blockpriv' => !empty((string)$node->blockpriv),
                  'blockbogons' => !empty((string)$node->blockbogons)];
        $ip4 = trim((string)$node->ipaddr);
        $sub4 = trim((string)$node->subnet);
        if (filter_var($ip4, FILTER_VALIDATE_IP, FILTER_FLAG_IPV4) && $sub4 !== '') {
            $entry['cidr4'] = network_of($ip4, (int)$sub4);
        }
        $ip6 = trim((string)$node->ipaddrv6);
        $sub6 = trim((string)$node->subnetv6);
        if (filter_var($ip6, FILTER_VALIDATE_IP, FILTER_FLAG_IPV6) && $sub6 !== '') {
            $entry['cidr6'] = $ip6 . '/' . $sub6;
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
function resolve_endpoint(?SimpleXMLElement $ep, string $family, array $ifaces): ?string
{
    if ($ep === null || isset($ep->any)) {
        return null;
    }
    $addr = trim((string)$ep->address);
    if ($addr !== '') {
        return $addr;
    }
    $net = trim((string)$ep->network);
    if ($net === '') {
        return null;
    }
    /* network is an interface key; use its statically known subnet. */
    if (isset($ifaces[$net])) {
        return $family === 'ip6' ? $ifaces[$net]['cidr6'] : $ifaces[$net]['cidr4'];
    }
    /* literal CIDR stored directly in network. */
    if (strpos($net, '/') !== false) {
        return $net;
    }
    return null;
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
function build_nat(SimpleXMLElement $cfg, array $ifaces, array $wanDevs): array
{
    $pre = [];
    $post = [];
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
            $post[] = '        oifname ' . ifname_token($dev) . ' ip saddr ' . fmt_addr_set($localNets)
                . ' counter masquerade comment "auto outbound nat"';
        }
    }

    /* advanced / hybrid: explicit outbound rules. */
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
            $src = resolve_endpoint($r->source ?? null, 'ip', $ifaces);
            if ($src !== null) {
                $parts[] = "ip saddr $src";
            }
            $proto = strtolower(trim((string)$r->protocol));
            if ($proto === 'tcp' || $proto === 'udp') {
                $dport = fmt_ports((string)($r->destination->port ?? ''));
                if ($dport !== null) {
                    $parts[] = "$proto dport $dport";
                }
            }
            $target = trim((string)$r->target);
            $verb = filter_var($target, FILTER_VALIDATE_IP, FILTER_FLAG_IPV4) ? "snat to $target" : 'masquerade';
            $post[] = '        ' . implode(' ', $parts) . " counter $verb comment \"outbound nat\"";
        }
    }

    /* port forwards: destination NAT plus an associated forward pass. */
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
            $target = trim((string)$r->target);
            if (!filter_var($target, FILTER_VALIDATE_IP, FILTER_FLAG_IPV4)) {
                continue;
            }
            $extPort = fmt_ports((string)($r->{'destination'}->port ?? ''));
            $localPort = trim((string)$r->{'local-port'});
            $parts = [];
            if ($dev !== null) {
                $parts[] = 'iifname ' . ifname_token($dev);
            }
            $parts[] = "$proto";
            if ($extPort !== null) {
                $parts[] = "dport $extPort";
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

    return ['pre' => $pre, 'post' => $post, 'passes' => $passes];
}

/* Translate a single <rule> into one nft statement, or null when the rule
 * uses a feature this iteration does not handle yet. */
function rule_line(SimpleXMLElement $rule, array $ifaces): ?string
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
    }

    /* source / destination addresses. */
    $hasL3 = $proto === 'icmp';
    $saddr = resolve_endpoint($rule->source ?? null, $family, $ifaces);
    if ($saddr !== null) {
        $parts[] = "$family saddr $saddr";
        $hasL3 = true;
    }
    $daddr = resolve_endpoint($rule->destination ?? null, $family, $ifaces);
    if ($daddr !== null) {
        $parts[] = "$family daddr $daddr";
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
        $sport = fmt_ports((string)($rule->source->port ?? ''));
        $dport = fmt_ports((string)($rule->destination->port ?? ''));
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

$rules = [];
if (isset($cfg->filter)) {
    foreach ($cfg->filter->rule as $rule) {
        $line = rule_line($rule, $ifaces);
        if ($line !== null) {
            $rules[] = $line;
        }
    }
}

$wanDevs = wan_devices($cfg, $ifaces);
$nat = build_nat($cfg, $ifaces, $wanDevs);

$martians = martian_lines($ifaces);
$lockout = '        tcp dport ' . fmt_addr_set(ANTI_LOCKOUT_PORTS)
    . ' counter accept comment "anti-lockout (ssh/web)"';

$out = [];
$out[] = '#!/usr/sbin/nft -f';
$out[] = '# Generated by MurOS nft_build.php. Do not edit by hand.';
$out[] = 'flush ruleset';
$out[] = '';
$out[] = 'table inet muros {';
$out[] = '    chain input {';
$out[] = '        type filter hook input priority 0; policy drop;';
$out[] = '        iif "lo" accept';
$out[] = '        ct state established,related accept';
$out[] = '        ct state invalid counter drop';
$out[] = '        meta l4proto ipv6-icmp accept comment "IPv6 neighbor discovery"';
$out[] = '        ip protocol icmp accept';
foreach ($martians as $m) {
    $out[] = $m;
}
$out[] = $lockout;
$out[] = '        jump filter_rules';
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
