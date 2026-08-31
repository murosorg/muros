#!/usr/bin/php
<?php

/*
 * Copyright (c) 2026 MurOS
 * All rights reserved.
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions are met:
 *
 * 1. Redistributions of source code must retain the above copyright notice,
 *    this list of conditions and the following disclaimer.
 *
 * 2. Redistributions in binary form must reproduce the above copyright
 *    notice, this list of conditions and the following disclaimer in the
 *    documentation and/or other materials provided with the distribution.
 *
 * THIS SOFTWARE IS PROVIDED ``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES,
 * INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY
 * AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
 * AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY,
 * OR CONSEQUENTIAL DAMAGES HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY.
 */

/*
 * MurOS: drop the connections pinned to a gateway.
 *
 * "pfctl -k gateway" removed the states routed through a gateway, which is what
 * made an established connection move to another uplink when its own went
 * down. Connection tracking holds no gateway, so there was nothing to kill and
 * the configd action was left doing nothing: after a failover the ruleset and
 * the routing tables pointed at the surviving uplink, but every existing flow
 * kept the mark of the dead one and kept being steered into its table.
 *
 * Policy based routing is what ties a flow to a gateway here, through the mark
 * that nft_build.php sets and that setup_policy_routing.php routes on, so the
 * equivalent is to drop the tracked connections carrying that mark. The next
 * packet of the connection is then evaluated again and leaves through whatever
 * gateway is current.
 *
 * A flow pinned to a gateway group carries the mark of the group, not of its
 * members, so the groups holding the named gateway are dropped as well.
 *
 * Accepts gateway names, as the monitor reports them, or gateway addresses.
 */

require_once 'config.inc';
require_once 'util.inc';
require_once 'interfaces.inc';

function gateway_mark(string $name): int
{
    return 1000 + (crc32($name) % 60000);
}

function group_members(array $group): array
{
    $items = $group['item'] ?? [];
    if (!is_array($items)) {
        $items = [$items];
    }

    $members = [];
    foreach ($items as $item) {
        $parts = explode('|', (string)$item);
        if (!empty($parts[0])) {
            $members[] = $parts[0];
        }
    }

    return $members;
}

$requested = [];
foreach (array_slice($argv, 1) as $argument) {
    foreach (preg_split('/[\s,]+/', $argument, -1, PREG_SPLIT_NO_EMPTY) as $token) {
        $requested[] = $token;
    }
}

if (empty($requested)) {
    fwrite(STDERR, "usage: kill_gateway_states.php <gateway|address> ...\n");
    exit(1);
}

$names = [];
foreach ((new \OPNsense\Routing\Gateways())->gatewaysIndexedByName() as $name => $gw) {
    if (in_array((string)$name, $requested, true) || in_array((string)($gw['gateway'] ?? ''), $requested, true)) {
        $names[(string)$name] = true;
    }
}

/* an unknown name is still worth a kill, the monitor may report a gateway that
 * the configuration no longer holds */
foreach ($requested as $token) {
    if (filter_var($token, FILTER_VALIDATE_IP) === false) {
        $names[$token] = true;
    }
}

foreach (config_read_array('gateways', 'gateway_group') as $group) {
    if (empty($group['name'])) {
        continue;
    }
    foreach (group_members($group) as $member) {
        if (isset($names[$member])) {
            $names[(string)$group['name']] = true;
            break;
        }
    }
}

foreach (array_keys($names) as $name) {
    $mark = gateway_mark($name);
    printf("dropping the connections marked %d (%s)\n", $mark, $name);
    mwexecfm('/usr/sbin/conntrack -D -m %s', $mark);
}
