<?php

/*
 * Copyright (c) 2015-2024 Franco Fichtner <franco@opnsense.org>
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

function legacy_interface_error($cmd, $opt = null)
{
    $msg = 'The command `' . $cmd . '\' failed to execute';

    if ($opt !== null) {
        $msg .= " ({$opt})";
    }

    log_msg($msg, LOG_ERR);
}

function legacy_interface_listget($flag = 'all')
{
    /*
     * MurOS: enumerate network devices with iproute2 instead of `ifconfig -l`.
     * Wireless devices are detected through sysfs (the cfg80211 "phy80211" link
     * present under /sys/class/net/<dev>) rather than the FreeBSD
     * net.wlan.devices sysctl. With $flag === 'up' only administratively up
     * devices are returned (the iproute2 "UP" flag), matching `ifconfig -lu`.
     */
    $ifs_wlan = [];
    foreach (glob('/sys/class/net/*/phy80211') as $path) {
        $ifs_wlan[] = basename(dirname($path));
    }

    if ($flag === 'wlan') {
        return $ifs_wlan;
    }

    $ifs = [];
    $out = [];
    exec('/usr/sbin/ip -j link show 2>/dev/null', $out, $ret);
    if ($ret) {
        legacy_interface_error('ip -j link show');
        return $ifs;
    }

    foreach ((json_decode(implode('', $out), true) ?: []) as $link) {
        if (empty($link['ifname'])) {
            continue;
        }
        if ($flag === 'up' && !in_array('UP', $link['flags'] ?? [])) {
            continue;
        }
        $ifs[] = $link['ifname'];
    }

    foreach ($ifs_wlan as $wlan) {
        if (!in_array($wlan, $ifs)) {
            $ifs[] = $wlan;
        }
    }

    return $ifs;
}

function legacy_interface_flags($ifs, $flag, $report_errors = true)
{
    /*
     * MurOS: only the administrative up/down state maps cleanly to iproute2.
     * FreeBSD-only tokens (hardware offload such as tso/lro/txcsum/rxcsum, the
     * stf "link2" flag, bridge member flags) have no ifconfig equivalent here
     * and are ignored: offload tuning is optional and bridge member flags are
     * applied by the Linux bridge configuration path.
     */
    $state = null;
    foreach (preg_split('/\s+/', trim((string)$flag)) as $token) {
        if ($token === 'up' || $token === 'down') {
            $state = $token;
        }
    }

    if ($state === null) {
        return;
    }

    mwexecf('/usr/sbin/ip link set dev %s %s', [$ifs, $state], !$report_errors);
}

function legacy_interface_create($ifs, $name = null)
{
    /*
     * MurOS: cloned device classes are created with iproute2. When $name is
     * given the first argument is the FreeBSD clone class (bridge, lagg, ...);
     * otherwise the class is derived from the device name (e.g. "lagg0" ->
     * "lagg"). Only classes with a direct Linux equivalent are created here.
     * Tunnel-like classes (gre, gif, stf, ipsec) require type-specific
     * parameters and are handled by their dedicated configuration paths, so
     * the requested name is returned unchanged to let callers proceed.
     */
    $dev = !empty($name) ? $name : $ifs;
    $class = !empty($name) ? $ifs : preg_replace('/[0-9].*$/', '', $ifs);

    $linktype = [
        'bridge' => 'bridge',
        'lagg' => 'bond',
    ][$class] ?? null;

    if ($linktype === null) {
        /* unsupported clone class: leave creation to a dedicated path */
        return $dev;
    }

    /* recreate from a clean state so type or option changes take effect */
    mwexecf('/usr/sbin/ip link del dev %s', [$dev], true);

    if (mwexecf('/usr/sbin/ip link add name %s type %s', [$dev, $linktype]) != 0) {
        return null;
    }

    return $dev;
}

function legacy_interface_destroy($ifs)
{
    /*
     * MurOS: tear down a virtual/cloned device with iproute2. Muted because
     * the device may not exist yet (first configuration) or may be a physical
     * NIC that cannot be deleted, neither of which is an error here, matching
     * the FreeBSD behaviour of ignoring `ifconfig destroy` failures.
     */
    mwexecf('/usr/sbin/ip link del dev %s', [$ifs], true);
}

function legacy_interface_setaddress($ifs, $addr, $family = 4)
{
    /*
     * MurOS: add an address (alias) with iproute2. The family is inferred from
     * the address; a bare address implies a host route (/32 or /128), matching
     * the FreeBSD `ifconfig ... alias` default. Muted because re-adding an
     * already present address during an idempotent reconfigure is not fatal.
     */
    $dev = $ifs === 'lo0' ? 'lo' : $ifs;
    mwexecf('/usr/sbin/ip address add %s dev %s', [$addr, $dev], true);
}

function legacy_interface_deladdress($ifs, $addr, $family = 4)
{
    /*
     * MurOS: remove an address with iproute2. Muted because the address may
     * already be gone, which is not an error.
     */
    $dev = $ifs === 'lo0' ? 'lo' : $ifs;
    mwexecf('/usr/sbin/ip address del %s dev %s', [$addr, $dev], true);
}

function legacy_interface_mtu($ifs, $mtu)
{
    /* MurOS: set the device MTU with iproute2. */
    mwexecf('/usr/sbin/ip link set dev %s mtu %s', [$ifs, $mtu]);
}

function legacy_bridge_member($ifs, $member)
{
    /* MurOS: enslave a member device to a Linux bridge ($ifs is the bridge). */
    mwexecf('/usr/sbin/ip link set dev %s master %s', [$member, $ifs]);
}

function legacy_vlan_info($ifs)
{
    /*
     * MurOS: read the current VLAN parameters (parent device, id, protocol) of
     * an existing 802.1q/802.1ad device with iproute2. Returns an associative
     * array (parent, tag, proto) or an empty array when the device is not a
     * VLAN. Used to recreate a device when a property that Linux fixes at
     * creation time (id, protocol) needs to change.
     */
    $out = [];
    exec('/usr/sbin/ip -d -j link show ' . escapeshellarg($ifs) . ' 2>/dev/null', $out, $ret);
    if ($ret != 0) {
        return [];
    }

    $data = json_decode(implode('', $out), true);
    if (empty($data[0]['linkinfo']['info_data']) || ($data[0]['linkinfo']['info_kind'] ?? '') !== 'vlan') {
        return [];
    }

    $info = $data[0]['linkinfo']['info_data'];
    /* iproute2 reports the protocol as 802.1Q / 802.1ad */
    $proto = strtolower($info['protocol'] ?? '802.1q') === '802.1ad' ? '802.1ad' : '802.1q';

    return [
        'parent' => $data[0]['link'] ?? '',
        'tag' => $info['id'] ?? '',
        'proto' => $proto,
    ];
}

function legacy_vlan_tag($ifs, $member, $tag, $pcp, $proto)
{
    /*
     * MurOS: Linux fixes a VLAN device's id and protocol at creation time, so
     * "tagging" maps to (re)creating the device with iproute2. The parent must
     * be administratively up to carry tagged traffic. Mirrors the logic of
     * _interfaces_vlan_configure().
     */
    $proto = $proto === '802.1ad' ? '802.1ad' : '802.1q';

    mwexecf('/usr/sbin/ip link set dev %s up', [$member]);
    mwexecf('/usr/sbin/ip link del dev %s', [$ifs], true);

    if (mwexecf('/usr/sbin/ip link add link %s name %s type vlan id %s protocol ' . $proto, [$member, $ifs, $tag]) != 0) {
        legacy_interface_error("ip link add link {$member} name {$ifs} type vlan id {$tag}");
        return;
    }

    if ((string)$pcp !== '') {
        mwexecf('/usr/sbin/ip link set dev %s type vlan egress-qos-map 0:%s', [$ifs, $pcp]);
    }

    mwexecf('/usr/sbin/ip link set dev %s up', [$ifs]);
}

function legacy_vlan_remove_tag($ifs)
{
    /*
     * MurOS: a Linux VLAN device cannot be re-tagged in place, so removing the
     * tag means deleting the device. The caller recreates it with the new
     * parameters via legacy_vlan_tag(). Muted because the device may already be
     * gone.
     */
    mwexecf('/usr/sbin/ip link del dev %s', [$ifs], true);
}

function legacy_vlan_pcp($ifs, $pcp)
{
    /* MurOS: the PCP (priority) egress map can be altered live with iproute2. */
    mwexecf('/usr/sbin/ip link set dev %s type vlan egress-qos-map 0:%s', [$ifs, $pcp]);
}

function legacy_vlan_proto($ifs, $proto)
{
    /*
     * MurOS: the VLAN protocol is immutable on a Linux device, so changing it
     * requires recreating the device. Read the current parent and id first,
     * then rebuild it with the requested protocol.
     */
    $info = legacy_vlan_info($ifs);
    if (empty($info['parent']) || $info['tag'] === '') {
        legacy_interface_error("legacy_vlan_proto: cannot resolve current VLAN parameters of {$ifs}");
        return;
    }

    legacy_vlan_tag($ifs, $info['parent'], $info['tag'], '', $proto);
}

function legacy_interface_stats($ifs = null)
{
    /* MurOS: per-interface counters from iproute2 (ip -s -j link show),
     * mapped to the human readable keys the GUI expects. */
    $cmd = '/usr/sbin/ip -s -j link show';
    if ($ifs != null) {
        $cmd .= ' dev ' . escapeshellarg($ifs);
    }
    $stats = [];

    exec($cmd . ' 2> /dev/null', $out, $ret);
    $entries = json_decode(implode('', $out), true);
    if (!is_array($entries)) {
        return $ifs != null ? [] : $stats;
    }

    foreach ($entries as $entry) {
        $dev = $entry['ifname'] ?? null;
        if ($dev === null) {
            continue;
        }
        $s64 = $entry['stats64'] ?? [];
        $rx = $s64['rx'] ?? [];
        $tx = $s64['tx'] ?? [];
        $stats[$dev] = [
            'device' => $dev,
            'driver' => '',
            'packets received' => (string)($rx['packets'] ?? 0),
            'input errors' => (string)($rx['errors'] ?? 0),
            'packets transmitted' => (string)($tx['packets'] ?? 0),
            'output errors' => (string)($tx['errors'] ?? 0),
            'collisions' => (string)($tx['collisions'] ?? 0),
            'bytes received' => (string)($rx['bytes'] ?? 0),
            'bytes transmitted' => (string)($tx['bytes'] ?? 0),
            'multicasts received' => (string)($rx['multicast'] ?? 0),
            'input queue drops' => (string)($rx['dropped'] ?? 0),
            'send queue drops' => (string)($tx['dropped'] ?? 0),
        ];
    }

    if ($ifs != null) {
        return $stats[$ifs] ?? (count($stats) ? reset($stats) : []);
    }

    return $stats;
}

/**
 * detect interface capabilities using ifconfig -m
 * @param string|null $intf
 * @return array list of interface specifics indexed by physical interface name
 */
/* MurOS: collect interface details from iproute2 (ip -j -d addr show) and
 * shape them into the structure historically produced by the FreeBSD
 * ifconfig parser, so existing consumers keep working unchanged. */
function _legacy_interfaces_details_iproute2($intf = null)
{
    $result = [];
    $cmd = '/usr/sbin/ip -j -d addr show';
    if (!empty($intf)) {
        $cmd .= ' dev ' . escapeshellarg($intf);
    }

    exec($cmd . ' 2> /dev/null', $out, $ret);
    $entries = json_decode(implode('', $out), true);
    if (!is_array($entries)) {
        return $result;
    }

    $vfaces = interfaces_virtual_patterns();

    foreach ($entries as $entry) {
        $dev = $entry['ifname'] ?? null;
        if ($dev === null) {
            continue;
        }

        $flags = array_map('strtolower', $entry['flags'] ?? []);
        $operstate = $entry['operstate'] ?? 'UNKNOWN';
        if ($operstate === 'UP' && !in_array('up', $flags)) {
            $flags[] = 'up';
        }

        $rec = [
            'flags' => $flags,
            'capabilities' => [],
            'options' => [],
            'macaddr' => !empty($entry['address']) ? $entry['address'] : '00:00:00:00:00:00',
            'macaddr_hw' => $entry['address'] ?? '00:00:00:00:00:00',
            'ipv4' => [],
            'ipv6' => [],
            'supported_media' => [],
            'is_physical' => !count(array_intersect(preg_split('/\d+/', $dev), $vfaces)),
            'device' => $dev,
            'mtu' => (string)($entry['mtu'] ?? ''),
        ];

        /* present a FreeBSD-like link status used by the GUI */
        if (in_array('up', $flags)) {
            $rec['status'] = in_array($operstate, ['DOWN', 'LOWERLAYERDOWN'], true) ? 'no carrier' : 'active';
        } else {
            $rec['status'] = 'no carrier';
        }

        foreach (($entry['addr_info'] ?? []) as $ai) {
            $family = $ai['family'] ?? '';
            if ($family === 'inet') {
                $rec['ipv4'][] = [
                    'ipaddr' => $ai['local'] ?? '',
                    'subnetbits' => (int)($ai['prefixlen'] ?? 32),
                    'tunnel' => false,
                ];
            } elseif ($family === 'inet6') {
                $addr = $ai['local'] ?? '';
                $rec['ipv6'][] = [
                    'autoconf' => !empty($ai['dynamic']),
                    'deprecated' => !empty($ai['deprecated']),
                    'detached' => false,
                    'ipaddr' => $addr,
                    'link-local' => (($ai['scope'] ?? '') === 'link') || (bool)preg_match('/^fe[89ab][0-9a-f]:/i', $addr),
                    'pltime' => (string)($ai['preferred_life_time'] ?? '0'),
                    'tentative' => !empty($ai['tentative']),
                    'tunnel' => false,
                    'vltime' => (string)($ai['valid_life_time'] ?? '0'),
                    'subnetbits' => (int)($ai['prefixlen'] ?? 128),
                ];
            }
        }

        if (!empty($rec['ipv6'])) {
            /* sort link local addresses to the bottom, keep primary on top */
            usort($rec['ipv6'], function ($a, $b) {
                return $a['link-local'] - $b['link-local'];
            });
        }

        /* VLAN details, exposed by `ip -d` under linkinfo */
        if (($entry['linkinfo']['info_kind'] ?? '') === 'vlan') {
            $vd = $entry['linkinfo']['info_data'] ?? [];
            $rec['vlan'] = [
                'tag' => (string)($vd['id'] ?? ''),
                'proto' => $vd['protocol'] ?? '802.1Q',
                'pcp' => '0',
                'parent' => $entry['link'] ?? '',
            ];
        }

        $result[$dev] = $rec;
    }

    return $result;
}

function legacy_interfaces_details($intf = null)
{
    /* MurOS: interface details are collected from iproute2 on Debian. The
     * original FreeBSD ifconfig parser is kept below (inert) for reference. */
    return _legacy_interfaces_details_iproute2($intf);
}

function _legacy_interfaces_details_freebsd($intf = null)
{
    $cmd = '/sbin/ifconfig -Lmv';
    $result = [];

    if (!empty($intf)) {
        $cmd = implode(' ', [$cmd, escapeshellarg($intf)]);
    }

    exec($cmd . ' 2> /dev/null', $ifconfig_data, $ret);

    $vfaces = interfaces_virtual_patterns();
    $current_interface = null;

    foreach ($ifconfig_data as $lineid => $line) {
        $line_parts = explode(' ', $line);
        if (strpos(trim($line), 'flags=') !== false && $line[0] != "\t") {
            $current_interface = explode(':', $line)[0];
            $result[$current_interface] = [];
            $result[$current_interface]["flags"] = [];
            $result[$current_interface]["capabilities"] = [];
            $result[$current_interface]["options"] = [];
            $result[$current_interface]["macaddr"] = "00:00:00:00:00:00";
            $result[$current_interface]["ipv4"] = [];
            $result[$current_interface]["ipv6"] = [];
            $result[$current_interface]["supported_media"] = [];
            $result[$current_interface]["is_physical"] = !count(array_intersect(preg_split('/\d+/', $current_interface), $vfaces));
            $result[$current_interface]["device"] = $current_interface;
            if (preg_match("/ mtu ([0-9]*).*$/", $line, $matches)) {
                $result[$current_interface]["mtu"] = $matches[1];
            }
            if (preg_match("/<(.*)>.*$/", $line, $matches)) {
                $result[$current_interface]["flags"] = explode(",", strtolower($matches[1]));
            }
        } elseif (empty($current_interface)) {
            // skip parsing, no interface found (yet)
            continue;
        } elseif (strpos(trim($line), 'capabilities=') !== false) {
            // parse capabilities
            $capabilities = substr($line, strpos($line, '<') + 1, -1);
            foreach (explode(',', $capabilities) as $capability) {
                $result[$current_interface]["capabilities"][] = strtolower(trim($capability));
            }
        } elseif (strpos(trim($line), 'options=') === 0) {
            // parse options
            $options = substr($line, strpos($line, '<') + 1, -1);
            foreach (explode(',', $options) as $option) {
                $result[$current_interface]["options"][] = strtolower(trim($option));
            }
        } elseif (strpos($line, "\tether ") !== false) {
            // current mac address
            $result[$current_interface]["macaddr"] = $line_parts[1];
            // fill original as well if not found
            if (!isset($result[$current_interface]['macaddr_hw'])) {
                $result[$current_interface]['macaddr_hw'] = $line_parts[1];
            }
        } elseif (strpos($line, "\thwaddr ") !== false) {
            // original mac address
            $result[$current_interface]['macaddr_hw'] = $line_parts[1];
        } elseif (strpos($line, "\tinet ") !== false) {
            // IPv4 information
            unset($mask);
            unset($vhid);
            for ($i = 0; $i < count($line_parts); ++$i) {
                if ($line_parts[$i] == 'netmask') {
                    /* look-ahead due to keyword match */
                    $mask = substr_count(base_convert(hexdec($line_parts[$i + 1]), 10, 2), '1');
                    ++$i;
                } elseif ($line_parts[$i] == 'vhid') {
                    /* look-ahead due to keyword match */
                    $vhid = $line_parts[$i + 1];
                    ++$i;
                }
            }
            if (isset($mask)) {
                $tmp = ['ipaddr' => $line_parts[1], 'subnetbits' => $mask, 'tunnel' => false];
                if ($line_parts[2] == '-->') {
                    $tmp['tunnel'] = true;
                    $tmp['endpoint'] = $line_parts[3];
                }
                if (isset($vhid)) {
                    $tmp['vhid'] = $vhid;
                }
                $result[$current_interface]['ipv4'][] = $tmp;
            }
        } elseif (strpos($line, "\tinet6 ") !== false) {
            // IPv6 information
            $addr = strtok($line_parts[1], '%');
            $tmp = [
                'autoconf' => false,
                'deprecated' => false,
                'detached' => false,
                'ipaddr' => $addr,
                'link-local' => !!preg_match('/^fe[89ab][0-9a-f]:/i', $addr),
                'pltime' => '0',
                'tentative' => false,
                'tunnel' => false,
                'vltime' => '0',
            ];
            for ($i = 0; $i < count($line_parts); ++$i) {
                if ($line_parts[$i] == 'prefixlen') {
                    /* look-ahead due to keyword match */
                    $tmp['subnetbits'] = intval($line_parts[$i + 1]);
                    ++$i;
                } elseif ($line_parts[$i] == 'vhid') {
                    /* look-ahead due to keyword match */
                    $tmp['vhid'] = $line_parts[$i + 1];
                    ++$i;
                } elseif ($line_parts[$i] == '-->') {
                    /* look-ahead due to keyword match */
                    $tmp['endpoint'] = $line_parts[$i + 1];
                    $tmp['tunnel'] = true;
                    ++$i;
                } elseif ($line_parts[$i] == 'autoconf') {
                    $tmp['autoconf'] = true;
                } elseif ($line_parts[$i] == 'deprecated') {
                    $tmp['deprecated'] = true;
                } elseif ($line_parts[$i] == 'detached') {
                    $tmp['detached'] = true;
                } elseif ($line_parts[$i] == 'tentative') {
                    $tmp['tentative'] = true;
                } elseif ($line_parts[$i] == 'pltime') {
                    $tmp['pltime'] = $line_parts[$i + 1];
                } elseif ($line_parts[$i] == 'vltime') {
                    $tmp['vltime'] = $line_parts[$i + 1];
                }
            }
            if (isset($tmp['subnetbits'])) {
                $result[$current_interface]['ipv6'][] = $tmp;
                // sort link local to bottom, leave rest of sorting as-is (primary address on top)
                usort($result[$current_interface]['ipv6'], function ($a, $b) {
                    return $a['link-local'] - $b['link-local'];
                });
            }
        } elseif (strpos($line, "\ttunnel ") !== false) {
            // extract tunnel proto, source and destination
            $result[$current_interface]["tunnel"] = [];
            $result[$current_interface]["tunnel"]["proto"] = $line_parts[1];
            $result[$current_interface]["tunnel"]["src_addr"] = $line_parts[2];
            $result[$current_interface]["tunnel"]["dest_addr"] = $line_parts[4];
        } elseif (preg_match("/media: (.*)/", $line, $matches)) {
            // media, when link is between parenthesis grep only the link part
            $result[$current_interface]['media'] = $matches[1];
            if (preg_match("/media: .*? \((.*?)\)/", $line, $matches)) {
                $result[$current_interface]['media'] = $matches[1];
            }
            $result[$current_interface]['media_raw'] = substr(trim($line), 7);
        } elseif (preg_match("/media (.*)/", $line, $matches)) {
            $result[$current_interface]["supported_media"][] = str_replace(" mediaopt ", " ", trim($matches[1]));
        } elseif (preg_match("/plugged: (.*)/", $line, $matches)) {
            $result[$current_interface]['sfp'] = [
                'plugged' => $matches[1]
            ];
        } elseif (
            isset($result[$current_interface]['sfp']) && /* safety precaution, plugged should be in the previous line */
            preg_match("/vendor:\s+(.*)\s+PN:(.*)\s+SN:\s+(.*)\s+DATE:\s+(.*)/", $line, $matches)
        ) {
            $result[$current_interface]['sfp']['vendor'] = $matches[1];
            $result[$current_interface]['sfp']['part_number'] = $matches[2];
            $result[$current_interface]['sfp']['serial_number'] = $matches[3];
            $result[$current_interface]['sfp']['manufacturing_date'] = $matches[4];
        } elseif (
            isset($result[$current_interface]['sfp']) &&
            preg_match("/module temperature:\s+(.*)\s+voltage:\s+(.*)Volts/", $line, $matches)
        ) {
            $result[$current_interface]['sfp']['temperature'] = $matches[1];
            $result[$current_interface]['sfp']['voltage'] = $matches[2];
        } elseif (
            isset($result[$current_interface]['sfp']) &&
            preg_match("/lane\s+(.*):\s+RX power:\s+(.*)\s+TX bias:\s+(.*)/", $line, $matches)
        ) {
            $result[$current_interface]['sfp'][sprintf('lane_%s_rx_power', $matches[1])] = $matches[2];
            $result[$current_interface]['sfp'][sprintf('lane_%s_tx_bias', $matches[1])] = $matches[3];
        } elseif (preg_match("/status: (.*)$/", $line, $matches)) {
            $result[$current_interface]['status'] = $matches[1];
        } elseif (preg_match("/channel (\S*)/", $line, $matches)) {
            $result[$current_interface]['channel'] = $matches[1];
        } elseif (preg_match("/ssid (\".*?\"|\S*)/", $line, $matches)) {
            $result[$current_interface]['ssid'] = $matches[1];
        } elseif (preg_match("/laggproto (\S+) lagghash (\S+)$/", $line, $matches)) {
            $result[$current_interface]['laggproto'] = $matches[1];
            $result[$current_interface]['lagghash'] = $matches[2];
        } elseif (preg_match("/laggproto (.*)$/", $line, $matches)) {
            $result[$current_interface]['laggproto'] = $matches[1];
        } elseif (preg_match("/lagg options:(.*)$/", $line, $matches)) {
            $next_line = $ifconfig_data[$lineid + 1];
            $result[$current_interface]['laggoptions'] = ['flags' => explode(
                ',',
                strtolower(substr($next_line, strpos($next_line, '<') + 1, -1))
            )];
            $next_line = $ifconfig_data[$lineid + 2];
            $result[$current_interface]['laggoptions']['flowid_shift'] = trim(explode(":", $next_line)[1]);
            $next_line = $ifconfig_data[$lineid + 3];
            if (strpos($next_line, "rr_limit:") !== false) {
                $result[$current_interface]['laggoptions']['rr_limit'] = trim(explode(":", $next_line)[1]);
            }
        } elseif (preg_match("/lagg statistics:(.*)$/", $line, $matches)) {
            $result[$current_interface]['laggstatistics'] = [];
            $next_line = $ifconfig_data[$lineid + 1];
            $result[$current_interface]['laggstatistics']['active ports'] = trim(explode(":", $next_line)[1]);
            $next_line = $ifconfig_data[$lineid + 2];
            $result[$current_interface]['laggstatistics']['flapping'] = trim(explode(":", $next_line)[1]);
        } elseif (
            preg_match("/laggport: (.*)\Wflags=\d+<(.*)> state=\d+<(.*)>$/", $line, $matches) ||
            preg_match("/laggport: (.*)\Wflags=\d+<(.*)>.*/", $line, $matches)
        ) {
            if (empty($result[$current_interface]['laggport'])) {
                $result[$current_interface]['laggport'] = [];
            }
            $result[$current_interface]['laggport'][trim($matches[1])] = [
                "flags" => explode(",", strtolower($matches[2])),
                "state" => isset($matches[3]) ? explode(",", strtolower($matches[3])) : [],
            ];
        } elseif (strpos($line, "\tgroups: ") !== false) {
            array_shift($line_parts);
            $result[$current_interface]['groups'] = $line_parts;
        } elseif (preg_match("/vlan: (.*)\Wvlanproto:\W(.*) vlanpcp:\W(.*) parent interface:\W(.*)$/", $line, $matches)) {
            $result[$current_interface]['vlan'] = [
                "tag" => $matches[1],
                "proto" => $matches[2],
                "pcp" => $matches[3],
                "parent" => $matches[4],
            ];
        } elseif (strpos($line, "\tcarp: ") !== false) {
            if (empty($result[$current_interface]["carp"])) {
                $result[$current_interface]["carp"] = [];
            }
            $result[$current_interface]["carp"][$line_parts[3]] = [
                "status" => $line_parts[1],
                "vhid" => $line_parts[3],
                "advbase" => $line_parts[5],
                "advskew" => $line_parts[7],
                "peer" => null,
                "peer6" => null,
            ];
            if (isset($ifconfig_data[$lineid + 1]) && strpos($ifconfig_data[$lineid + 1], "peer6") !== false) {
                preg_match("/\Wpeer (.*)\Wpeer6 (.*)$/", $ifconfig_data[$lineid + 1], $matches);
                $result[$current_interface]["carp"][$line_parts[3]]['peer'] = $matches[1];
                $result[$current_interface]["carp"][$line_parts[3]]['peer6'] = $matches[2];
            }
        } elseif (strpos($line, "\tvxlan") !== false) {
            if (empty($result[$current_interface]["vxlan"])) {
                $result[$current_interface]["vxlan"] = [];
            }
            $result[$current_interface]["vxlan"]["vni"] = $line_parts[2];
            $result[$current_interface]["vxlan"]["local"] = $line_parts[4];
            if ($line_parts[5] == "group") {
                $result[$current_interface]["vxlan"]["group"] = $line_parts[6];
                $result[$current_interface]['vxlan']['remote'] = null;
            } else {
                $result[$current_interface]['vxlan']['group'] = null;
                $result[$current_interface]["vxlan"]["remote"] = $line_parts[6];
            }
        } elseif (preg_match("/member: (.*)\Wflags=\w+<(.*)>.*/", $line, $matches)) {
            if (empty($result[$current_interface]["members"])) {
                $result[$current_interface]["members"] = [];
            }
            $result[$current_interface]["members"][$matches[1]] = [
                "flags" => explode(",", strtolower($matches[2]))
            ];
        } elseif (preg_match("/\tnd6 options=\w+<(.*)>/", $line, $matches)) {
            $result[$current_interface]["nd6"] = [
                "flags" => explode(",", strtolower($matches[1]))
            ];
        } elseif (preg_match("/\tid ([\w\:]+) priority (\d+) hellotime (\d+) fwddelay (\d+).*/", $line, $matches)) {
            $result[$current_interface]["priority"] = $matches[2];
            $result[$current_interface]["hellotime"] = $matches[3];
            $result[$current_interface]["fwddelay"] = $matches[4];
        } elseif (preg_match("/\tmaxage (\d+) holdcnt (\d+) proto (\w+) maxaddr (\d+) timeout (\d+).*/", $line, $matches)) {
            $result[$current_interface]["maxage"] = $matches[1];
            $result[$current_interface]["holdcnt"] = $matches[2];
            $result[$current_interface]["proto"] = $matches[3];
            $result[$current_interface]["maxaddr"] = $matches[4];
            $result[$current_interface]["timeout"] = $matches[5];
        }
    }

    return $result;
}

/**
 * fetch interface details for one interface
 * @param $intf string interface name
 * @return array list of interface specifics
 */
function legacy_interface_details($intf)
{
    $result = [];

    $details = legacy_interfaces_details($intf);
    if (isset($details[$intf])) {
        $result = $details[$intf];
    }

    return $result;
}

function interfaces_virtual_patterns()
{
    /* list of virtual device type patterns */
    return [
        '_stf',
        '_vlan',
        '_wlan',
        'bridge',
        'carp',
        'enc',
        'gif',
        'gre',
        'ipfw', /* ipfw logging device, not enabled by default */
        'ipsec',
        'l2tp',
        'lagg',
        'lo',
        'ng',
        'ovpnc',
        'ovpns',
        'pflog',
        'pfsync',
        'plip',
        'ppp',
        'pppoe',
        'pptp',
        'qinq',
        'tap',
        'tun',
        'vlan',
        'vxlan',
        'wg',
    ];
}

/*
 *   get_interface_list() - Return a list of all physical interfaces
 *   along with MAC, IPv4 and status.
 *
 *   $only_active = false -- interfaces that are available in the system
 *                  true  -- interfaces that are physically connected
 *
 *   $include_dmesg = false -- skip probing dmesg for more information
 *                    true  -- probe dmesg for more information
 */
function get_interface_list($only_active = false, $include_dmesg = false)
{
    $dmesg_arr = [];
    $iflist = [];

    if ($include_dmesg) {
        exec('/usr/bin/dmesg', $dmesg_arr);
    }

    $vfaces = interfaces_virtual_patterns();

    $ifnames_wlan = legacy_interface_listget('wlan');
    $ifnames_up = legacy_interface_listget('up');
    $ifnames = legacy_interface_listget();

    $interface_data = legacy_interfaces_details();

    if ($only_active) {
        $_ifnames = [];
        $all_stats = legacy_interface_stats();
        foreach ($ifnames as $ifname) {
            $ifinfo = $all_stats[$ifname];
            if (!empty($ifinfo['link state']) && $ifinfo['link state'] == '2') {
                $_ifnames[] = $ifname;
            }
        }
        $ifnames = $_ifnames;
    }

    foreach ($ifnames as $ifname) {
        $tmp_ifnames = preg_split('/\d+/', $ifname);
        if (count(array_intersect($tmp_ifnames, $vfaces))) {
            continue;
        }
        if (in_array($ifname, $ifnames_wlan)) {
            continue;
        }

        $ifdata = !empty($interface_data[$ifname]) ? $interface_data[$ifname] : [];
        $toput = [
          'up' => in_array($ifname, $ifnames_up),
          'ipaddr' => !empty($ifdata['ipv4'][0]['ipaddr']) ? $ifdata['ipv4'][0]['ipaddr'] : null,
          'mac' => !empty($ifdata['macaddr']) ? $ifdata['macaddr'] : null,
          'descr' => '',
        ];

        foreach ($dmesg_arr as $dmesg_line) {
            $descr = [];
            if (preg_match("/\b{$ifname}: <(.*?)>/i", $dmesg_line, $descr) == 1) {
                $toput['descr'] = $descr[1];
                break;
            }
        }

        $iflist[$ifname] = $toput;
    }

    return $iflist;
}
