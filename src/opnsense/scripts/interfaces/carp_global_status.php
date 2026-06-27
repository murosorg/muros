#!/usr/local/bin/php
<?php

/*
 * Copyright (C) 2022 Deciso B.V.
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

require_once('config.inc');
require_once('util.inc');
require_once('vrrp.inc');

/*
 * MurOS: report the CARP/VRRP status from the keepalived data plane instead of
 * the FreeBSD net.inet.carp.* sysctls. The GUI keeps using the same fields:
 *   - maintenancemode: persistent config flag (set via carp_set_status.php);
 *   - allow: 1 when VRRP is active (keepalived running) or in maintenance;
 *   - demotion: non-zero only in maintenance mode (240, matching the GUI).
 */

$carpcount = 0;
foreach (config_read_array('virtualip', 'vip', false) as $carp) {
    if ($carp['mode'] == 'carp') {
        $carpcount++;
        break;
    }
}

$maintenance = !empty($config['virtualip_carp_maintenancemode']);
$running = keepalived_running();

$response = [
    'demotion' => $maintenance ? '240' : '0',
    'allow' => ($carpcount == 0 || $maintenance || $running) ? '1' : '0',
    'maintenancemode' => $maintenance,
    'status_msg' => '',
];

if ($carpcount == 0) {
    $response['status_msg'] = gettext("Could not locate any defined CARP interfaces.");
} elseif (empty($maintenance) && !$running) {
    $response['status_msg'] = gettext("VRRP is currently disabled on this unit.");
}

echo json_encode($response);
