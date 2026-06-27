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
require_once('interfaces.inc');
require_once('system.inc');
require_once('util.inc');
require_once('vrrp.inc');

/*
 * MurOS: CARP maintenance/enable/disable is mapped onto the keepalived (VRRP)
 * data plane. Maintenance mode is a persistent config flag honoured by
 * vrrp_configure(): when set, keepalived is stopped so this unit stops
 * advertising and the peer becomes master. Disable/enable stop or rebuild the
 * keepalived and conntrackd services at runtime.
 */

$action = strtolower($argv[1] ?? '');

if ($action == 'maintenance') {
    if (!empty($config['virtualip_carp_maintenancemode'])) {
        unset($config['virtualip_carp_maintenancemode']);
        write_config('Leave CARP maintenance mode');
        vrrp_configure();
        conntrackd_configure();
        echo json_encode(['status' => 'ok', 'action' => 'leave_maintenance']);
    } else {
        $config['virtualip_carp_maintenancemode'] = true;
        write_config('Enter CARP maintenance mode');
        /* vrrp_configure() reads the flag and stops keepalived */
        vrrp_configure();
        echo json_encode(['status' => 'ok', 'action' => 'enter_maintenance']);
    }
} elseif ($action == 'disable') {
    mwexecf('/usr/bin/systemctl stop keepalived', [], true);
    mwexecf('/usr/bin/systemctl stop conntrackd', [], true);
    foreach (config_read_array('virtualip', 'vip', false) as $vip) {
        if (!empty($vip['vhid']) && $vip['mode'] == 'carp') {
            interface_vip_bring_down($vip);
        }
    }
    echo json_encode(['status' => 'ok', 'action' => 'disable']);
} elseif ($action == 'enable') {
    vrrp_configure();
    conntrackd_configure();
    echo json_encode(['status' => 'ok', 'action' => 'enable']);
} else {
    echo json_encode(['status' => 'failed']);
}
