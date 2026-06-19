<?php

/*
 * Copyright (C) 2019-2022 Deciso B.V.
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

function vxlan_configure()
{
    return [
        'newwanip' => ['vxlan_configure_do'],
        'vxlan' => ['vxlan_configure_do'],
    ];
}

function vxlan_devices()
{
    $names = [];

    foreach (iterator_to_array((new \OPNsense\Interfaces\VxLan())->vxlan->iterateItems()) as $vxlan) {
        $names["vxlan{$vxlan->deviceId}"] = [
            'descr' => sprintf('vxlan%s %s (VNI %s)', $vxlan->deviceId, $vxlan->vxlanremote, $vxlan->vxlanid),
            'name' => "vxlan{$vxlan->deviceId}",
        ];
    }

    return [[
        'function' => 'vxlan_configure_device',
        'configurable' => true,
        'pattern' => '^vxlan',
        'volatile' => true,
        'type' => 'vxlan',
        'names' => $names,
    ]];
}

function vxlan_configure_do($verbose = false, $device = null)
{
    $cnf = OPNsense\Core\Config::getInstance()->object();
    $interfaces_details = legacy_interfaces_details();
    $configured_devices = array();
    $changed_devices = array();
    $vxlans = iterator_to_array((new \OPNsense\Interfaces\VxLan())->vxlan->iterateItems());

    if (!empty($vxlans)) {
        service_log(sprintf('Configuring VXLAN interface%s...', empty($device) ? 's' : " {$device}"), $verbose);
    }

    $all_addresses = array();
    $known_addresses = array();
    foreach ($interfaces_details as $intf) {
        foreach (['ipv4', 'ipv6'] as $ipproto) {
            if (!empty($intf[$ipproto])) {
                foreach ($intf[$ipproto] as $net) {
                    $known_addresses[] = $net['ipaddr'];
                }
            }
        }
    }

    // (re)configure vxlan devices
    foreach ($vxlans as $vxlan) {
        $device_name = "vxlan{$vxlan->deviceId}";
        $isChanged = false;

        if ($device !== null && $device != $device_name) {
            $configured_devices[] = $device_name;
            continue;
        }

        if (!in_array((string)$vxlan->vxlanlocal, $known_addresses)) {
            // skip when interface address is not assigned (yet)
            continue;
        }

        $configured_devices[] = $device_name;

        /*
         * MurOS: gather the requested settings. A Linux VXLAN bakes all of its
         * parameters into the device at creation time, so changing any of them
         * means recreating the interface (ip link does not allow editing them
         * in place). The "remote" (unicast) and "group" (multicast) defaults
         * are mutually exclusive on Linux, so remote takes precedence.
         */
        $vni = (string)$vxlan->vxlanid;
        $local = (string)$vxlan->vxlanlocal;
        $remote = (string)$vxlan->vxlanremote;
        $group = (string)$vxlan->vxlangroup;
        $dstport = !empty((string)$vxlan->vxlanremoteport) ? (string)$vxlan->vxlanremoteport : '4789';
        if (!empty($remote)) {
            $group = '';
        }
        $parent = '';
        $intfnm = (string)$vxlan->vxlandev;
        if (!empty($intfnm) && !empty($cnf->interfaces->$intfnm)) {
            $parent = (string)$cnf->interfaces->$intfnm->if;
        }

        $desired = [
            'id' => $vni,
            'local' => $local,
            'remote' => $remote,
            'group' => $group,
            'dev' => $parent,
            'dstport' => $dstport,
        ];

        /* read the current settings (if the device exists) to detect changes */
        $current = [];
        if (!empty($interfaces_details[$device_name])) {
            $raw = shell_exec('/usr/sbin/ip -d -j link show ' . escapeshellarg($device_name) . ' 2>/dev/null');
            $info = json_decode((string)$raw, true)[0]['linkinfo']['info_data'] ?? [];
            $current = [
                'id' => isset($info['id']) ? (string)$info['id'] : '',
                'local' => $info['local'] ?? '',
                'remote' => $info['remote'] ?? '',
                'group' => $info['group'] ?? '',
                'dev' => $info['link'] ?? '',
                'dstport' => isset($info['port']) ? (string)$info['port'] : '',
            ];
        }

        if ($current != $desired) {
            /* (re)create the device with the requested parameters */
            mwexecf('/usr/sbin/ip link del dev %s', [$device_name], true);

            $fmt = '/usr/sbin/ip link add %s type vxlan id %s';
            $args = [$device_name, $vni];
            if (!empty($local)) {
                $fmt .= ' local %s';
                $args[] = $local;
            }
            if (!empty($remote)) {
                $fmt .= ' remote %s';
                $args[] = $remote;
            } elseif (!empty($group)) {
                $fmt .= ' group %s';
                $args[] = $group;
            }
            if (!empty($parent)) {
                $fmt .= ' dev %s';
                $args[] = $parent;
            }
            $fmt .= ' dstport %s';
            $args[] = $dstport;

            mwexecf($fmt, $args);
            mwexecf('/usr/sbin/ip link set dev %s up', [$device_name]);
            $changed_devices[] = $device_name;
        }
    }

    // destroy nonexistent interfaces
    foreach ($interfaces_details as $intf => $data) {
        if (strpos($intf, "vxlan") === 0) {
            if (!in_array($intf, $configured_devices)) {
                mwexecf('/usr/sbin/ip link del dev %s', [$intf], true);
            }
        }
    }

    if (!empty($vxlans)) {
        service_log("done.\n", $verbose);
    }

    // configure attached interface when devices have changed
    interfaces_restart_by_device($verbose, $changed_devices);
}

function vxlan_configure_device($device)
{
    vxlan_configure_do(false, $device);
}
