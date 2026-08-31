<?php

/*
 * Copyright (C) 2026 MurOS
 * All rights reserved.
 *
 * Provision the Linux substrate for policy based routing (route-to).
 *
 * For every gateway a dedicated routing table holds a default route through
 * that gateway, and an ip rule steers packets carrying the gateway mark into
 * that table. nft_build.php tags a flow with the same mark in its
 * mangle_prerouting chain (gateway_mark()), so a firewall rule pinned to a
 * gateway sends its traffic out the matching uplink while everything else
 * follows the main table.
 *
 * Gateway groups get a table too, holding the members of the highest tier that
 * still passes the trigger of the group, as a multipath route when the tier
 * holds more than one. That is the failover: the monitor reloads the ruleset
 * when a member changes state, this script runs again and the table of the
 * group points at what is left.
 *
 * The table id and the ip rule priority both equal the mark, so this script
 * owns and can rebuild every table/rule in the mark range without touching
 * anything else on the box. It is idempotent: it installs the desired set and
 * removes our stale tables/rules from a previous run.
 *
 * Usage:
 *   setup_policy_routing.php name gateway-ip device [name gw dev ...]
 *   setup_policy_routing.php --auto
 *   setup_policy_routing.php --flush
 */

const MARK_MIN = 1000;
const MARK_MAX = 60999;

/* Same formula as nft_build.php gateway_mark(); the two must agree. */
function gateway_mark(string $name): int
{
    return MARK_MIN + (crc32($name) % 60000);
}

$GLOBALS['dry_run'] = false;

function run(string $cmd): void
{
    if (!empty($GLOBALS['dry_run'])) {
        echo $cmd . "\n";
        return;
    }
    exec($cmd . ' 2>/dev/null');
}

/* Marks we currently own, read back from the live ip rules. A rule is ours
 * only when it carries an fwmark and lives at a priority in our range that
 * equals that mark: that is the exact signature this script writes. The check
 * deliberately ignores every rule without an fwmark so the kernel's built-in
 * local/main/default rules (priorities 0, 32766, 32767, which fall inside the
 * range) are never mistaken for ours and deleted. */
function existing_marks(string $fam): array
{
    $json = shell_exec(sprintf('/usr/sbin/ip %s -j rule show 2>/dev/null', $fam)) ?: '[]';
    $rules = json_decode($json, true) ?: [];
    $marks = [];
    foreach ($rules as $rule) {
        if (!isset($rule['priority']) || !isset($rule['fwmark'])) {
            continue;
        }
        $prio = (int)$rule['priority'];
        /* fwmark is reported in hex (e.g. "0x63d8"); normalise to int */
        $mark = is_string($rule['fwmark']) ? (int)hexdec($rule['fwmark']) : (int)$rule['fwmark'];
        if ($prio >= MARK_MIN && $prio <= MARK_MAX && $prio === $mark) {
            $marks[$prio] = true;
        }
    }
    return $marks;
}

function remove_mark(string $fam, int $mark): void
{
    /* Drop every rule at this priority, then clear its table. The guard keeps
     * the loop finite even if a delete unexpectedly fails. */
    for ($attempt = 0; $attempt < 16; $attempt++) {
        $json = shell_exec(sprintf('/usr/sbin/ip %s -j rule show 2>/dev/null', $fam)) ?: '[]';
        $rules = json_decode($json, true) ?: [];
        $found = false;
        foreach ($rules as $rule) {
            if ((int)($rule['priority'] ?? -1) === $mark) {
                $found = true;
                break;
            }
        }
        if (!$found) {
            break;
        }
        run(sprintf('/usr/sbin/ip %s rule del priority %d', $fam, $mark));
    }
    run(sprintf('/usr/sbin/ip %s route flush table %d', $fam, $mark));
}

/* Add one next hop to the table of a gateway or of a gateway group. A group
 * whose active tier holds several gateways ends up with several next hops in
 * the same table, which is how the load balancing of a tier is expressed on
 * Linux. Hops are keyed by address family: a group mixing an IPv4 and an IPv6
 * gateway gets one table per family under the same mark, and the ip rule of
 * each family only ever reaches the routes of that family. */
function add_hop(array &$desired, string $name, string $gwip, string $dev, int $weight = 1): void
{
    if ($name === '' || $dev === '' || filter_var($gwip, FILTER_VALIDATE_IP) === false) {
        return;
    }

    $fam = strpos($gwip, ':') !== false ? '-6' : '-4';
    $mark = gateway_mark($name);

    if (!isset($desired[$fam][$mark])) {
        $desired[$fam][$mark] = ['name' => $name, 'hops' => []];
    }

    foreach ($desired[$fam][$mark]['hops'] as $hop) {
        if ($hop['gwip'] === $gwip && $hop['dev'] === $dev) {
            return;
        }
    }

    $desired[$fam][$mark]['hops'][] = ['gwip' => $gwip, 'dev' => $dev, 'weight' => max(1, $weight)];
}

/* Read what to provision from the running configuration. This lets the
 * firewall apply refresh the tables after every ruleset reload without knowing
 * the topology, and keeps them in sync when a gateway address or interface
 * changes. Disabled, loopback and inactive gateways are skipped by the model.
 *
 * Gateway groups are provisioned as well, and this is what makes multi-WAN
 * failover work: nft_build.php marks a rule pinned to a group exactly like a
 * rule pinned to a gateway, so the group needs a table of its own. Its next
 * hops are the members of the highest tier that still satisfies the trigger of
 * the group, which the model resolves from the live dpinger status. When a
 * member goes down the monitor runs rc.routing_configure, which reloads the
 * ruleset, which calls this script again, and the table then points at the
 * next tier. */
function discover_gateways(): array
{
    require_once 'config.inc';
    require_once 'util.inc';
    require_once 'interfaces.inc';

    $desired = [];

    foreach ((new \OPNsense\Routing\Gateways())->gatewaysIndexedByName() as $name => $gw) {
        add_hop($desired, (string)$name, (string)($gw['gateway'] ?? ''), (string)($gw['if'] ?? ''));
    }

    $status = function_exists('return_gateways_status') ? return_gateways_status() : [];
    foreach ((new \OPNsense\Routing\GatewayGroups())->getActiveGroups($status) as $name => $members) {
        foreach ($members as $member) {
            add_hop(
                $desired,
                (string)$name,
                (string)($member['gwip'] ?? ''),
                (string)($member['int'] ?? ''),
                (int)($member['weight'] ?? 1)
            );
        }
    }

    return $desired;
}

/* A single next hop is a plain default route, several are a multipath route.
 * The weights come from the gateway configuration, so a tier balancing two
 * uplinks of different capacity splits the flows accordingly. */
function route_command(string $fam, int $mark, array $hops): string
{
    if (count($hops) === 1) {
        return sprintf(
            '/usr/sbin/ip %s route replace default via %s dev %s table %d',
            $fam,
            escapeshellarg($hops[0]['gwip']),
            escapeshellarg($hops[0]['dev']),
            $mark
        );
    }

    $command = sprintf('/usr/sbin/ip %s route replace default table %d', $fam, $mark);
    foreach ($hops as $hop) {
        $command .= sprintf(
            ' nexthop via %s dev %s weight %d',
            escapeshellarg($hop['gwip']),
            escapeshellarg($hop['dev']),
            $hop['weight']
        );
    }

    return $command;
}

$args = array_slice($argv, 1);
$GLOBALS['dry_run'] = in_array('--dry-run', $args, true);
$auto = in_array('--auto', $args, true);
$args = array_values(array_filter($args, fn ($a) => $a !== '--dry-run' && $a !== '--auto'));
$flush = in_array('--flush', $args, true);

$desired = [];
if (!$flush) {
    if ($auto) {
        $desired = discover_gateways();
    } else {
        if (count($args) % 3 !== 0) {
            fwrite(STDERR, "expected name/gateway/device triplets\n");
            exit(1);
        }
        for ($i = 0; $i < count($args); $i += 3) {
            $before = $desired;
            add_hop($desired, $args[$i], $args[$i + 1], $args[$i + 2]);
            if ($desired === $before) {
                fwrite(STDERR, "skipping invalid gateway\n");
            }
        }
    }
}

/* Remove tables/rules we own that are no longer wanted. Rules and tables are
 * per address family, so both families are reconciled separately. */
foreach (['-4', '-6'] as $fam) {
    foreach (array_keys(existing_marks($fam)) as $mark) {
        if (!isset($desired[$fam][$mark])) {
            remove_mark($fam, $mark);
        }
    }
}

/* Install or refresh the desired set. */
foreach ($desired as $fam => $gateways) {
    foreach ($gateways as $mark => $gw) {
        if (empty($gw['hops'])) {
            continue;
        }
        run(route_command($fam, $mark, $gw['hops']));
        /* (re)create the lookup rule at a fixed priority equal to the mark, in
         * the family of the gateway: an "ip rule" without a family selector is
         * IPv4 only, so an IPv6 gateway got a table no packet ever reached. */
        run(sprintf('/usr/sbin/ip %s rule del priority %d', $fam, $mark));
        /* Match the gateway number only. The intrusion detection writes its
         * own flags in the high bits of the same mark, and an unmasked rule
         * stopped matching as soon as a packet had been through the queue. */
        run(sprintf(
            '/usr/sbin/ip %s rule add priority %d fwmark %d/0xffff lookup %d',
            $fam,
            $mark,
            $mark,
            $mark
        ));
        $vias = [];
        foreach ($gw['hops'] as $hop) {
            $vias[] = sprintf('%s dev %s weight %d', $hop['gwip'], $hop['dev'], $hop['weight']);
        }
        printf(
            "gateway %s -> mark %d, table %d via %s\n",
            $gw['name'],
            $mark,
            $mark,
            implode(', ', $vias)
        );
    }
}

if ($flush || empty($desired)) {
    echo "policy routing tables cleared\n";
}
