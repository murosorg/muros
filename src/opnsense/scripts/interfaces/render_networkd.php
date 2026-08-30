#!/usr/bin/php
<?php

/*
 * MurOS interface addressing renderer for Debian (systemd-networkd).
 *
 * Translates the addressing of every assigned data interface in the
 * OPNsense-style configuration into a systemd-networkd .network unit. This
 * is the Debian replacement for the per-interface DHCP clients and ifconfig
 * calls performed by interface_configure() on FreeBSD: networkd handles the
 * DHCP lease (WAN), the static addressing (LAN) and the link state, and it
 * keeps doing so across reboots and carrier changes.
 *
 * The management interface is never rendered: it is left under the base
 * system networking (netplan/cloud-init) and may be listed in the reserved
 * file so MurOS can never lock the operator out.
 *
 * Generated files are owned by MurOS and named 10-muros-<dev>.network.
 * Stale files for interfaces that are no longer assigned are removed.
 *
 * Usage: render_networkd.php [config.xml]   (defaults to /conf/config.xml)
 */

$configFile = isset($argv[1]) ? $argv[1] : '/conf/config.xml';
$netDir = '/etc/systemd/network';
$reservedFile = '/usr/local/etc/muros/reserved.conf';

if (!is_readable($configFile)) {
    fwrite(STDERR, 'config not readable: ' . $configFile . PHP_EOL);
    exit(1);
}
$cfg = simplexml_load_file($configFile);
if ($cfg === false || !isset($cfg->interfaces)) {
    fwrite(STDERR, 'no interfaces section in config' . PHP_EOL);
    exit(1);
}

function dev_exists($dev)
{
    return $dev !== '' && is_dir('/sys/class/net/' . $dev);
}

function reserved_devices($file)
{
    if (!is_readable($file)) {
        return array();
    }
    $out = array();
    foreach (file($file, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES) as $line) {
        $line = trim($line);
        if ($line !== '' && $line[0] !== '#') {
            $out[$line] = true;
        }
    }
    return $out;
}

$reserved = reserved_devices($reservedFile);
@mkdir($netDir, 0755, true);
$wanted = array();

/*
 * Interfaces reference their upstream by gateway name (e.g. LAN_GW), not by
 * address. Build a name -> address map from the gateway definitions so the
 * default route can be written into the .network unit, mirroring how FreeBSD
 * resolved the named gateway before installing the route. Both the modern
 * OPNsense.Gateways model and the legacy <gateways> section are honoured.
 */
function gateway_map($cfg)
{
    $map = array('inet' => array(), 'inet6' => array());
    $sources = array();
    if (isset($cfg->OPNsense->Gateways->gateway_item)) {
        $sources[] = $cfg->OPNsense->Gateways->gateway_item;
    }
    if (isset($cfg->gateways->gateway_item)) {
        $sources[] = $cfg->gateways->gateway_item;
    }
    foreach ($sources as $items) {
        foreach ($items as $gw) {
            if (!empty((string)$gw->disabled)) {
                continue;
            }
            $name = trim((string)$gw->name);
            $addr = trim((string)$gw->gateway);
            if ($name === '' || !filter_var($addr, FILTER_VALIDATE_IP)) {
                continue;
            }
            $proto = (trim((string)$gw->ipprotocol) === 'inet6') ? 'inet6' : 'inet';
            $map[$proto][$name] = $addr;
        }
    }
    return $map;
}

$gwmap = gateway_map($cfg);

foreach ($cfg->interfaces->children() as $key => $node) {
    $dev = trim((string)$node->if);
    if (!dev_exists($dev) || isset($reserved[$dev])) {
        continue;
    }
    if (empty((string)$node->enable)) {
        continue;
    }

    /*
     * Never render a loopback or virtual interface (e.g. the OPNsense lo0
     * entry) onto a physical device. On FreeBSD lo0 is the loopback device;
     * after assignment its <if> may point at a spare NIC, but its loopback
     * addressing (127.0.0.1/::1) belongs to the kernel 'lo' device, which
     * Linux configures on its own. systemd-networkd also refuses to assign a
     * loopback address to a real NIC, which would otherwise leave that
     * interface stuck with 127.0.0.1/8 and no usable addressing.
     */
    if (!empty((string)$node->virtual) || !empty((string)$node->internal_dynamic)) {
        continue;
    }

    $ip4 = trim((string)$node->ipaddr);
    $sub4 = trim((string)$node->subnet);
    $gw4 = trim((string)$node->gateway);
    $ip6 = trim((string)$node->ipaddrv6);
    $sub6 = trim((string)$node->subnetv6);
    $gw6 = trim((string)$node->gatewayv6);
    $mtu = trim((string)$node->mtu);

    /* Skip the kernel loopback device and any loopback addressing. */
    if ($dev === 'lo' || strncmp($ip4, '127.', 4) === 0 || $ip6 === '::1') {
        continue;
    }

    /* Resolve named gateways (e.g. LAN_GW) to their address. */
    if ($gw4 !== '' && !filter_var($gw4, FILTER_VALIDATE_IP, FILTER_FLAG_IPV4)) {
        $gw4 = isset($gwmap['inet'][$gw4]) ? $gwmap['inet'][$gw4] : '';
    }
    if ($gw6 !== '' && !filter_var($gw6, FILTER_VALIDATE_IP, FILTER_FLAG_IPV6)) {
        $gw6 = isset($gwmap['inet6'][$gw6]) ? $gwmap['inet6'][$gw6] : '';
    }

    $v4dhcp = ($ip4 === 'dhcp');
    $v6dhcp = ($ip6 === 'dhcp6');
    /*
     * SLAAC: no DHCPv6 client, the address comes from the router
     * advertisements. FreeBSD ran rtsold for this, on Linux the kernel builds
     * the address as soon as advertisements are accepted, which networkd
     * enables per link.
     */
    $v6slaac = ($ip6 === 'slaac');

    /*
     * Prefix delegation. FreeBSD had the dhcp6c script carve a /64 out of the
     * IA_PD and assign it to the tracking interface; networkd does that on its
     * own once the downstream interface points at the upstream one and picks a
     * subnet in the delegated prefix. Announcing the prefix is left to radvd,
     * which owns the router advertisements of this platform.
     */
    $v6track = ($ip6 === 'track6');
    $trackdev = '';
    $trackid = '';
    if ($v6track) {
        $trackif = trim((string)$node->{'track6-interface'});
        if ($trackif !== '' && isset($cfg->interfaces->$trackif)) {
            $trackdev = trim((string)$cfg->interfaces->$trackif->if);
        }
        /* stored as a decimal integer, the GUI converts the hexadecimal input */
        $trackid = trim((string)$node->{'track6-prefix-id'});
        $trackid = ctype_digit($trackid) ? $trackid : '0';
    }
    $v4static = filter_var($ip4, FILTER_VALIDATE_IP, FILTER_FLAG_IPV4) && ctype_digit($sub4);
    $v6static = filter_var($ip6, FILTER_VALIDATE_IP, FILTER_FLAG_IPV6) && ctype_digit($sub6);

    if ($v4dhcp && $v6dhcp) {
        $dhcp = 'yes';
    } elseif ($v4dhcp) {
        $dhcp = 'ipv4';
    } elseif ($v6dhcp) {
        $dhcp = 'ipv6';
    } else {
        $dhcp = 'no';
    }

    $lines = array();
    $lines[] = '# Generated by MurOS render_networkd.php. Do not edit by hand.';
    $lines[] = '[Match]';
    $lines[] = 'Name=' . $dev;
    $lines[] = '';
    if ($mtu !== '' && ctype_digit($mtu)) {
        $lines[] = '[Link]';
        $lines[] = 'MTUBytes=' . $mtu;
        $lines[] = '';
    }
    $lines[] = '[Network]';
    $lines[] = 'DHCP=' . $dhcp;
    $lines[] = 'ConfigureWithoutCarrier=yes';
    $lines[] = 'IPv6AcceptRA=' . ($v6dhcp || $v6slaac ? 'yes' : 'no');
    if ($v6track && $trackdev !== '') {
        $lines[] = 'DHCPPrefixDelegation=yes';
    }
    if ($v4static) {
        $lines[] = 'Address=' . $ip4 . '/' . $sub4;
    }
    if ($v6static) {
        $lines[] = 'Address=' . $ip6 . '/' . $sub6;
    }
    if ($v4static && filter_var($gw4, FILTER_VALIDATE_IP, FILTER_FLAG_IPV4)) {
        $lines[] = 'Gateway=' . $gw4;
    }
    if ($v6static && filter_var($gw6, FILTER_VALIDATE_IP, FILTER_FLAG_IPV6)) {
        $lines[] = 'Gateway=' . $gw6;
    }

    /*
     * A DHCP interface may still carry a fixed alias address, which the
     * FreeBSD dhclient configuration expressed as an "alias" block.
     */
    $alias4 = trim((string)$node->{'alias-address'});
    $aliassub4 = trim((string)$node->{'alias-subnet'});
    if ($v4dhcp && filter_var($alias4, FILTER_VALIDATE_IP, FILTER_FLAG_IPV4) && ctype_digit($aliassub4)) {
        $lines[] = 'Address=' . $alias4 . '/' . $aliassub4;
    }
    $lines[] = '';

    /*
     * DHCPv4 client options. FreeBSD passed these to dhclient through
     * /var/etc/dhclient.<dev>.conf; the lease is acquired by networkd here, so
     * they belong to the unit. "dhcpvlanprio" has no networkd equivalent and is
     * not rendered.
     */
    if ($v4dhcp) {
        $hostname = trim((string)$node->dhcphostname);
        $rejectFrom = trim((string)$node->dhcprejectfrom);
        $lines[] = '[DHCPv4]';
        /* honour the MTU offered by the server only when asked to */
        $lines[] = 'UseMTU=' . (trim((string)$node->dhcphonourmtu) !== '' ? 'yes' : 'no');
        if ($hostname !== '' && preg_match('/^[A-Za-z0-9._-]+$/', $hostname)) {
            $lines[] = 'Hostname=' . $hostname;
        }
        /* ignore offers from these servers (dhclient "reject") */
        $deny = array();
        foreach (preg_split('/[\s,]+/', $rejectFrom) as $server) {
            if (filter_var($server, FILTER_VALIDATE_IP, FILTER_FLAG_IPV4)) {
                $deny[] = $server;
            }
        }
        if (!empty($deny)) {
            $lines[] = 'DenyList=' . implode(' ', $deny);
        }
        $lines[] = '';
    }

    if ($v6track && $trackdev !== '') {
        $lines[] = '[DHCPPrefixDelegation]';
        $lines[] = 'UplinkInterface=' . $trackdev;
        $lines[] = 'SubnetId=' . $trackid;
        /* radvd sends the advertisements on this platform */
        $lines[] = 'Announce=no';
        $lines[] = '';
    }

    /*
     * DHCPv6 client options, the counterpart of the dhcp6c configuration.
     * "dhcp6prefixonly" (request a prefix but no address) has no networkd
     * switch and is not rendered.
     */
    if ($v6dhcp) {
        $lines[] = '[DHCPv6]';
        /* do not overwrite the resolver with the values from the lease */
        if (trim((string)$node->dhcp6_norequest_dns) !== '') {
            $lines[] = 'UseDNS=no';
        }
        /*
         * Prefix hint: the GUI stores the number of bits below /64, so a
         * delegation size of /48 is stored as 16.
         */
        $pdlen = trim((string)$node->{'dhcp6-ia-pd-len'});
        if (trim((string)$node->{'dhcp6-ia-pd-send-hint'}) !== '' && ctype_digit($pdlen)) {
            $prefixlen = 64 - (int)$pdlen;
            if ($prefixlen > 0 && $prefixlen <= 64) {
                $lines[] = 'PrefixDelegationHint=::/' . $prefixlen;
            }
        }
        $lines[] = '';
    }

    $target = $netDir . '/10-muros-' . $dev . '.network';
    file_put_contents($target, implode(PHP_EOL, $lines));
    chmod($target, 0644);
    $wanted[$target] = true;
    echo 'rendered ' . $node->getName() . ' -> ' . $dev . ' (dhcp=' . $dhcp . ($v4static ? ', ' . $ip4 . '/' . $sub4 : '') . ')' . PHP_EOL;
}

/* Remove MurOS-owned files for interfaces no longer assigned. */
foreach (glob($netDir . '/10-muros-*.network') as $existing) {
    if (!isset($wanted[$existing])) {
        @unlink($existing);
        echo 'removed stale ' . $existing . PHP_EOL;
    }
}

exec('systemctl is-active --quiet systemd-networkd', $o, $rc);
if ($rc !== 0) {
    exec('systemctl enable --now systemd-networkd 2>&1');
}
exec('networkctl reload 2>&1', $o2, $rc2);
exit(0);
