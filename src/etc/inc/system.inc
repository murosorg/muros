<?php

/*
 * Copyright (C) 2016-2025 Franco Fichtner <franco@opnsense.org>
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

function system_powerd_configure($verbose = false)
{
    global $config;

    killbyname('powerd');

    if (!isset($config['system']['powerd_enable'])) {
        return;
    }

    service_log('Starting power daemon...', $verbose);

    $ac_mode = 'hadp';
    if (!empty($config['system']['powerd_ac_mode'])) {
        $ac_mode = $config['system']['powerd_ac_mode'];
    }

    $battery_mode = 'hadp';
    if (!empty($config['system']['powerd_battery_mode'])) {
        $battery_mode = $config['system']['powerd_battery_mode'];
    }

    $normal_mode = 'hadp';
    if (!empty($config['system']['powerd_normal_mode'])) {
        $normal_mode = $config['system']['powerd_normal_mode'];
    }

    mwexecf(
        '/usr/sbin/powerd -b %s -a %s -n %s',
        [$battery_mode, $ac_mode, $normal_mode]
    );

    service_log("done.\n", $verbose);
}

function system_locale_configure($verbose = false)
{
    global $config;

    /*
     * MurOS: on FreeBSD every locale is always available, but a Debian system
     * only carries the locales that have been generated. The web GUI selects
     * its language by calling setlocale(LC_ALL, "<lang>.UTF-8") and binds the
     * gettext catalogs to it (see set_language() in authgui.inc); if that
     * locale was never generated, setlocale() fails and translations as well as
     * locale-aware formatting silently fall back to C. This function makes sure
     * the configured GUI language (plus the en_US base) exist by generating
     * them with localedef when missing. It is idempotent and cheap: an already
     * present locale is skipped.
     */
    $wanted = ['en_US.UTF-8'];
    if (!empty($config['system']['language'])) {
        $wanted[] = $config['system']['language'] . '.UTF-8';
    }
    foreach (config_read_array('system', 'user', false) as $user) {
        if (!empty($user['language'])) {
            $wanted[] = $user['language'] . '.UTF-8';
        }
    }
    $wanted = array_unique($wanted);

    /* canonicalise locale names so "en_US.UTF-8" matches "en_US.utf8" */
    $normalise = function ($name) {
        return preg_replace('/[^a-z0-9]/', '', strtolower($name));
    };

    $present = [];
    foreach (explode("\n", (string)shell_safe('/usr/bin/locale -a 2>/dev/null')) as $line) {
        $line = trim($line);
        if ($line !== '') {
            $present[$normalise($line)] = true;
        }
    }

    $generated = false;
    foreach ($wanted as $locale) {
        if (isset($present[$normalise($locale)])) {
            continue;
        }

        /* "fr_FR.UTF-8" -> source "fr_FR", charmap "UTF-8" */
        $parts = explode('.', $locale, 2);
        $source = $parts[0];
        $charmap = $parts[1] ?? 'UTF-8';

        if (!$generated) {
            service_log('Generating locales...', $verbose);
            $generated = true;
        }

        if (mwexecf('/usr/bin/localedef -i %s -f %s %s', [$source, $charmap, $locale], true) != 0) {
            log_msg(sprintf('system_locale_configure: failed to generate locale %s', $locale), LOG_WARNING);
        }
    }

    if ($generated) {
        service_log("done.\n", $verbose);
    }
}

function system_sysctl_defaults()
{
    global $config;

    /* notes:
     *  - always set 'default' so the backend can use it and the user can see it
     *  - since this is a function the settings could be derived from configuration
     *    values or functions although in reality this is/was very rarely needed.
     *  - 'description' and 'type' can be used to indicate persistence of these values
     *    when in reality these are not available due to kernel modules unloaded or
     *    compile time options unset (like debugging)
     */

    /*
     * MurOS: this table was a list of FreeBSD sysctl OIDs. It has been
     * translated to their Linux (procfs) equivalents. FreeBSD-only tunables
     * with no Linux counterpart (enc(4) ipsec masks, CARP, pf.share_forward,
     * ZFS, NUMA boot flags, driver knobs like hw.ixl/hw.vtnet, ...) were
     * dropped. set_sysctl() filters this list against the keys actually
     * present on the running kernel, so entries that do not exist (for
     * example the IPv6 keys when IPv6 is disabled) are skipped silently.
     */
    return [
        /* routing: this is a router/firewall. Forwarding is also shipped as a
         * sysctl.d drop-in (applied at boot before this runs); kept here so it
         * shows on the GUI Tunables page and can be overridden. */
        'net.ipv4.ip_forward' => [ 'default' => '1', 'description' => 'Enable IPv4 packet forwarding (routing)' ],
        'net.ipv6.conf.all.forwarding' => [ 'default' => '1', 'description' => 'Enable IPv6 packet forwarding (routing)' ],

        /* a router decides routing from its own table, it neither accepts nor
         * sends ICMP redirects (was net.inet.icmp.drop_redirect / ip.redirect) */
        'net.ipv4.conf.all.accept_redirects' => [ 'default' => '0', 'description' => 'Accept ICMP redirects (IPv4)' ],
        'net.ipv4.conf.default.accept_redirects' => [ 'default' => '0' ],
        'net.ipv4.conf.all.secure_redirects' => [ 'default' => '0', 'description' => 'Accept ICMP redirects from listed gateways only (IPv4)' ],
        'net.ipv4.conf.default.secure_redirects' => [ 'default' => '0' ],
        'net.ipv4.conf.all.send_redirects' => [ 'default' => '0', 'description' => 'Send ICMP redirects (IPv4)' ],
        'net.ipv4.conf.default.send_redirects' => [ 'default' => '0' ],
        'net.ipv6.conf.all.accept_redirects' => [ 'default' => '0', 'description' => 'Accept ICMP redirects (IPv6)' ],
        'net.ipv6.conf.default.accept_redirects' => [ 'default' => '0' ],

        /* reject source-routed packets (was net.inet.ip.accept_sourceroute) */
        'net.ipv4.conf.all.accept_source_route' => [ 'default' => '0', 'description' => 'Accept source-routed packets (IPv4)' ],
        'net.ipv4.conf.default.accept_source_route' => [ 'default' => '0' ],
        'net.ipv6.conf.all.accept_source_route' => [ 'default' => '0', 'description' => 'Accept source-routed packets (IPv6)' ],
        'net.ipv6.conf.default.accept_source_route' => [ 'default' => '0' ],

        /* log impossible (martian) source addresses */
        'net.ipv4.conf.all.log_martians' => [ 'default' => '1', 'description' => 'Log packets with impossible source addresses' ],
        'net.ipv4.conf.default.log_martians' => [ 'default' => '1' ],

        /* SYN flood mitigation (was net.inet.tcp.syncookies) */
        'net.ipv4.tcp_syncookies' => [ 'default' => '1', 'description' => 'Enable TCP SYN cookies' ],

        /* ICMP hardening (was net.inet.icmp.*) */
        'net.ipv4.icmp_echo_ignore_broadcasts' => [ 'default' => '1', 'description' => 'Ignore ICMP echo/timestamp sent to broadcast (smurf protection)' ],
        'net.ipv4.icmp_ignore_bogus_error_responses' => [ 'default' => '1', 'description' => 'Ignore bogus ICMP error responses' ],
        'net.ipv4.icmp_errors_use_inbound_ifaddr' => [ 'default' => '1', 'description' => 'Source ICMP errors from the inbound interface address' ],

        /* a firewall does not use IPv6 privacy (temporary) addresses
         * (was net.inet6.ip6.use_tempaddr / prefer_tempaddr) */
        'net.ipv6.conf.all.use_tempaddr' => [ 'default' => '0', 'description' => 'Use IPv6 privacy (temporary) addresses' ],
        'net.ipv6.conf.default.use_tempaddr' => [ 'default' => '0' ],

        /* socket / input queue sizing (was kern.ipc.maxsockbuf and
         * net.inet.ip.intr_queue_maxlen) */
        'net.core.rmem_max' => [ 'default' => '4262144', 'description' => 'Maximum socket receive buffer (bytes)' ],
        'net.core.wmem_max' => [ 'default' => '4262144', 'description' => 'Maximum socket send buffer (bytes)' ],
        'net.core.netdev_max_backlog' => [ 'default' => '1000', 'description' => 'Maximum input packet queue length per CPU' ],
    ];
}

function system_sysctl_get()
{
    $defaults = system_sysctl_defaults();
    $sysctls = [];

    foreach (array_keys($defaults) as $name) {
        /* compile list of required sysctls not necessarily present in config */
        $sysctls[$name] = '';
    }

    foreach (config_read_array('sysctl', 'item', false) as $tunable) {
        /* get the user specified tunables from the configuration */
        $sysctls[$tunable['tunable']] = $tunable['value'];
    }

    foreach ($sysctls as $key => &$value) {
        /* keeping the now deprecated 'default' as a keyword for selecting defaults */
        if ($value != 'default' && $value != '') {
            continue;
        }

        if (!empty($defaults[$key])) {
            $value = $defaults[$key]['default'];
        } else {
            log_msg('warning: ignoring missing default tunable request: ' . $key, LOG_WARNING);
            unset($sysctls[$key]);
        }
    }

    ksort($sysctls);

    return $sysctls;
}

function system_resolvconf_host_routes()
{
    $routes = [];

    foreach (get_nameservers(null, true) as $dnsserver) {
        if (isset($routes[$dnsserver['host']])) {
            log_msg("Duplicated DNS route ignored for {$dnsserver['host']} on {$dnsserver['interface']}", LOG_WARNING);
            continue;
        }

        $routes[$dnsserver['host']] = $dnsserver['gateway'];
    }

    return $routes;
}

function system_resolvconf_generate($verbose = false)
{
    service_log('Generating /etc/resolv.conf...', $verbose);

    $syscfg = config_read_array('system');
    $resolvconf = '';
    $routes = [];
    $search = [];

    $resolvconf .= "# This file was automatically generated by system_resolvconf_generate()\n";
    $resolvconf .= "# If you want to append configuration here use /etc/resolv.conf.local\n";

    /*
     * MurOS: FreeBSD selects the IPv4/IPv6 source/destination preference with
     * ip6addrctl(8). On Debian that policy lives in glibc's /etc/gai.conf
     * (RFC 3484/6724). Preferring IPv4 means raising the precedence of the
     * IPv4-mapped range above native IPv6; the default (no override) keeps the
     * standard IPv6-first behaviour.
     */
    $gaiconf = "# This file was automatically generated by system_resolvconf_generate()\n";
    if (isset($syscfg['prefer_ipv4'])) {
        $gaiconf .= "precedence ::ffff:0:0/96  100\n";
    }
    file_put_contents('/etc/gai.conf', $gaiconf);

    if (!empty($syscfg['domain'])) {
        $resolvconf .= "domain {$syscfg['domain']}\n";
    }

    if (!isset($syscfg['dnslocalhost']) && !empty(service_by_filter(['dns_ports' => '53']))) {
        $resolvconf .= "nameserver 127.0.0.1\n";
    }

    $routes = system_resolvconf_host_routes();

    foreach (array_keys($routes) as $host) {
        $resolvconf .= "nameserver {$host}\n";
    }

    $search = get_searchdomains();

    if (count($search)) {
        $result = $search[0];
        /* resolv.conf search keyword limit is 6 domains, 256 characters */
        foreach (range(2, 6) as $len) {
            if (count($search) < $len) {
                break;
            }
            $temp = implode(' ', array_slice($search, 0, $len));
            if (strlen($temp) >= 256) {
                break;
            }
            $result = $temp;
        }
        $resolvconf .= "search {$result}\n";
    }

    /* append custom user options */
    $resolvconf .= @file_get_contents('/etc/resolv.conf.local');

    file_safe('/etc/resolv.conf', $resolvconf);

    /* setup static routes for DNS servers as configured */
    foreach ($routes as $host => $gateway) {
        if (!empty($gateway)) {
            system_host_route($host, $gateway);
        }
    }

    service_log("done.\n", $verbose);
}

function get_locale_list()
{
    $locales = [];

    /* first one is the default */
    $locales['en_US'] = gettext('English');
    $locales['zh_CN'] = gettext('Chinese (Simplified)');
    $locales['zh_TW'] = gettext('Chinese (Traditional)');
    $locales['cs_CZ'] = gettext('Czech');
    $locales['fr_FR'] = gettext('French');
    $locales['de_DE'] = gettext('German');
    $locales['el_GR'] = gettext('Greek');
    $locales['it_IT'] = gettext('Italian');
    $locales['ja_JP'] = gettext('Japanese');
    $locales['ko_KR'] = gettext('Korean');
    $locales['no_NO'] = gettext('Norwegian');
    $locales['fa_IR'] = gettext('Persian');
    $locales['pl_PL'] = gettext('Polish');
    $locales['pt_BR'] = gettext('Portuguese (Brazil)');
    $locales['pt_PT'] = gettext('Portuguese (Portugal)');
    $locales['ru_RU'] = gettext('Russian');
    $locales['es_ES'] = gettext('Spanish');
    $locales['tr_TR'] = gettext('Turkish');
    $locales['uk_UA'] = gettext('Ukrainian');
    $locales['vi_VN'] = gettext('Vietnamese');

    if (!product::getInstance()->development()) {
        /* below 30% progress or currently unvetted */
        unset($locales['vi_VN']);
    }

    return $locales;
}

function get_zoneinfo()
{
    $zones = timezone_identifiers_list(DateTimeZone::ALL ^ DateTimeZone::UTC);

    $etcs = glob('/usr/share/zoneinfo/Etc/*');
    foreach ($etcs as $etc) {
        $zones[] = ltrim($etc, '/usr/share/zoneinfo/');
    }

    natsort($zones);

    return $zones;
}

function get_searchdomains()
{
    $syscfg = config_read_array('system');
    $master_list = [];
    $search_list = [];

    if (!empty($syscfg['domain'])) {
        $master_list[] = $syscfg['domain'];
    }

    if (!empty($syscfg['dnssearchdomain'])) {
        $dnssds = array_unique(explode(',', $syscfg['dnssearchdomain']));

        foreach ($dnssds as $dnssd) {
            if ($dnssd == '.') {
                /* pass root only but including other manually set domains as is */
                return $dnssds;
            }

            /* add custom search entries after default domain before potential provider entries */
            $master_list[] = $dnssd;
        }
    }

    if (!empty($syscfg['dnsallowoverride'])) {
        /* return domains as required by configuration */
        $list = shell_safe('/usr/local/sbin/ifctl -sl');
        if (!empty($list)) {
            $search_list = explode("\n", $list);
        }
    }

    foreach ($search_list as $fdns) {
        $contents = file($fdns, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES);
        if (is_array($contents)) {
            foreach ($contents as $dns) {
                if (is_fqdn($dns)) {
                    $master_list[] = $dns;
                }
            }
        }
    }

    return array_unique($master_list);
}

function get_nameservers($interface = null, $with_gateway = false)
{
    global $config;

    $gateways = new \OPNsense\Routing\Gateways();
    $syscfg = config_read_array('system');
    $exclude_interfaces = [];
    $master_list = [];
    $dns_lists = [];

    if (!empty($interface)) {
        /* only acquire servers provided for this interface */
        $devices = get_real_interface($interface, 'both');
        $list = shell_safe('/usr/local/sbin/ifctl -4nli %s', reset($devices));
        if (!empty($list)) {
            $dns_lists[] = $list;
        }
        $list = shell_safe('/usr/local/sbin/ifctl -6nli %s', end($devices));
        if (!empty($list)) {
            $dns_lists[] = $list;
        }
    } elseif (!empty($syscfg['dnsallowoverride'])) {
        /* return dynamic servers as required by configuration */
        $list = shell_safe('/usr/local/sbin/ifctl -nl');
        if (!empty($list)) {
            $dns_lists = explode("\n", $list);
        }
    }

    if (!empty($syscfg['dnsallowoverride_exclude'])) {
        /* by design this only works on dynamic servers, not static ones */
        foreach (explode(',', $syscfg['dnsallowoverride_exclude']) as $intf) {
            if (isset($config['interfaces'][$intf])) {
                $devices = get_real_interface($intf, 'both');
                array_push($exclude_interfaces, ...$devices);
            }
        }
    }

    foreach ($dns_lists as $fdns) {
        /* inspect dynamic servers registered in the system files */
        $intf = [];

        /* XXX this is ifctl logic we eventually need to pass down */
        $intf[0] = preg_replace('/(_[^_]+|:.+)$/', '', basename($fdns));
        $intf[1] = preg_replace('/^.+_/', '', basename($fdns));
        $intf[1] = strpos($intf[1], 'v6') === false ? '4' : '6';

        if (in_array($intf[0], $exclude_interfaces)) {
            continue;
        }

        $contents = @file($fdns, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES);
        if (!is_array($contents)) {
            continue;
        }

        if ($with_gateway) {
            /* router file is available for connectivity creating nameserver files */
            $gw = shell_safe('/usr/local/sbin/ifctl -%s -ri %s', [$intf[1], $intf[0]]);
            if (is_linklocal($gw) && strpos($gw, '%') === false) {
                $gw .= "%{$intf[0]}";
            }
        }

        foreach ($contents as $dns) {
            if (empty($dns) || !is_ipaddr($dns)) {
                continue;
            }

            if ($with_gateway) {
                $master_list[] = [
                    'host' => $dns,
                    'gateway' => $gw,
                    'interface' => convert_real_interface_to_friendly_interface_name($intf[0]),
                    'source' => 'interface',
                ];
            } else {
                $master_list[] = $dns;
            }
        }
    }

    for ($dnscounter = 1; $dnscounter < 9; $dnscounter++) {
        /* inspect static servers and optional gateway assignment */
        $dns = $syscfg['dnsserver'][$dnscounter - 1] ?? null;
        if (empty($dns) || !is_ipaddr($dns)) {
            continue;
        }

        $gwkey = "dns{$dnscounter}gw";
        $gateway = null;
        $dnsif = null;

        if (!empty($syscfg[$gwkey]) && $syscfg[$gwkey] != 'none') {
            /* if a gateway is attached figure out the interface and address */
            $dnsif = $gateways->getInterfaceName($syscfg[$gwkey]);
            $dnsgw = $gateways->getAddress($syscfg[$gwkey]);

            if (empty($dnsif) || empty($dnsgw)) {
                /* do not present servers for defunct gateways */
                continue;
            }

            if (!empty($interface) && $interface != $dnsif) {
                /* not looking for this one */
                continue;
            }

            $gateway = $dnsgw;
        } elseif (!empty($interface)) {
            /* discard because not attached to any interface */
            continue;
        }

        if ($with_gateway) {
            $master_list[] = [
                'host' => $dns,
                'gateway' => $gateway,
                'interface' => $dnsif,
                'source' => 'config',
            ];
        } else {
            $master_list[] = $dns;
        }
    }

    if (!$with_gateway) {
        $master_list = array_unique($master_list);
    }

    return $master_list;
}

function system_hosts_generate($verbose = false)
{
    service_log('Generating /etc/hosts...', $verbose);

    $syscfg = config_read_array('system');

    $hosts = "127.0.0.1\tlocalhost\tlocalhost.{$syscfg['domain']}\n";
    $hosts .= "::1\t\tlocalhost\tlocalhost.{$syscfg['domain']}\n";

    $if = get_primary_interface_from_list();
    if (!empty($if)) {
        $cfgip = get_interface_ip($if);
        if (!empty($cfgip)) {
            $hosts .= "{$cfgip}\t{$syscfg['hostname']}\t{$syscfg['hostname']}.{$syscfg['domain']}\n";
        }
        $cfgip = get_interface_ipv6($if);
        if (!empty($cfgip)) {
            $hosts .= "{$cfgip}\t{$syscfg['hostname']}\t{$syscfg['hostname']}.{$syscfg['domain']}\n";
        }
    }

    file_put_contents('/etc/hosts', $hosts);

    service_log("done.\n", $verbose);
}

function system_resolver_configure($verbose = false)
{
    system_resolvconf_generate($verbose);
    system_hosts_generate($verbose);
}

function system_hostname_configure($verbose = false)
{
    service_log('Setting hostname: ', $verbose);

    $syscfg = config_read_array('system');

    $hostname = "{$syscfg['hostname']}.{$syscfg['domain']}";

    mwexecf('/bin/hostname %s', $hostname);

    service_log("{$hostname}\n", $verbose);
}

function system_host_route($host, $gateway)
{
    if (is_ipaddrv4($gateway)) {
        $family = 'inet';
    } elseif (is_ipaddrv6($gateway)) {
        $family = 'inet6';
    } elseif ($gateway === null) {
        $family = is_ipaddrv4($host) ? 'inet' : 'inet6';
    } else {
        log_msg("ROUTING: not a valid host gateway address: '{$gateway}'", LOG_ERR);
        return;
    }

    /*
     * If the gateway is the same as the host we do not touch the route
     * as this is not needed and it may also break the routing table.
     */
    if ($host === $gateway) {
        return;
    }

    /* MurOS: host routes are managed with iproute2 (a bare address is a /32 or /128) */
    $fam = $family == 'inet6' ? '-6' : '-4';

    /* this was an explicit delete */
    if ($gateway === null) {
        mwexecfm("/usr/sbin/ip $fam route del %s", [$host]);
        return;
    }

    /* replace is idempotent and atomically updates a changed next-hop */
    mwexecf("/usr/sbin/ip $fam route replace %s via %s", [$host, $gateway]);
}

function system_interface_route($gw, $routes)
{
    $interface = $gw['interface'];
    $gateway = $gw['gateway'] ?? 'missing';
    $far = !empty($gw['fargw']);
    $device = $gw['if'];
    $family = 'inet';

    if (!is_ipaddrv4($gateway)) {
        log_msg("ROUTING: not a valid {$interface} interface gateway address: '{$gateway}'", LOG_ERR);
        return;
    }

    if (!$far) {
        /* special case tries to turn on far gateway when required for dynamic gateway */
        $dynamicgw = OPNsense\Interface\Autoconf::getRouter($device, 'inet');
        if (!empty($dynamicgw) && $gateway === $dynamicgw) {
            list (, $network) = interfaces_primary_address($interface);
            if (!empty($network) && !ip_in_subnet($gateway, $network)) {
                /*
                 * If we do not fail primary network detection and the local address
                 * is in not the same network as the gateway address set far flag.
                 */
                $far = 1;

                log_msg("ROUTING: treating '{$gateway}' as far gateway for '{$network}'");
            }
        }
    }

    if (!$far) {
        /* nothing to do */
        return;
    }

    foreach ($routes as $route) {
        /* find host routes on the link for the underlying device */
        if ($route['netif'] != $device) {
            continue;
        } elseif ($route['proto'] != 'ipv4') {
            continue;
        } elseif (strpos($route['gateway'], 'link#') !== 0) {
            continue;
        /* XXX may consider "S" as well for manually assigned */
        } elseif (strpos($route['flags'], 'H') === false) {
            continue;
        }

        /* keeping previous, but do not log this noisy operation */
        return;
    }

    log_msg("ROUTING: setting {$family} interface route to {$gateway} via {$device}");

    /*
     * MurOS: an on-link (interface) route to the gateway, used for far/dynamic
     * gateways. "replace" is idempotent so we never need to remove it first.
     */
    $fam = $family == 'inet6' ? '-6' : '-4';
    mwexecf("/usr/sbin/ip $fam route replace %s dev %s", [$gateway, $device]);
}

function system_default_route($gw, $routes)
{
    $interface = $gw['interface'];
    $gateway = $gw['gateway'];
    $device = $gw['if'];

    if (is_ipaddrv4($gateway)) {
        $family = 'inet';
    } elseif (is_ipaddrv6($gateway)) {
        $family = 'inet6';
    } else {
        log_msg("ROUTING: not a valid default gateway address: '{$gateway}'", LOG_ERR);
        return;
    }

    if (is_linklocal($gateway)) {
        /* XXX always inet6 but why not do this elsewhere */
        $gateway .= "%{$device}";
    }

    foreach ($routes as $route) {
        /* find out if the default route matches what we want to set */
        if ($route['proto'] != ($family == 'inet6' ? 'ipv6' : 'ipv4')) {
            continue;
        } elseif ($route['destination'] != 'default') {
            continue;
        } elseif ($route['gateway'] != $gateway) {
            continue;
        }

        log_msg("ROUTING: keeping {$family} default route to {$gateway}");
        return;
    }

    log_msg("ROUTING: setting {$family} default route to {$gateway}");

    /*
     * MurOS: install the default route with iproute2. A link-local IPv6
     * next-hop carries its scope as "address%device"; Linux expresses this as
     * "via <address> dev <device>". "replace" updates any existing default.
     */
    $fam = $family == 'inet6' ? '-6' : '-4';
    if (strpos($gateway, '%') !== false) {
        list($gwaddr, $gwdev) = explode('%', $gateway, 2);
        mwexecf("/usr/sbin/ip $fam route replace default via %s dev %s", [$gwaddr, $gwdev]);
    } else {
        mwexecf("/usr/sbin/ip $fam route replace default via %s", [$gateway]);
    }
}

function system_routing_configure($verbose = false, $interface_map = null, $monitor = true, $family = null)
{
    global $config;

    if (!plugins_argument_map($interface_map)) {
        return;
    }

    log_msg(sprintf('ROUTING: entering configure using %s', empty($interface_map) ? 'defaults' : join(', ', $interface_map)), LOG_DEBUG);

    service_log(sprintf('Setting up routes%s...', empty($interface_map) ? '' : ' for ' . join(', ', $interface_map)), $verbose);

    $ifdetails = legacy_interfaces_details();
    $gateways = new \OPNsense\Routing\Gateways();
    $routes = json_decode(configd_run('interface routes list -n json'), true) ?? [];
    $down_gateways = [];

    foreach ($gateways->gatewaysIndexedByName() as $gateway) {
        /* check if we need to add a required interface route to the gateway (IPv4 only) */
        if ($gateway['ipprotocol'] !== 'inet' || ($family !== null && $family !== 'inet')) {
            continue;
        } elseif (!empty($interface_map) && !in_array($gateway['interface'], $interface_map)) {
            continue;
        }

        if (empty($ifdetails[$gateway['if']]['ipv4'][0])) {
            log_msg("ROUTING: refusing to set interface route on addressless {$gateway['interface']}({$gateway['if']})", LOG_WARNING);
            continue;
        }

        system_interface_route($gateway, $routes);
    }

    if (isset($config['system']['gw_switch_default']) && $monitor !== 'ignore') {
        foreach (return_gateways_status() as $gwname => $gwstatus) {
            /* for clarity: this also matches 'force_down' */
            if (strpos($gwstatus['status'], 'down') !== false) {
                $down_gateways[] = $gwname;
            }
        }
    }

    if (count($down_gateways)) {
        log_msg(sprintf('ROUTING: ignoring down gateways: %s', implode(', ', $down_gateways)), LOG_DEBUG);
    }

    foreach (['inet' => 'ipv4', 'inet6' => 'ipv6'] as $ipproto => $type) {
        if ($family !== null && $family !== $ipproto) {
            continue;
        }

        $gateway = $gateways->getDefaultGW($down_gateways, $ipproto);
        if (empty($gateway['gateway'])) {
            continue;
        }

        if (isset($config['system']['gw_switch_default']) || empty($interface_map) || in_array($gateway['interface'], $interface_map)) {
            if (empty($ifdetails[$gateway['if']][$type][0])) {
                log_msg("ROUTING: refusing to set {$ipproto} gateway on addressless {$gateway['interface']}({$gateway['if']})", LOG_ERR);
                continue;
            }

            log_msg("ROUTING: configuring {$ipproto} default gateway on {$gateway['interface']}", LOG_INFO);

            system_default_route($gateway, $routes);
        }
    }

    $static_routes = get_staticroutes(false);
    if (count($static_routes)) {
        $gateways_arr = $gateways->gatewaysIndexedByName(false, true);
        foreach ($static_routes as $rtent) {
            if (empty($gateways_arr[$rtent['gateway']])) {
                log_msg(sprintf('ROUTING: gateway IP could not be found for %s', $rtent['network']), LOG_WARNING);
                continue;
            }
            $gateway = $gateways_arr[$rtent['gateway']];
            if (!empty($interface_map) && !in_array($gateway['interface'], $interface_map)) {
                continue;
            }

            if (is_subnetv4($rtent['network'])) {
                $ipproto = 'inet';
            } elseif (is_subnetv6($rtent['network'])) {
                $ipproto = 'inet6';
            } else {
                log_msg(sprintf('ROUTING: cannot add static route to "%s"', $rtent['network']), LOG_ERR);
                continue;
            }

            if ($family !== null && $family !== $ipproto) {
                continue;
            }

            /*
             * MurOS: static routes are programmed with iproute2. "replace" is
             * idempotent (it inserts or updates), so the historical pre-delete
             * is only required when removing a disabled route.
             */
            $fam = $ipproto == 'inet6' ? '-6' : '-4';
            $network = $rtent['network'];

            /* blackhole pseudo-gateways (Null4/Null6) drop the matching traffic */
            if (in_array($rtent['gateway'], ['Null4', 'Null6'])) {
                if (!empty($rtent['disabled'])) {
                    mwexecfm("/usr/sbin/ip $fam route del %s", [$network]);
                } else {
                    mwexecf("/usr/sbin/ip $fam route replace blackhole %s", [$network]);
                }
                continue;
            }

            if (!empty($rtent['disabled'])) {
                mwexecfm("/usr/sbin/ip $fam route del %s", [$network]);
                continue;
            }

            $gatewayip = $gateway['gateway'] ?? '';
            $interfacegw = $gateway['if'];

            if (is_ipaddr($gatewayip)) {
                if (!empty($gateway['fargw']) && $gateway['ipprotocol'] != 'inet6') {
                    /* ensure the gateway itself is reachable on-link first */
                    mwexecf("/usr/sbin/ip $fam route replace %s dev %s", [$gatewayip, $interfacegw]);
                }

                if ($ipproto == 'inet' && is_ipaddrv6($gatewayip)) {
                    /* RFC 5549: an IPv4 destination reached through an IPv6 next-hop */
                    mwexecf("/usr/sbin/ip $fam route replace %s via inet6 %s dev %s", [$network, $gatewayip, $interfacegw]);
                } elseif (is_linklocal($gatewayip)) {
                    /* a link-local next-hop needs the outgoing device */
                    $gwaddr = explode('%', $gatewayip)[0];
                    mwexecf("/usr/sbin/ip $fam route replace %s via %s dev %s", [$network, $gwaddr, $interfacegw]);
                } else {
                    mwexecf("/usr/sbin/ip $fam route replace %s via %s", [$network, $gatewayip]);
                }
            } elseif (!empty($interfacegw)) {
                mwexecf("/usr/sbin/ip $fam route replace %s dev %s", [$network, $interfacegw]);
            }
        }
    }

    service_log("done.\n", $verbose);

    if ($monitor === true) {
        /* reload requested monitors only or reload in full */
        $gwnames = !empty($interface_map) ? [] : null;

        if (!empty($interface_map)) {
            foreach ($gateways->gatewaysIndexedByName() as $name => $gateway) {
                if ($family !== null && $family !== $gateway['ipprotocol']) {
                    continue;
                }

                if (in_array($gateway['interface'], $interface_map)) {
                    $gwnames[] = $name;
                }
            }
        }

        plugins_configure('monitor', $verbose, [$gwnames]);
    }
}

function system_syslog_start($verbose = false)
{
    service_log('Configuring system logging...', $verbose);

    configd_run('template reload OPNsense/Syslog');
    mwexecf('/usr/local/opnsense/scripts/syslog/generate_certs.php');

    $last_version = @file_get_contents('/var/run/syslog-ng.version');
    $this_version = shell_safe('syslog-ng -V | sha256sum');

    if (isvalidpid('/var/run/syslog-ng.pid') && $last_version == $this_version) {
        mwexecf('/usr/local/sbin/syslog-ng-ctl reload');
    } else {
        /* full restart: stop the running daemon, then relaunch it. syslog-ng
           daemonizes itself; pin the pid file so the service layer can track it. */
        killbypid('/var/run/syslog-ng.pid');
        mwexecf('/usr/local/sbin/syslog-ng -f %s --pidfile %s', [
            '/usr/local/etc/syslog-ng.conf',
            '/var/run/syslog-ng.pid',
        ]);
    }

    file_put_contents('/var/run/syslog-ng.version', $this_version);

    service_log("done.\n", $verbose);
}

function system_syslog_reset($verbose = false)
{
    $it = new RecursiveDirectoryIterator('/var/log');

    foreach (new RecursiveIteratorIterator($it) as $file) {
        if ($file->isFile() && strpos($file->getFilename(), '.log') > -1) {
            if (strpos($file->getFilename(), 'flowd') === false) {
                @unlink((string)$file);
            }
        }
    }

    system_syslog_start($verbose);
    plugins_configure('dhcp', $verbose);
}

function system_firmware_configure($verbose = false)
{
    service_log('Writing firmware settings:', $verbose);

    $scripts = glob('/usr/local/opnsense/scripts/firmware/repos/*');
    natsort($scripts);

    foreach ($scripts as $script) {
        $basename = basename($script);
        if ($basename == 'README' || strpos($basename, '.pgksave') !== false) {
            continue;
        } elseif (!is_executable($script)) {
            continue;
        }

         /* offer visibility of errors only */
         pass_safe($script . '> /dev/null');

        /* make a note about repo being handled */
        service_log(' ' . preg_replace('/\..*?$/', '', $basename));
    }

    service_log("\n");
}

function system_trust_configure($verbose = false)
{
    service_log('Writing trust files...', $verbose);

    $trust = new \OPNsense\Trust\General();

    /*
     * Write separate files because certctl will ignore the whole file
     * if the first certificate matches.  Unfortunately the behaviour
     * can also be harmful because it overrides the user choice given
     * through our GUI autorities section.
     *
     * 1. Clear all files (a subdirectory is not supported)
     * 2. Write all files
     * 3. Execute rehash to fill /etc/ssl/certs
     * 4. Pull all content from /etc/ssl/certs
     * 5. Write bundle files with all /etc/ssl/certs content
     */

    $ca_files = '/usr/local/share/certs/ca-root-opnsense-%s';

    foreach (glob(sprintf($ca_files, '*')) as $ca_unlink) {
        @unlink($ca_unlink);
    }

    foreach (config_read_array('ca', false) as $i => $entry) {
        if (!empty($entry['crt'])) {
            // Split and cleans ca certificates, one entry could contain multiple certs if a user imported a bundle
            // avoid expired ca's from being considered as valid alternatives.
            $certlist = str_replace("\r", '', base64_decode($entry['crt']));
            $user_cas = [""];
            foreach (explode("\n", $certlist) as $row) {
                $user_cas[count($user_cas) - 1] .= $row . "\n";
                if (strpos($row, '---END') > 0) {
                    $user_cas[] = "";
                }
            }
            $ca = "# OPNsense trust authority: {$entry['descr']}\n";
            $ca_count = 0;
            $include_intermediates = !empty((string)$trust->store_intermediate_certs);
            foreach ($user_cas as $user_ca) {
                if (!empty(trim($user_ca))) {
                    $certinfo = @openssl_x509_parse($user_ca);
                    $certext = !empty($certinfo['extensions']) ? $certinfo['extensions'] : [];
                    $authoritykey = "";
                    if (!empty($certext['authorityKeyIdentifier'])) {
                        $authoritykey = trim(str_replace('keyid:', '', preg_split("/\r\n|\n|\r/", $certext['authorityKeyIdentifier'])[0]));
                    }
                    $subjectkey = $certext['subjectKeyIdentifier'];
                    $is_self_signed = empty($authoritykey) || $subjectkey == $authoritykey;

                    $error_line = "";
                    if (!empty($certinfo['validTo']) && $certinfo['validTo_time_t'] < time()) {
                        $error_line = sprintf(
                            "(system local trust) refusing to import %s from %s (expired @ %s)",
                            $certinfo['name'],
                            $entry['descr'],
                            date('r', $certinfo['validFrom_time_t'])
                        );
                    } elseif (!$include_intermediates && !$is_self_signed) {
                        $error_line = sprintf(
                            "(system local trust) skip intermediate certificate %s from %s",
                            $certinfo['name'],
                            $entry['descr']
                        );
                    }
                    if (!empty($error_line)) {
                        syslog(LOG_NOTICE, $error_line);
                        $ca .= "#" .  str_replace("\n", "", $error_line);
                    } else {
                        $ca .= $user_ca;
                        $ca_count += 1;
                    }
                }
            }

            if ($ca_count) {
                $ca_file = sprintf($ca_files, $i . '.crt');
                \OPNsense\Core\File::file_put_contents(sprintf($ca_file, $i), $ca, 0644);
            }
        }
    }

    if (!empty((string)$trust->install_crls)) {
        /* deploy all collected CRL's into the trust store, they will be hashed into /etc/ssl/certs by certctl eventually */
        foreach (config_read_array('crl', false) as $i => $entry) {
            if (!empty($entry) && !empty($entry['text'])) {
                $crl_file = sprintf($ca_files, $i . '.crl');
                $payload = base64_decode($entry['text']) ?? '';
                \OPNsense\Core\File::file_put_contents(sprintf($crl_file, $i), $payload, 0644);
            }
        }
    }

    service_log("done.\n", $verbose);

    /* collects all trusted certificates into /etc/ssl/certs directory */
    pass_safe('/usr/local/opnsense/scripts/system/certctl.py rehash');

    service_log('Writing trust bundles...', $verbose);

    /* collect all the file content and write it to compatibility bundle locations */
    $ca_bundle = [];
    foreach (glob('/etc/ssl/certs/*.[0-9]') as $file) {
        $ca_bundle[] = file_get_contents($file);
    }
    $ca_bundle = join("\n", $ca_bundle);
    foreach (['/etc/ssl/cert.pem', '/usr/local/openssl/cert.pem'] as $pem) {
        @unlink($pem); /* remove permanently as we use the directory */
    }
    foreach (['/usr/local/etc/ssl/cert.pem'] as $pem) {
        @unlink($pem); /* do not clobber symlink target */
        file_safe($pem, $ca_bundle);
    }

    configd_run('template reload OPNsense/Trust');

    service_log("done.\n", $verbose);
}

function system_timezone_configure($verbose = false)
{
    $syscfg = config_read_array('system');

    service_log('Setting timezone: ', $verbose);

    /* extract appropriate timezone file */
    $timezone = $syscfg['timezone'];
    $timezones = get_zoneinfo();

    /* reset to default if empty or nonexistent */
    if (
        empty($timezone) || !in_array($timezone, $timezones) ||
        !file_exists(sprintf('/usr/share/zoneinfo/%s', $timezone))
    ) {
        $timezone = 'Etc/UTC';
    }

    /* apply timezone */
    if (file_exists(sprintf('/usr/share/zoneinfo/%s', $timezone))) {
        copy(sprintf('/usr/share/zoneinfo/%s', $timezone), '/etc/localtime');
    }

    service_log("{$timezone}\n", $verbose);
}

function system_sysctl_configure($verbose = false)
{
    service_log('Setting up extended sysctls...', $verbose);

    $todo = [];
    $new_values = system_sysctl_get();
    $current_values = get_sysctl(array_keys($new_values));
    foreach ($new_values as $prop => $value) {
        if (isset($current_values[$prop]) && $current_values[$prop] != $value) {
            $todo[$prop] = $value;
        }
    }
    set_sysctl($todo);

    service_log("done.\n", $verbose);
}

function system_kernel_configure($verbose = false)
{
    global $config;

    service_log('Configuring kernel modules...', $verbose);

    /*
     * MurOS: preload the Linux kernel modules backing the features MurOS
     * configures. Most of these autoload on first use (iproute2 pulls in
     * "bridge"/"8021q"/... when a device of that kind is created, and nft pulls
     * in "nf_tables"), but loading them up front keeps behaviour deterministic
     * after a reboot. FreeBSD-only modules (carp, if_enc, pf, pflog, pfsync)
     * have no Linux equivalent and are dropped: packet filtering is handled by
     * nftables (nf_tables) and there is no /dev/pf to fix permissions on.
     */
    $mods = [
        'nf_tables',    /* nftables core (firewall, NAT) */
        'nft_ct',       /* connection tracking matches */
        'nf_conntrack', /* stateful tracking */
        'bridge',       /* if_bridge */
        '8021q',        /* if_vlan */
        'bonding',      /* if_lagg */
        'tun',          /* if_tun / if_tap */
        'ip_gre',       /* if_gre (IPv4) */
        'ip6_gre',      /* if_gre (IPv6) */
        'sit',          /* if_gif (IPv6-in-IPv4) */
        'ip6_tunnel',   /* if_gif (IPv6 tunnelling) */
        'wireguard',    /* native WireGuard */
    ];

    if (!empty($config['system']['crypto_hardware'])) {
        foreach (explode(',', $config['system']['crypto_hardware']) as $crypto) {
            log_msg(sprintf('Loading %s cryptographic accelerator module.', $crypto), LOG_INFO);
            $mods[] = $crypto;
        }
    }

    if (!empty($config['system']['thermal_hardware'])) {
        log_msg(sprintf('Loading %s thermal monitor module.', $config['system']['thermal_hardware']), LOG_INFO);
        $mods[] = $config['system']['thermal_hardware'];
    }

    foreach ($mods as $mod) {
        /* muted: a module compiled into the kernel is unavailable to modprobe but still present */
        mwexecf('/usr/sbin/modprobe %s', [$mod], true);
    }

    service_log("done.\n", $verbose);
}

function system_devd_configure($verbose = false)
{
    /*
     * MurOS: on Debian the device manager is systemd-udevd, which is already
     * started by systemd before rc.bootup runs. There is no FreeBSD devd to
     * launch; just make sure udevd is active (best effort) so device events are
     * processed. The historic post-start sleep is no longer needed.
     */
    service_log('Starting device manager...', $verbose);

    mwexecf('/usr/bin/systemctl start systemd-udevd', [], true);

    service_log("done.\n", $verbose);
}

function system_cron_configure($verbose = false)
{
    function generate_cron_job($command, $minute = '0', $hour = '*', $monthday = '*', $month = '*', $weekday = '*')
    {
        $cron_item = [];

        $cron_item['minute'] = $minute;
        $cron_item['hour'] = $hour;
        $cron_item['mday'] = $monthday;
        $cron_item['month'] = $month;
        $cron_item['wday'] = $weekday;
        $cron_item['command'] = $command;

        return $cron_item;
    }

    $autocron = [];

    service_log('Configuring cron...', $verbose);

    foreach (plugins_cron() as $cron_plugin) {
        /*
         * We are stuffing jobs inside 'autocron' to be able to
         * deprecate this at a later time.  Ideally all of the
         * services should use a single cron-model, which this is
         * not.  At least this plugin function helps us to divide
         * and conquer the code bits...  :)
         */
        if (!empty($cron_plugin['autocron'])) {
            $autocron[] = call_user_func_array('generate_cron_job', $cron_plugin['autocron']);
        }
    }

    $crontab_contents = "# DO NOT EDIT THIS FILE -- OPNsense auto-generated file\n";
    $crontab_contents .= "#\n";
    $crontab_contents .= "# User-defined crontab files can be loaded via /etc/cron.d\n";
    $crontab_contents .= "# or /usr/local/etc/cron.d and follow the same format as\n";
    $crontab_contents .= "# /etc/crontab, see the crontab(5) manual page.\n";
    $crontab_contents .= "SHELL=/bin/sh\n";
    $crontab_contents .= "PATH=/etc:/bin:/sbin:/usr/bin:/usr/sbin:/usr/local/bin:/usr/local/sbin\n";
    $crontab_contents .= "REQUESTS_CA_BUNDLE=/usr/local/etc/ssl/cert.pem\n";
    $crontab_contents .= "#minute\thour\tmday\tmonth\twday\tcommand\n";

    foreach ($autocron as $item) {
        $crontab_contents .= "{$item['minute']}\t";
        $crontab_contents .= "{$item['hour']}\t";
        $crontab_contents .= "{$item['mday']}\t";
        $crontab_contents .= "{$item['month']}\t";
        $crontab_contents .= "{$item['wday']}\t";
        $crontab_contents .= "({$item['command']}) > /dev/null\n";
    }

    /*
     * MurOS: FreeBSD keeps per-user crontabs under /var/cron/tabs and reloads
     * them through cron's rc.d script. Debian's cron reads per-user crontabs
     * from /var/spool/cron/crontabs (no user field, mode 0600, owned by the
     * target user and group crontab) and reloads them through systemd.
     */
    $crontab_root = '/var/spool/cron/crontabs/root';
    file_put_contents($crontab_root, $crontab_contents);
    chmod($crontab_root, 0600);
    @chown($crontab_root, 'root');
    @chgrp($crontab_root, 'crontab');

    configd_run('template reload OPNsense/Cron');

    mwexecf('/usr/bin/systemctl restart cron');

    service_log("done.\n", $verbose);
}

function system_console_types()
{
    return array(
        /* sorted by usage */
        'video' => array('value' => 'vidconsole', 'name' => gettext('VGA Console')),
        'serial' => array('value' => 'comconsole', 'name' => gettext('Serial Console')),
        'efi' => array('value' => 'efi', 'name' => gettext('EFI Console')),
        'null' => array('value' => 'nullconsole', 'name' => gettext('Mute Console')),
    );
}

function system_cache_flush($verbose = false)
{
    service_log('Flushing all caches...', $verbose);

    (new OPNsense\Core\ACL())->invalidateCache();

    (new OPNsense\Base\Menu\MenuSystem())->invalidateCache();

    /* clears ALL model caches, not just model specific ones via flushCacheData() */
    foreach (glob('/var/lib/php/tmp/mdl_cache_*.json') as $filename) {
        @unlink($filename);
    }

    service_log("done.\n", $verbose);
}

function system_login_configure($verbose = false)
{
    global $config;

    service_log('Configuring login behaviour...', $verbose);

    /* depends on user account locking */
    local_sync_accounts();

    $serialspeed = 115200;

    if (!empty($config['system']['serialspeed']) && is_numeric($config['system']['serialspeed'])) {
        $serialspeed = $config['system']['serialspeed'];
    }

    $new_boot_config = array();
    $new_boot_config['comconsole_speed'] = null;
    $new_boot_config['boot_multicons'] = null;
    $new_boot_config['boot_serial'] = null;
    $new_boot_config['kern.vty'] = null;
    $new_boot_config['console'] = null;

    $console_types = system_console_types();
    $console_selection = array();

    foreach (array('primaryconsole', 'secondaryconsole') as $console_order) {
        if (!empty($config['system'][$console_order]) && isset($console_types[$config['system'][$console_order]])) {
            $console_selection[] = $console_types[$config['system'][$console_order]]['value'];
        }
    }

    $console_selection = array_unique($console_selection);

    $output_enabled = count($console_selection) != 1 || !in_array('nullconsole', $console_selection);
    $virtual_enabled = !count($console_selection) || in_array('vidconsole', $console_selection) ||
        in_array('efi', $console_selection);
    $serial_enabled = in_array('comconsole', $console_selection);

    if (count($console_selection)) {
        $new_boot_config['console'] = '"' . implode(',', $console_selection) . '"';
        if (count($console_selection) >= 2) {
            $new_boot_config['boot_multicons'] = '"YES"';
        }
    }

    /*
     * MurOS: console configuration. On FreeBSD this wrote /boot.config,
     * /boot/loader.conf and /etc/ttys and signalled init(8) with SIGHUP.
     * On Debian the console is owned by systemd:
     *   - a serial console is the serial-getty@ttySN template unit, with the
     *     baud rate pinned through a unit drop-in,
     *   - the local video terminal is getty@tty1 (autovt).
     * The kernel "console=" argument lives in GRUB and is intentionally left
     * untouched here: changing it requires update-grub and a reboot, so it is
     * handled at install time / via /etc/default/grub rather than on every
     * settings save (avoiding any risk of cutting console access at runtime).
     */
    $serial_device = 'ttyS0';
    $serial_unit = "serial-getty@{$serial_device}.service";
    $dropin_dir = "/etc/systemd/system/{$serial_unit}.d";

    if ($serial_enabled) {
        /* pin the requested baud rate through a drop-in that overrides the
         * stock ExecStart (which keep-bauds through a list of speeds). */
        @mkdir($dropin_dir, 0755, true);
        $override = sprintf(
            "[Service]\nExecStart=\nExecStart=-/sbin/agetty --keep-baud %s %%I \$TERM\n",
            $serialspeed
        );
        @file_put_contents("{$dropin_dir}/muros-baud.conf", $override);
        mwexecf('/usr/bin/systemctl daemon-reload');
        mwexecf('/usr/bin/systemctl enable %s', [$serial_unit]);
        mwexecf('/usr/bin/systemctl restart %s', [$serial_unit]);
    } else {
        mwexecf('/usr/bin/systemctl disable %s', [$serial_unit], true);
        mwexecf('/usr/bin/systemctl stop %s', [$serial_unit], true);
        @unlink("{$dropin_dir}/muros-baud.conf");
        mwexecf('/usr/bin/systemctl daemon-reload');
    }

    /* video terminal: systemd manages getty@tty1 via autovt by default;
     * only disable it when the operator explicitly selected no video output. */
    if ($virtual_enabled) {
        mwexecf('/usr/bin/systemctl unmask %s', ['getty@tty1.service'], true);
    } else {
        mwexecf('/usr/bin/systemctl mask %s', ['getty@tty1.service'], true);
    }

    configd_run('template reload OPNsense/Auth');

    service_log("done.\n", $verbose);
}

function reset_factory_defaults($sync = true)
{
    mwexecf('/bin/rm -fr /conf/* /var/log/* /root/.history');
    mwexecf('/usr/local/sbin/opnsense-beep stop');

    /* as we go through a special case directly shut down */
    $shutdown_cmd = '/sbin/shutdown -op now';
    if ($sync) {
        mwexecf($shutdown_cmd);
        while (true) {
            sleep(1);
        }
    } else {
        mwexecfb($shutdown_cmd);
    }
}
