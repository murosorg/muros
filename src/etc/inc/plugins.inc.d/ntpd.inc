<?php

/*
 * Copyright (C) 2016-2024 Franco Fichtner <franco@opnsense.org>
 * Copyright (C) 2004-2007 Scott Ullrich <sullrich@gmail.com>
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

function ntpd_enabled()
{
    global $config;

    return isset($config['system']['timeservers']);
}

function ntpd_services()
{
    global $config;

    $services = [];

    if (!ntpd_enabled()) {
        return $services;
    }

    $pconfig = [];
    $pconfig['name'] = 'ntpd';
    $pconfig['description'] = gettext('Network Time Daemon');
    $pconfig['php']['restart'] = ['ntpd_configure_do'];
    $pconfig['php']['start'] = ['ntpd_configure_do'];
    $pconfig['php']['stop'] = ['ntpd_stop'];

    if (!empty($config['ntpd']['clientmode'])) {
        /* one-shot client sync has no long running daemon to track */
        $pconfig['nocheck'] = true;
    } else {
        $pconfig['pidfile'] = '/run/chrony/chronyd.pid';
    }

    $services[] = $pconfig;

    return $services;
}

function ntpd_syslog()
{
    $logfacilities = [];

    $logfacilities['ntpd'] = ['facility' => ['ntp', 'ntpd', 'ntpdate']];

    return $logfacilities;
}

function ntpd_cron()
{
    global $config;

    $jobs = [];

    if (ntpd_enabled() && !empty($config['ntpd']['clientmode'])) {
        $jobs[]['autocron'] = ['/usr/local/sbin/pluginctl -s ntpd restart', '0', '2'];
    }

    return $jobs;
}
function ntpd_configure()
{
    return [
        'bootup' => ['ntpd_configure_do'],
        'local' => ['ntpd_configure_do'],
        'newwanip' => ['ntpd_configure_do'],
    ];
}

function ntpd_configure_gps($port)
{
    global $config;

    $gps_device = '/dev/gps0';
    $serialport = "/dev/{$port}";

    @unlink($gps_device);

    if (empty($port) || !preg_match('/^(cua|tty)[a-z0-9.,{}]+$/i', $port)) {
        return false;
    } elseif (!file_exists($serialport) || filetype($serialport) != 'char') {
        return false;
    }

    symlink($serialport, $gps_device);

    $gps_init = base64_decode(
        $config['ntpd']['gps']['initcmd'] ??
        'JFBVQlgsNDAsR1NWLDAsMCwwLDAqNTkNCiRQVUJYLDQwLEdMTCwwLDAsMC' .
        'wwKjVDDQokUFVCWCw0MCxaREEsMCwwLDAsMCo0NA0KJFBVQlgsNDAsVlRH' .
        'LDAsMCwwLDAqNUUNCiRQVUJYLDQwLEdTViwwLDAsMCwwKjU5DQokUFVCWC' .
        'w0MCxHU0EsMCwwLDAsMCo0RQ0KJFBVQlgsNDAsR0dBLDAsMCwwLDANCiRQ' .
        'VUJYLDQwLFRYVCwwLDAsMCwwDQokUFVCWCw0MCxSTUMsMCwwLDAsMCo0Ng' .
        '0KJFBVQlgsNDEsMSwwMDA3LDAwMDMsNDgwMCwwDQokUFVCWCw0MCxaREEs' .
        'MSwxLDEsMQ=='
    );

    file_put_contents($serialport, $gps_init);

    /* set the serial line speed so gpsd and manual tools agree with the GPS */
    switch ($config['ntpd']['gps']['speed'] ?? '') {
        case '16':
            $gpsbaud = '9600';
            break;
        case '32':
            $gpsbaud = '19200';
            break;
        case '48':
            $gpsbaud = '38400';
            break;
        case '64':
            $gpsbaud = '57600';
            break;
        case '80':
            $gpsbaud = '115200';
            break;
        default:
            $gpsbaud = '4800';
            break;
    }

    mwexecf('/usr/bin/stty -F %s %s', [$serialport, $gpsbaud], true);

    return true;
}

function ntpd_configure_pps($port)
{
    $pps_device = '/dev/pps0';
    $serialport = "/dev/{$port}";

    @unlink($pps_device);

    if (empty($port) || !preg_match('/^(cua|tty)[a-z0-9.,{}]+$/i', $port)) {
        return false;
    } elseif (!file_exists($serialport) || filetype($serialport) != 'char') {
        return false;
    }

    @symlink($serialport, $pps_device);

    return true;
}

function ntpd_stop()
{
    /* the NTP service maps to the system chrony daemon on Debian */
    mwexecf('/usr/bin/systemctl stop chrony', [], true);
}

function ntpd_configure_do($verbose = false)
{
    global $config;

    if (!ntpd_enabled()) {
        ntpd_stop();
        return;
    }

    service_log('Starting NTP service...', $verbose);

    config_read_array('ntpd');

    $statsdir = '/var/log/chrony';
    @mkdir($statsdir, 0755, true);

    $noselect = isset($config['ntpd']['noselect']) ? explode(' ', $config['ntpd']['noselect']) : [];
    $prefer = isset($config['ntpd']['prefer']) ? explode(' ', $config['ntpd']['prefer']) : [];
    $iburst = isset($config['ntpd']['iburst']) ? explode(' ', $config['ntpd']['iburst']) : [];
    $ispool = isset($config['ntpd']['ispool']) ? explode(' ', $config['ntpd']['ispool']) : [];

    $cfg = [];
    $cfg[] = '#';
    $cfg[] = '# Autogenerated chrony configuration file - do not edit';
    $cfg[] = '#';
    $cfg[] = '';

    $cfg[] = '# Upstream servers';
    foreach (explode(' ', (string)($config['system']['timeservers'] ?? '')) as $ts) {
        if ($ts === '') {
            continue;
        }
        $line = (in_array($ts, $ispool) || preg_match('/\.pool\.ntp\.org$/', $ts)) ? "pool {$ts}" : "server {$ts}";
        if (in_array($ts, $iburst)) {
            $line .= ' iburst';
        }
        $line .= ' maxpoll 9';
        if (in_array($ts, $prefer)) {
            $line .= ' prefer';
        }
        if (in_array($ts, $noselect)) {
            $line .= ' noselect';
        }
        $cfg[] = $line;
    }
    $cfg[] = '';

    if (ntpd_configure_gps($config['ntpd']['gps']['port'] ?? '')) {
        $refid = !empty($config['ntpd']['gps']['refid']) ? $config['ntpd']['gps']['refid'] : 'GPS';
        $line = "refclock SHM 0 refid {$refid} poll 4";
        if (!empty($config['ntpd']['gps']['fudge1'])) {
            $line .= ' offset ' . $config['ntpd']['gps']['fudge1'];
        }
        if (empty($config['ntpd']['gps']['prefer'])) {
            $line .= ' prefer';
        }
        if (!empty($config['ntpd']['gps']['noselect'])) {
            $line .= ' noselect';
        }
        if (!empty($config['ntpd']['gps']['stratum'])) {
            $line .= ' stratum ' . $config['ntpd']['gps']['stratum'];
        }
        $cfg[] = '# GPS reference clock';
        $cfg[] = $line;
        $cfg[] = '';
    }

    if (ntpd_configure_pps($config['ntpd']['pps']['port'] ?? '')) {
        $line = 'refclock PPS /dev/pps0 lock GPS poll 4';
        if (!empty($config['ntpd']['pps']['fudge1'])) {
            $line .= ' offset ' . $config['ntpd']['pps']['fudge1'];
        }
        if (!empty($config['ntpd']['pps']['refid'])) {
            $line .= ' refid ' . $config['ntpd']['pps']['refid'];
        }
        if (!empty($config['ntpd']['pps']['stratum'])) {
            $line .= ' stratum ' . $config['ntpd']['pps']['stratum'];
        }
        $cfg[] = '# PPS reference clock';
        $cfg[] = $line;
        $cfg[] = '';
    }

    $orphan = !empty($config['ntpd']['orphan']) ? (int)$config['ntpd']['orphan'] : 12;
    $cfg[] = '# Fall back to the local clock if all sources become unreachable';
    $cfg[] = "local stratum {$orphan} orphan";
    $cfg[] = '';

    $cfg[] = 'driftfile /var/lib/chrony/chrony.drift';
    $cfg[] = 'rtcsync';
    $cfg[] = 'makestep 1.0 3';

    $logmask = [];
    if (!empty($config['ntpd']['loopstats'])) {
        $logmask[] = 'tracking';
    }
    if (!empty($config['ntpd']['peerstats'])) {
        $logmask[] = 'measurements';
    }
    if (!empty($config['ntpd']['clockstats'])) {
        $logmask[] = 'refclocks';
    }
    if (!empty($logmask)) {
        $cfg[] = "logdir {$statsdir}";
        $cfg[] = 'log ' . implode(' ', $logmask);
    }

    if (!empty($config['ntpd']['leapsec'])) {
        $leapsec = base64_decode($config['ntpd']['leapsec']);
        file_put_contents('/var/lib/chrony/leap-seconds.list', $leapsec);
        $cfg[] = 'leapseclist /var/lib/chrony/leap-seconds.list';
    } else {
        $cfg[] = 'leapseclist /usr/share/zoneinfo/leap-seconds.list';
    }
    $cfg[] = '';

    if (empty($config['ntpd']['limited'])) {
        $cfg[] = 'ratelimit interval 3 burst 8';
    }

    if (!empty($config['ntpd']['interface'])) {
        $ntpifs = [];
        foreach (explode(',', $config['ntpd']['interface']) as $interface) {
            if (!is_ipaddr($interface) && strstr($interface, '_vip') === false) {
                $ntpifs[] = $interface;
            }
        }
        foreach (interfaces_addresses($ntpifs) as $tmpaddr => $info) {
            if (!$info['bind'] || ($info['family'] == 'inet6' && $info['tentative'])) {
                continue;
            }
            $cfg[] = "bindaddress {$tmpaddr}";
        }
    }

    if (empty($config['ntpd']['noserve'])) {
        $cfg[] = 'allow all';
    }

    $cfg[] = 'bindcmdaddress 127.0.0.1';
    $cfg[] = 'bindcmdaddress ::1';
    $cfg[] = '';

    if (!empty($config['ntpd']['custom_options'])) {
        $cfg[] = '# custom options';
        $cfg[] = $config['ntpd']['custom_options'];
    }

    file_put_contents('/etc/chrony/chrony.conf', implode("\n", $cfg) . "\n");

    if (empty($config['ntpd']['clientmode'])) {
        mwexecf('/usr/bin/systemctl restart chrony');
    } else {
        ntpd_stop();
        mwexecfb('/usr/sbin/chronyd -q -f /etc/chrony/chrony.conf');
    }

    service_log("done.\n", $verbose);
}

function ntpd_xmlrpc_sync()
{
    return [[
        'description' => gettext('Network Time'),
        'section' => 'ntpd,system.timeservers',
        'services' => ['ntpd'],
        'id' => 'ntpd',
    ]];
}
