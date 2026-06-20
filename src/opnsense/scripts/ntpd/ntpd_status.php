#!/usr/local/bin/php
<?php

/*
 * Copyright (C) 2014-2025 Deciso B.V.
 * Copyright (C) 2013 Dagorlad
 * Copyright (C) 2012 Jim Pingle <jimp@pfsense.org>
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

require_once('script/load_phalcon.php');

use OPNsense\Core\Config;
use OPNsense\Core\Shell;

$result = ['ntpq_servers' => [], 'gps' => []];

/*
 * Query the chrony daemon and present its sources with the same field names
 * the legacy ntpq based page produced, so the existing view keeps working.
 * chronyc -c returns comma separated records.
 */
function chrony_records($command)
{
    $records = [];
    foreach (Shell::shell_safe('/usr/bin/chronyc -c ' . $command . ' 2> /dev/null', [], true) as $line) {
        $line = trim($line);
        if ($line === '') {
            continue;
        }
        $records[] = explode(',', $line);
    }
    return $records;
}

/* map the chrony source state to the ntpq status symbols used by the view */
$state_map = [
    '*' => '*',
    '+' => '+',
    '-' => '-',
    'x' => 'x',
    '?' => ' ',
    '~' => ' ',
];

/* map the chrony source mode to the ntpq connection type symbols */
$mode_map = [
    '^' => 'u',
    '=' => 's',
    '#' => 'l',
];

/* index the per source statistics by name to recover the jitter (std dev) */
$stats = [];
foreach (chrony_records('sourcestats') as $row) {
    if (count($row) >= 8) {
        $stats[$row[0]] = $row;
    }
}

/* the synced source reference id is only exposed through the tracking record */
$track_refid = '';
$tracking = chrony_records('tracking');
if (!empty($tracking[0]) && count($tracking[0]) >= 2) {
    $track_refid = $tracking[0][0];
}

foreach (chrony_records('sources') as $row) {
    if (count($row) < 10) {
        continue;
    }
    $mode = $row[0];
    $state = $row[1];
    $name = $row[2];

    $server = [];
    $server['status'] = $state_map[$state] ?? ' ';
    $server['server'] = $name;
    $server['refid'] = ($state === '*' && $track_refid !== '') ? $track_refid : '-';
    $server['stratum'] = $row[3];
    $server['type'] = $mode_map[$mode] ?? 'u';
    $server['when'] = $row[6];
    /* chrony reports the poll as a power of two, the view expects seconds */
    $server['poll'] = (string)(1 << max(0, (int)$row[4]));
    $server['reach'] = $row[5];
    /* offset and jitter are converted from seconds to milliseconds like ntpq */
    $server['offset'] = sprintf('%.3f', ((float)$row[7]) * 1000.0);
    $server['jitter'] = isset($stats[$name][7]) ? sprintf('%.3f', ((float)$stats[$name][7]) * 1000.0) : '';

    /* the round trip delay is only available through the authorised ntpdata query */
    $server['delay'] = '';
    if (($mode === '^' || $mode === '=') && preg_match('/^[A-Za-z0-9._:-]+$/', $name)) {
        foreach (Shell::shell_safe('/usr/bin/chronyc ntpdata ' . $name . ' 2> /dev/null', [], true) as $dline) {
            if (preg_match('/Peer delay\s*:\s*([0-9.eE+-]+)\s*seconds/', $dline, $m)) {
                $server['delay'] = sprintf('%.3f', ((float)$m[1]) * 1000.0);
                break;
            }
        }
    }

    $result['ntpq_servers'][] = $server;
}

echo json_encode($result);
