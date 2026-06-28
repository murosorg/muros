<?php

/*
 * Copyright (C) 2026 MurOS
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
 * MurOS high availability data plane.
 *
 * FreeBSD CARP virtual IPs (virtualip/vip mode=carp) and the pfsync state
 * channel have no Linux equivalent, so they are realised with the native
 * Debian tooling:
 *   - keepalived (VRRP) owns the virtual addresses and the master election;
 *   - conntrackd replicates the connection tracking table between peers.
 *
 * The generators below are idempotent: they rewrite the service configuration
 * from config.xml and reload (or stop) the matching systemd unit.
 */

require_once 'config.inc';
require_once 'util.inc';
require_once 'interfaces.inc';

define('VRRP_CONF', '/etc/keepalived/keepalived.conf');
define('VRRP_STATE_DIR', '/var/run/vrrp');
define('CONNTRACKD_CONF', '/etc/conntrackd/conntrackd.conf');
define('KEEPALIVED_BIN', '/usr/sbin/keepalived');

/*
 * CARP prefers the lowest advskew as master; VRRP prefers the highest
 * priority. Invert the skew into the 1..254 VRRP priority range so the
 * existing advskew semantics (0 = master, 100 = backup) keep working.
 */
function vrrp_priority_from_advskew($advskew)
{
    $skew = (int)$advskew;
    if ($skew < 0) {
        $skew = 0;
    }
    if ($skew > 254) {
        $skew = 254;
    }
    $prio = 254 - $skew;
    return $prio < 1 ? 1 : $prio;
}

function vrrp_instance_name($device, $vhid, $family)
{
    return sprintf('vip_%s_%d_v%d', preg_replace('/[^A-Za-z0-9]/', '_', $device), (int)$vhid, $family);
}

function vrrp_maintenance_mode()
{
    global $config;
    return !empty($config['virtualip_carp_maintenancemode']);
}

function vrrp_disabled()
{
    global $config;
    return !empty($config['virtualip_carp_disabled']);
}

function keepalived_running()
{
    return mwexecf('/usr/bin/systemctl --quiet is-active keepalived', [], true) == 0;
}

/*
 * Group the CARP virtual IPs into VRRP instances. Addresses that share an
 * interface, vhid and family belong to the same instance (one VRRP election
 * carrying several addresses).
 */
function vrrp_collect_instances()
{
    $instances = [];

    foreach (config_read_array('virtualip', 'vip', false) as $vip) {
        if (($vip['mode'] ?? '') !== 'carp') {
            continue;
        }
        if (empty($vip['vhid']) || empty($vip['subnet'])) {
            continue;
        }

        $family = is_ipaddrv6($vip['subnet']) ? 6 : 4;
        $device = get_real_interface($vip['interface'], $family === 6 ? 'inet6' : 'inet');
        if (empty($device)) {
            continue;
        }

        $key = $device . '|' . $vip['vhid'] . '|' . $family;
        if (!isset($instances[$key])) {
            $peer = $family === 6 ? ($vip['peer6'] ?? '') : ($vip['peer'] ?? '');
            if ($peer === '224.0.0.18' || $peer === 'ff02::12') {
                $peer = '';
            }
            /* For unicast VRRP (used when the L2 segment filters multicast,
             * common on cloud and Proxmox bridges) pin the source address so
             * adverts always leave from the right local IP. */
            $src = '';
            if ($peer !== '') {
                $src = $family === 6
                    ? (string)get_interface_ipv6($vip['interface'])
                    : (string)get_interface_ip($vip['interface']);
            }
            $instances[$key] = [
                'name' => vrrp_instance_name($device, $vip['vhid'], $family),
                'device' => $device,
                'vhid' => (int)$vip['vhid'],
                'family' => $family,
                'priority' => vrrp_priority_from_advskew($vip['advskew'] ?? 0),
                'advert_int' => !empty($vip['advbase']) ? (int)$vip['advbase'] : 1,
                'password' => isset($vip['password']) ? str_replace(' ', '', $vip['password']) : '',
                'peer' => $peer,
                'src' => $src,
                'addresses' => [],
            ];
        }

        $bits = $vip['subnet_bits'] ?? ($family === 6 ? 128 : 32);
        $instances[$key]['addresses'][] = sprintf('%s/%s dev %s', $vip['subnet'], $bits, $device);
    }

    return $instances;
}

/*
 * Rewrite /etc/keepalived/keepalived.conf and reload the service. When there
 * are no CARP VIPs, HA is disabled or the node is in maintenance mode, stop
 * keepalived so this unit sources no VRRP adverts and the peer takes over.
 */
function vrrp_configure()
{
    global $config;

    if (!is_dir(VRRP_STATE_DIR)) {
        @mkdir(VRRP_STATE_DIR, 0755, true);
    }

    $instances = vrrp_collect_instances();

    if (empty($instances) || vrrp_disabled() || vrrp_maintenance_mode()) {
        @file_put_contents(VRRP_CONF, "# MurOS: no active VRRP instances\n");
        mwexecf('/usr/bin/systemctl stop keepalived', [], true);
        foreach (glob(VRRP_STATE_DIR . '/*.state') ?: [] as $stale) {
            @unlink($stale);
        }
        return;
    }

    if (!is_dir(dirname(VRRP_CONF))) {
        @mkdir(dirname(VRRP_CONF), 0755, true);
    }

    $notify = '/usr/local/sbin/muros-vrrp-notify';
    $nopreempt = !empty($config['hasync']['disablepreempt']);

    /*
     * Service-failure tracking. Each executable dropped into
     * /usr/local/etc/rc.carp_service_status.d by a subsystem reports whether
     * its service is healthy. On FreeBSD a failure bumped the
     * net.inet.carp.demotion sysctl; on Linux we let keepalived run the checks
     * through a track_script and move the instance to FAULT (so a healthy peer
     * takes over) when any of them fails. Only wire the script when at least
     * one check exists, otherwise keepalived would run a periodic no-op.
     */
    $service_checks = array_filter(
        glob('/usr/local/etc/rc.carp_service_status.d/*') ?: [],
        'is_executable'
    );
    $track_services = !empty($service_checks);

    $conf = "# Generated by MurOS (vrrp.inc). Do not edit by hand.\n";
    $conf .= "global_defs {\n";
    $conf .= "    enable_script_security\n";
    $conf .= "    script_user root\n";
    $conf .= "}\n\n";

    if ($track_services) {
        $conf .= "vrrp_script carp_service_status {\n";
        $conf .= "    script \"/usr/local/sbin/carp_service_status_check\"\n";
        $conf .= "    interval 5\n";
        $conf .= "    timeout 10\n";
        $conf .= "    rise 2\n";
        $conf .= "    fall 2\n";
        $conf .= "}\n\n";
    }

    foreach ($instances as $inst) {
        $conf .= sprintf("vrrp_instance %s {\n", $inst['name']);
        $conf .= "    state BACKUP\n";
        $conf .= sprintf("    interface %s\n", $inst['device']);
        $conf .= sprintf("    virtual_router_id %d\n", $inst['vhid']);
        $conf .= sprintf("    priority %d\n", $inst['priority']);
        $conf .= sprintf("    advert_int %d\n", $inst['advert_int']);
        if ($inst['family'] === 6) {
            $conf .= "    native_ipv6\n";
        }
        if ($nopreempt) {
            $conf .= "    nopreempt\n";
        }
        if (!empty($inst['peer'])) {
            if (!empty($inst['src'])) {
                $conf .= sprintf("    unicast_src_ip %s\n", $inst['src']);
            }
            $conf .= "    unicast_peer {\n";
            $conf .= sprintf("        %s\n", $inst['peer']);
            $conf .= "    }\n";
        }
        /* Re-broadcast the VIP ownership periodically so downstream switches
         * and hosts keep a fresh ARP/ND entry; this shortens the blackout
         * window after a failover on flaky layer-2 segments. */
        $conf .= "    garp_master_refresh 60\n";
        $conf .= "    garp_master_delay 5\n";
        if ($inst['password'] !== '') {
            $conf .= "    authentication {\n";
            $conf .= "        auth_type PASS\n";
            $conf .= sprintf("        auth_pass %s\n", substr($inst['password'], 0, 8));
            $conf .= "    }\n";
        }
        $conf .= "    virtual_ipaddress {\n";
        foreach ($inst['addresses'] as $addr) {
            $conf .= sprintf("        %s\n", $addr);
        }
        $conf .= "    }\n";
        if ($track_services) {
            $conf .= "    track_script {\n";
            $conf .= "        carp_service_status\n";
            $conf .= "    }\n";
        }
        $conf .= sprintf("    notify %s\n", $notify);
        $conf .= "}\n\n";
    }

    /*
     * Group instances that share a priority into a vrrp_sync_group so every
     * virtual IP a node should own fails over together. Without this each
     * instance transitions on its own, so a single interface or service
     * failure could move one VIP to the peer while the rest stay put, leaving
     * the cluster split across both nodes with asymmetric routing. Instances
     * carrying a different priority (a deliberate active/active split through
     * advskew) stay in their own group, so that intent is preserved.
     */
    $groups = [];
    foreach ($instances as $inst) {
        $groups[$inst['priority']][] = $inst['name'];
    }
    $gidx = 0;
    foreach ($groups as $members) {
        if (count($members) < 2) {
            continue;
        }
        $gidx++;
        $conf .= sprintf("vrrp_sync_group muros_ha_%d {\n", $gidx);
        $conf .= "    group {\n";
        foreach ($members as $member) {
            $conf .= sprintf("        %s\n", $member);
        }
        $conf .= "    }\n";
        $conf .= "}\n\n";
    }

    /*
     * Drop state files left over from instances that no longer exist (a VIP
     * was removed or its interface/vhid changed), otherwise the UI keeps
     * reporting a phantom instance forever.
     */
    $live = [];
    foreach ($instances as $inst) {
        $live[$inst['name']] = true;
    }
    foreach (glob(VRRP_STATE_DIR . '/*.state') ?: [] as $stale) {
        if (empty($live[basename($stale, '.state')])) {
            @unlink($stale);
        }
    }

    /*
     * Validate before installing. A malformed keepalived.conf would take HA
     * down on reload, so mirror the firewall's "nft -c" safety net: test the
     * candidate with "keepalived -t" and keep the running configuration when
     * it does not pass.
     */
    $candidate = VRRP_CONF . '.candidate';
    file_put_contents($candidate, $conf);
    $out = [];
    $rc = 0;
    exec(KEEPALIVED_BIN . ' -t -f ' . escapeshellarg($candidate) . ' 2>&1', $out, $rc);
    if ($rc !== 0) {
        @unlink($candidate);
        log_error('vrrp: refusing to apply an invalid keepalived configuration: '
            . trim(implode('; ', $out)));
        return;
    }
    rename($candidate, VRRP_CONF);

    if (keepalived_running()) {
        mwexecf('/usr/bin/systemctl reload keepalived', [], true);
    } else {
        mwexecf('/usr/bin/systemctl restart keepalived', [], true);
    }
}

/*
 * Report the VRRP state of every instance. keepalived's notify hook writes the
 * current role (MASTER/BACKUP/FAULT) to VRRP_STATE_DIR/<instance>.state.
 */
function vrrp_states()
{
    $states = [];
    foreach (glob(VRRP_STATE_DIR . '/*.state') ?: [] as $file) {
        $name = basename($file, '.state');
        $states[$name] = strtoupper(trim((string)@file_get_contents($file)));
    }
    return $states;
}

/*
 * Rewrite /etc/conntrackd/conntrackd.conf from the HA sync settings and reload
 * the service. The dedicated sync interface (hasync/pfsyncinterface) carries a
 * multicast FT-FW channel; without it conntrackd is stopped.
 */
function conntrackd_configure()
{
    global $config;

    $hasync = $config['hasync'] ?? [];
    $syncif = '';
    if (!empty($hasync['pfsyncinterface'])) {
        $syncif = $hasync['pfsyncinterface'];
    }

    $localip = !empty($syncif) ? get_interface_ip($syncif) : null;

    if (empty($syncif) || empty($localip)) {
        mwexecf('/usr/bin/systemctl stop conntrackd', [], true);
        return;
    }

    $device = get_real_interface($syncif);

    $conf = "# Generated by MurOS (vrrp.inc). Do not edit by hand.\n";
    $conf .= "Sync {\n";
    $conf .= "    Mode FTFW {\n";
    $conf .= "        DisableExternalCache off\n";
    $conf .= "    }\n";
    $conf .= "    Multicast {\n";
    $conf .= "        IPv4_address 225.0.0.50\n";
    $conf .= "        Group 3780\n";
    $conf .= sprintf("        IPv4_interface %s\n", $localip);
    $conf .= sprintf("        Interface %s\n", $device);
    $conf .= "        SndSocketBuffer 1249280\n";
    $conf .= "        RcvSocketBuffer 1249280\n";
    $conf .= "        Checksum on\n";
    $conf .= "    }\n";
    $conf .= "}\n\n";
    $conf .= "General {\n";
    $conf .= "    Nice -20\n";
    $conf .= "    HashSize 32768\n";
    $conf .= "    HashLimit 131072\n";
    $conf .= "    LogFile off\n";
    $conf .= "    Syslog on\n";
    $conf .= "    LockFile /var/lock/conntrack.lock\n";
    $conf .= "    UNIX {\n";
    $conf .= "        Path /var/run/conntrackd.ctl\n";
    $conf .= "    }\n";
    $conf .= "    NetlinkBufferSize 2097152\n";
    $conf .= "    NetlinkBufferSizeMaxGrowth 8388608\n";
    $conf .= "    Filter From Userspace {\n";
    $conf .= "        Address Ignore {\n";
    $conf .= "            IPv4_address 127.0.0.1\n";
    $conf .= sprintf("            IPv4_address %s\n", $localip);
    $conf .= "        }\n";
    $conf .= "    }\n";
    $conf .= "}\n";

    if (!is_dir(dirname(CONNTRACKD_CONF))) {
        @mkdir(dirname(CONNTRACKD_CONF), 0755, true);
    }
    file_put_contents(CONNTRACKD_CONF, $conf);

    mwexecf('/usr/bin/systemctl restart conntrackd', [], true);
}
