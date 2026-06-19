#!/usr/bin/php
<?php

/*
 * MurOS interface bring-up for Debian (iproute2).
 *
 * Reads the OPNsense-style configuration and configures every assigned
 * interface that is NOT owned by systemd-networkd. The management port is
 * left under networkd/netplan control so MurOS never fights the base
 * networking and can never lock the operator out. For every other assigned
 * interface the link is brought up and its static addressing from
 * config.xml is applied with iproute2. This is the Linux replacement for
 * the FreeBSD ifconfig calls performed by interface_configure() in
 * interfaces.inc.
 *
 * Usage: apply_linux.php [config.xml]   (defaults to /conf/config.xml)
 */

$configFile = isset($argv[1]) ? $argv[1] : '/conf/config.xml';
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

/* A device is owned by systemd-networkd when networkctl reports a real
 * network file for it. Those interfaces (typically the management port)
 * are left untouched. */
function networkd_managed($dev)
{
    $out = shell_exec('networkctl status ' . escapeshellarg($dev) . ' 2>/dev/null');
    if ($out === null) {
        return false;
    }
    foreach (explode(PHP_EOL, $out) as $line) {
        $p = strpos($line, 'Network File:');
        if ($p !== false) {
            $val = trim(substr($line, $p + 13));
            return $val !== '' && $val !== 'n/a';
        }
    }
    return false;
}

function run($cmd)
{
    $o = array();
    exec($cmd . ' 2>&1', $o, $rc);
    fwrite(STDOUT, '[' . ($rc === 0 ? 'ok' : 'rc=' . $rc) . '] ' . $cmd . PHP_EOL);
}

foreach ($cfg->interfaces->children() as $key => $node) {
    $dev = trim((string)$node->if);
    if (!dev_exists($dev)) {
        continue;
    }
    if (empty((string)$node->enable)) {
        run('ip link set ' . escapeshellarg($dev) . ' down');
        continue;
    }
    if (networkd_managed($dev)) {
        continue;
    }

    $mtu = trim((string)$node->mtu);
    if ($mtu !== '' && ctype_digit($mtu)) {
        run('ip link set ' . escapeshellarg($dev) . ' mtu ' . $mtu);
    }
    run('ip link set ' . escapeshellarg($dev) . ' up');

    $ip4 = trim((string)$node->ipaddr);
    $sub4 = trim((string)$node->subnet);
    if (filter_var($ip4, FILTER_VALIDATE_IP, FILTER_FLAG_IPV4) && ctype_digit($sub4)) {
        run('ip -4 addr replace ' . escapeshellarg($ip4 . '/' . $sub4) . ' dev ' . escapeshellarg($dev));
    }

    $ip6 = trim((string)$node->ipaddrv6);
    $sub6 = trim((string)$node->subnetv6);
    if (filter_var($ip6, FILTER_VALIDATE_IP, FILTER_FLAG_IPV6) && ctype_digit($sub6)) {
        run('ip -6 addr replace ' . escapeshellarg($ip6 . '/' . $sub6) . ' dev ' . escapeshellarg($dev));
    }
}

exit(0);
