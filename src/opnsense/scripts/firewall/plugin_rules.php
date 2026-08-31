#!/usr/bin/php
<?php

/*
 * Copyright (C) 2026 Deciso B.V.
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
 * OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
 * SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
 * INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
 * CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
 * ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
 * POSSIBILITY OF SUCH DAMAGE.
 */

/**
 * simple wrapper to convert legacy rules into usable data for our MVC implementation
 */


/**
 * Dump the firewall rules the plugins register for themselves.
 *
 * Several parts of the system need rules of their own to work at all: the
 * DHCP servers need the requests addressed to them to be let in, IPsec needs
 * IKE and ESP from the remote peer, the captive portal needs name resolution
 * before a client is authenticated. Those rules are not in the configuration,
 * each plugin registers them at build time through plugins_firewall(), and
 * the interface shows them on the rule page as automatically generated.
 *
 * The nftables generator reads the configuration file, not the plugin tree,
 * so it never saw them: the page showed rules that were never applied, and a
 * DHCP server or an IPsec tunnel only worked if the operator had written the
 * rule by hand. This script runs in the framework, collects what the plugins
 * registered and writes it where the generator can read it.
 *
 * Rules coming from the firewall model itself are left out, the generator
 * already reads those straight from the configuration.
 */

require_once('config.inc');
require_once('interfaces.inc');
require_once('util.inc');
require_once('system.inc');
require_once('filter.inc');

$target = $argv[1] ?? '/var/etc/muros/plugin_rules.json';

$rules = [];
try {
    $fw = filter_core_get_initialized_plugin_system();
    plugins_firewall($fw);
    foreach ($fw->iterateFilterRules() as $prio => $rule) {
        $raw = $rule->getRawRule();
        $ref = (string)($raw['#ref'] ?? '');
        if (strpos($ref, 'ui/firewall/filter') === 0) {
            continue;
        }
        $entry = ['priority' => (int)$prio];
        foreach ([
            'interface', 'interfacenot', 'direction', 'ipprotocol', 'protocol', 'from', 'from_not',
            'from_port', 'to', 'to_not', 'to_port', 'type', 'quick', 'log', 'descr', 'label',
            'statetype', 'gateway', 'disabled',
        ] as $field) {
            if (isset($raw[$field]) && $raw[$field] !== '' && $raw[$field] !== null) {
                $entry[$field] = is_bool($raw[$field]) ? ($raw[$field] ? '1' : '0') : (string)$raw[$field];
            }
        }
        $entry['ref'] = $ref;
        $rules[] = $entry;
    }
} catch (Throwable $e) {
    fwrite(STDERR, 'plugin rules: ' . $e->getMessage() . PHP_EOL);
    exit(1);
}

@mkdir(dirname($target), 0755, true);
file_put_contents($target, json_encode($rules, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES) . PHP_EOL);
echo count($rules) . ' plugin rules' . PHP_EOL;
