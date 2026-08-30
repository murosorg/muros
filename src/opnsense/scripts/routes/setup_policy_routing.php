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

/* Read the gateways to provision from the running configuration, as
 * name/address/device triplets. This lets the firewall apply refresh the
 * tables after every ruleset reload without knowing the topology, and keeps
 * the tables in sync when a gateway address or interface changes. Disabled,
 * loopback and inactive gateways are skipped by the model. */
function discover_gateways(): array
{
    require_once 'config.inc';
    require_once 'util.inc';
    require_once 'interfaces.inc';

    $triplets = [];
    foreach ((new \OPNsense\Routing\Gateways())->gatewaysIndexedByName() as $name => $gw) {
        $gwip = (string)($gw['gateway'] ?? '');
        $dev = (string)($gw['if'] ?? '');
        if ((string)$name === '' || $dev === '' || filter_var($gwip, FILTER_VALIDATE_IP) === false) {
            continue;
        }
        $triplets[] = (string)$name;
        $triplets[] = $gwip;
        $triplets[] = $dev;
    }

    return $triplets;
}

$args = array_slice($argv, 1);
$GLOBALS['dry_run'] = in_array('--dry-run', $args, true);
$auto = in_array('--auto', $args, true);
$args = array_values(array_filter($args, fn ($a) => $a !== '--dry-run' && $a !== '--auto'));
$flush = in_array('--flush', $args, true);

$desired = [];
if (!$flush) {
    if ($auto) {
        $args = discover_gateways();
    }
    if (count($args) % 3 !== 0) {
        fwrite(STDERR, "expected name/gateway/device triplets\n");
        exit(1);
    }
    for ($i = 0; $i < count($args); $i += 3) {
        $name = $args[$i];
        $gwip = $args[$i + 1];
        $dev = $args[$i + 2];
        if ($name === '' || filter_var($gwip, FILTER_VALIDATE_IP) === false || $dev === '') {
            fwrite(STDERR, "skipping invalid gateway\n");
            continue;
        }
        $fam = strpos($gwip, ':') !== false ? '-6' : '-4';
        $desired[$fam][gateway_mark($name)] = ['name' => $name, 'gwip' => $gwip, 'dev' => $dev];
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
        run(sprintf(
            '/usr/sbin/ip %s route replace default via %s dev %s table %d',
            $fam,
            escapeshellarg($gw['gwip']),
            escapeshellarg($gw['dev']),
            $mark
        ));
        /* (re)create the lookup rule at a fixed priority equal to the mark, in
         * the family of the gateway: an "ip rule" without a family selector is
         * IPv4 only, so an IPv6 gateway got a table no packet ever reached. */
        run(sprintf('/usr/sbin/ip %s rule del priority %d', $fam, $mark));
        run(sprintf(
            '/usr/sbin/ip %s rule add priority %d fwmark %d lookup %d',
            $fam,
            $mark,
            $mark,
            $mark
        ));
        printf(
            "gateway %s -> mark %d, table %d via %s dev %s\n",
            $gw['name'],
            $mark,
            $mark,
            $gw['gwip'],
            $gw['dev']
        );
    }
}

if ($flush || empty($desired)) {
    echo "policy routing tables cleared\n";
}
