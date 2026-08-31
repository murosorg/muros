<?php

/*
 * Copyright (C) 2017-2025 Deciso B.V.
 * Copyright (C) 2004-2007 Scott Ullrich <sullrich@gmail.com>
 * Copyright (C) 2005 Bill Marquette <bill.marquette@gmail.com>
 * Copyright (C) 2006 Peter Allgeyer <allgeyer@web.de>
 * Copyright (C) 2008-2010 Ermal Luçi
 * Copyright (C) 2003-2004 Manuel Kasper <mk@neon1.net>
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

require_once('filter.lib.inc');

/* sort by interface only, retain the original order of rules that apply to
   the same interface */
function filter_rules_sort()
{
    global $config;

    /* mark each rule with the sequence number (to retain the order while sorting) */
    for ($i = 0; isset($config['filter']['rule'][$i]); $i++) {
        $config['filter']['rule'][$i]['seq'] = $i;
    }
    usort($config['filter']['rule'], function ($a, $b) {
        if (isset($a['floating']) && isset($b['floating'])) {
            return $a['seq'] - $b['seq'];
        } elseif (isset($a['floating'])) {
            return -1;
        } elseif (isset($b['floating'])) {
            return 1;
        } elseif ($a['interface'] == $b['interface']) {
            return $a['seq'] - $b['seq'];
        } elseif ($a['interface'] == 'wan') {
            return -1;
        } elseif ($b['interface'] == 'wan') {
            return 1;
        } elseif ($a['interface'] == 'lan') {
            return -1;
        } elseif ($b['interface'] == 'lan') {
            return 1;
        } else {
            return strnatcmp($a['interface'], $b['interface']);
        }
    });
    /* strip the sequence numbers again, add uuid's where not available */
    for ($i = 0; isset($config['filter']['rule'][$i]); $i++) {
        unset($config['filter']['rule'][$i]['seq']);
        if (
            empty($config['filter']['rule'][$i]['@attributes'])
            && empty($config['filter']['rule'][$i]['@attributes']['uuid'])
        ) {
            $config['filter']['rule'][$i]['@attributes'] = ['uuid' => generate_uuid()];
        }
    }
}

function filter_configure()
{
    /*
     * Defer this to configd which will avoid this call on bootup when
     * this should not be triggered.  The reason is that rc.bootup calls
     * filter_configure_sync() directly which does this too.
     */
    configd_run('filter reload');
}

/**
 * sync interface groups, but leave the ones not managed by us intact.
 *
 * MurOS: FreeBSD had interface groups in the kernel, ifconfig attached a device
 * to as many of them as needed and pf matched the group name as if it were an
 * interface. Linux has device groups too, but a netdev belongs to exactly one
 * of them, which cannot express an interface being in several groups at once,
 * and nothing in the netfilter matchers looks at them anyway. So the membership
 * is kept as state, written here and read back by legacy_interfaces_details(),
 * and the ruleset generator expands a group to the devices of its members when
 * it emits the interface match.
 */
function ifgroup_setup()
{
    global $config;

    $all_ifs = [];

    if (isset($config['ifgroups']['ifgroupentry'])) {
        foreach ($config['ifgroups']['ifgroupentry'] as $group) {
            foreach (preg_split('/[ |,]+/', $group['members']) as $member) {
                if (!empty($config['interfaces'][$member])) {
                    foreach (get_real_interface($member, 'both') as $if) {
                        if (!isset($all_ifs[$if])) {
                            $all_ifs[$if] = [];
                        }
                        if (!in_array($group['ifname'], $all_ifs[$if])) {
                            $all_ifs[$if][] = $group['ifname'];
                        }
                    }
                }
            }
        }
    }

    ksort($all_ifs);

    /* the state is advisory, a stale file only mislabels a device in a status
     * page, so a failed write is logged and ignored rather than fatal */
    if (@file_put_contents(IFGROUP_STATE_FILE, json_encode($all_ifs)) === false) {
        log_error('unable to write the interface group state to ' . IFGROUP_STATE_FILE);
    }
}

function filter_configure_sync($verbose = false, $load_aliases = true)
{
    global $config;

    service_log('Configuring firewall.', $verbose);

    /*
     * Flush alias cache so consumers can find dynamically generated
     * items on their next cycle.  This ensures items like captive
     * portal zones are available after applying them.
     */
    \OPNsense\Firewall\Alias::flushCacheData();

    /* Use filter lock to not allow concurrent filter reloads during this run. */
    $fobj = new \OPNsense\Core\FileObject('/tmp/rules.debug', 'a+', 0600, LOCK_EX);

    /*
     * MurOS (Debian) data plane is nftables, not pf. Every code path that
     * reconfigures the firewall (interface apply, general settings, boot,
     * new WAN IP, NAT, schedules...) funnels through this function, so we
     * delegate ruleset generation and loading to the same nft pipeline used
     * by the GUI "Apply" action (nft_build.php -> nft -c -> nft -f). This
     * keeps a single source of truth for the ruleset and avoids the missing
     * FreeBSD pfctl and pflog tooling. Nothing here writes a pf ruleset
     * anymore, and there is no reader to restart: the log statements of the
     * generated ruleset go to the kernel and read_log.py picks them up from the
     * journal.
     */
    if (isset($config['system']['disablefilter'])) {
        /* firewall disabled: drop the MurOS ruleset entirely. */
        mwexecf('/usr/sbin/nft delete table inet muros');
        service_log("disabled.\n", $verbose);
    } else {
        $apply_failed = mwexecfm('/usr/local/opnsense/scripts/firewall/nft_apply.sh');
        if ($apply_failed) {
            syslog(LOG_ERR, 'There were error(s) loading the nftables ruleset.');
            file_put_contents(
                '/tmp/rules.error',
                gettext('There were error(s) loading the nftables ruleset.')
            );
            service_log("failed.\n", $verbose);
        } else {
            @unlink('/tmp/rules.error');
            service_log("done.\n", $verbose);
        }
    }

    unset($fobj);
    reopenlog();

    if ($load_aliases) {
        configd_run('template reload OPNsense/Filter');
    }
}

/****f* filter/filter_get_time_based_rule_status
 * NAME
 *   filter_get_time_based_rule_status
 * INPUTS
 *   xml schedule block
 * RESULT
 *   true/false - true if the rule should be installed
 ******/
/*
 <schedules>
   <schedule>
     <name>ScheduleMultipleTime</name>
     <descr>main descr</descr>
     <time>
       <position>0,1,2</position>
       <hour>0:0-24:0</hour>
       <desc>time range 2</desc>
     </time>
     <time>
       <position>4,5,6</position>
       <hour>0:0-24:0</hour>
       <desc>time range 1</desc>
     </time>
   </schedule>
 </schedules>
*/
function filter_get_time_based_rule_status($schedule)
{
    /* no schedule? rule should be installed */
    if (empty($schedule)) {
        return true;
    }
    /*
     * iterate through time blocks and determine
     * if the rule should be installed or not.
     */
    foreach ($schedule['timerange'] as $timeday) {
        $matched_time = true; /* keep original behavior, no time set allows the whole day */
        if (!empty($timeday['hour'])) {
            $tmp = explode("-", $timeday['hour']);
            $now = strtotime("now");
            $matched_time = $now >= strtotime($tmp[0]) && $now < strtotime($tmp[1]);
        }
        if ($matched_time) {
            if (!empty($timeday['position'])) {
                $this_weekday = date('w') == 0 ? 7 : date('w');

                foreach (explode(",", $timeday['position']) as $day) {
                    if ($day == $this_weekday) {
                        return true;
                    }
                }
            } else {
                $months = explode(',', $timeday['month'] ?? '');
                $days = explode(',', $timeday['day'] ?? '');
                if (empty($months) || empty($days) || count($days) != count($months)) {
                    /* invalid data */
                    continue;
                }
                $today = date("dm");
                for ($i = 0; $i < count($days); ++$i) {
                    if (sprintf("%02d%02d", $days[$i], $months[$i]) == $today) {
                        return true;
                    }
                }
            }
        }
    }
    return false;
}

/* Return total physical memory in bytes, read from the Linux kernel. */
function filter_physmem_bytes()
{
    $meminfo = @file_get_contents('/proc/meminfo');
    if ($meminfo !== false && preg_match('/^MemTotal:\s+(\d+)\s*kB/m', $meminfo, $m)) {
        return ((int)$m[1]) * 1024;
    }

    return 0;
}

function filter_default_table_entries()
{
    /*
     * Sizing for named sets (aliases): retain a minimum of 1 million entries,
     * scale up with available memory and cap at around 10 million to make use
     * of large GeoIP / threat-feed sets without exhausting memory.
     */
    return (min(10000000, max(1000000, (int)(filter_physmem_bytes() / 1048576) * 1500)));
}

function filter_default_state_size()
{
    /* be cautious and only allocate 10% of system memory to the state table */
    return (int)(filter_physmem_bytes() / 1048576) * 100;
}

function get_protocols()
{
    $protocols = array('any', 'TCP', 'UDP', 'TCP/UDP', 'ICMP', 'ESP', 'AH', 'GRE', 'IGMP', 'PIM', 'OSPF');

    /* IPv6 extension headers are skipped by the packet filter, we cannot police them */
    $ipv6_ext = array('IPV6-ROUTE', 'IPV6-FRAG', 'IPV6-OPTS', 'IPV6-NONXT', 'MOBILITY-HEADER');

    foreach (explode("\n", file_get_contents('/etc/protocols')) as $line) {
        if (substr($line, 0, 1) != "#") {
            $parts = preg_split('/\s+/', $line);
            if (count($parts) >= 4 && $parts[1] > 0) {
                $protocol = trim(strtoupper($parts[0]));
                if (!in_array($protocol, $ipv6_ext) && !in_array($protocol, $protocols)) {
                    $protocols[] = $protocol;
                }
            }
        }
    }
    return $protocols;
}

/**
 * Return array of possible TOS values
 */
function get_tos_values($blank_label = '')
{
    $ret = array(
        '' => $blank_label,
        'lowdelay' => gettext('lowdelay'),
        'critical' => gettext('critical'),
        'inetcontrol' => gettext('inetcontrol'),
        'netcontrol' => gettext('netcontrol'),
        'throughput' => gettext('throughput'),
        'reliability' => gettext('reliability'),
        'ef' => 'EF',
    );

    foreach (array(11, 12, 13, 21, 22, 23, 31, 32, 33, 41 ,42, 43) as $val) {
        $ret["af$val"] = "AF$val";
    }

    foreach (range(0, 7) as $val) {
        $ret["cs$val"] = "CS$val";
    }

    foreach (range(0, 255) as $val) {
        $ret['0x' . dechex($val)] = sprintf('0x%02X', $val);
    }

    return $ret;
}
