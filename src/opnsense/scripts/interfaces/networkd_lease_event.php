#!/usr/bin/php
<?php

/*
 * MurOS DHCPv4 lease change handler for Debian (systemd-networkd).
 *
 * On FreeBSD every lease transition ran dhclient-script, which published the
 * router, the nameservers and the search domain through ifctl and then asked
 * configd to run the "newwanip" chain (gateway, routes, filter reload, dynamic
 * DNS, ...). systemd-networkd has no per-lease hook: it only writes the lease
 * to /run/systemd/netif/leases/<ifindex>. This script is triggered by
 * muros-lease-watch.path whenever that directory changes, diffs each lease
 * against the last state it published and replays the same chain for the
 * interfaces whose lease actually changed.
 *
 * Usage: networkd_lease_event.php [device ...]   (default: every lease)
 */

$leaseDir = '/run/systemd/netif/leases';
$stateDir = '/var/db/muros-leases';

function device_by_ifindex($ifindex)
{
    foreach (glob('/sys/class/net/*/ifindex') as $path) {
        if (trim((string)@file_get_contents($path)) === (string)$ifindex) {
            return basename(dirname($path));
        }
    }

    return '';
}

/* The lease file is a flat KEY=VALUE list, documented as private data: only
 * the few keys the newwanip chain needs are read, missing keys are ignored. */
function read_lease($file)
{
    $lease = [];
    foreach (@file($file, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES) ?: [] as $line) {
        if ($line === '' || $line[0] === '#' || strpos($line, '=') === false) {
            continue;
        }
        list($key, $value) = explode('=', $line, 2);
        $lease[trim($key)] = trim($value);
    }

    return $lease;
}

function ifctl($args)
{
    $cmd = '/usr/local/sbin/ifctl';
    foreach ($args as $arg) {
        $cmd .= ' ' . escapeshellarg($arg);
    }
    exec($cmd . ' > /dev/null 2>&1');
}

function publish($device, $lease)
{
    /* default gateway offered with the lease */
    $router = '';
    foreach (preg_split('/\s+/', $lease['ROUTER'] ?? '') as $candidate) {
        if (filter_var($candidate, FILTER_VALIDATE_IP, FILTER_FLAG_IPV4) && $candidate !== '255.255.255.255') {
            $router = $candidate;
            break;
        }
    }
    $args = ['-i', $device, '-4rd'];
    if ($router !== '') {
        $args[] = '-a';
        $args[] = $router;
    }
    ifctl($args);

    /* nameservers and search domain, consumed by the resolver configuration */
    $args = ['-i', $device, '-4nd'];
    foreach (preg_split('/\s+/', $lease['DNS'] ?? '') as $server) {
        if (filter_var($server, FILTER_VALIDATE_IP, FILTER_FLAG_IPV4)) {
            $args[] = '-a';
            $args[] = $server;
        }
    }
    ifctl($args);

    $args = ['-i', $device, '-4sd'];
    $domain = $lease['DOMAINNAME'] ?? '';
    if ($domain !== '' && preg_match('/^[A-Za-z0-9._-]+$/', $domain)) {
        $args[] = '-a';
        $args[] = $domain;
    }
    ifctl($args);
}

function fingerprint($lease)
{
    $keys = ['ADDRESS', 'NETMASK', 'ROUTER', 'DNS', 'DOMAINNAME', 'SERVER_ADDRESS'];
    $parts = [];
    foreach ($keys as $key) {
        $parts[] = $key . '=' . ($lease[$key] ?? '');
    }

    return implode("\n", $parts) . "\n";
}

/*
 * MurOS: the boot chain publishes the same information for every interface and
 * configd defers "interface newip" while the system is booting, so a lease that
 * lands in the middle of the chain must not be replayed here. The chain holds
 * the exclusive lock on /var/run/booting for its whole run, which is what
 * product::booting() probes. The path and timer units are ordered after the
 * chain, but the kernel can still deliver a lease event before they are, so the
 * lock is checked here too; the next sweep of the timer picks the lease up.
 */
$lock = @fopen('/var/run/booting', 'r');
if ($lock !== false) {
    if (!flock($lock, LOCK_SH | LOCK_NB)) {
        exit(0);
    }
    flock($lock, LOCK_UN);
    fclose($lock);
}

$only = array_slice($argv, 1);
@mkdir($stateDir, 0700, true);

$seen = [];
foreach (glob($leaseDir . '/*') as $file) {
    $ifindex = basename($file);
    if (!ctype_digit($ifindex)) {
        continue;
    }
    $device = device_by_ifindex($ifindex);
    if ($device === '' || (!empty($only) && !in_array($device, $only, true))) {
        continue;
    }
    $lease = read_lease($file);
    if (!filter_var($lease['ADDRESS'] ?? '', FILTER_VALIDATE_IP, FILTER_FLAG_IPV4)) {
        continue;
    }

    $seen[$device] = true;
    $stateFile = $stateDir . '/' . $device;
    $current = fingerprint($lease);
    if (@file_get_contents($stateFile) === $current) {
        continue;
    }

    file_put_contents($stateFile, $current);
    chmod($stateFile, 0600);
    publish($device, $lease);
    echo 'lease change on ' . $device . ' (' . $lease['ADDRESS'] . ')' . PHP_EOL;
    exec('/usr/local/sbin/configctl -d interface newip ' . escapeshellarg($device) . ' force > /dev/null 2>&1');
}

/*
 * IPv6 side. There is no lease file for router advertisements and networkd
 * keeps its DHCPv6 state private, so the acquired state is read back from the
 * kernel: only addresses flagged dynamic come from an advertisement or from the
 * DHCPv6 client, which excludes the statically configured interfaces. The
 * router is taken from the default route of the device, as the rtsold script
 * used to publish it.
 */
function dynamic_addresses6($device)
{
    $json = shell_exec(sprintf('/usr/sbin/ip -6 -j addr show dev %s scope global 2>/dev/null', escapeshellarg($device)));
    $addresses = [];
    foreach (json_decode((string)$json, true) ?: [] as $entry) {
        foreach (($entry['addr_info'] ?? []) as $ai) {
            if (!empty($ai['dynamic']) && !empty($ai['local'])) {
                $addresses[] = $ai['local'] . '/' . ($ai['prefixlen'] ?? 128);
            }
        }
    }
    sort($addresses);

    return $addresses;
}

function router6($device)
{
    $json = shell_exec(sprintf('/usr/sbin/ip -6 -j route show default dev %s 2>/dev/null', escapeshellarg($device)));
    foreach (json_decode((string)$json, true) ?: [] as $route) {
        if (!empty($route['gateway'])) {
            return $route['gateway'];
        }
    }

    return '';
}

foreach (glob('/sys/class/net/*') as $path) {
    $device = basename($path);
    if ($device === 'lo' || (!empty($only) && !in_array($device, $only, true))) {
        continue;
    }

    $addresses = dynamic_addresses6($device);
    $router = router6($device);
    $stateFile = $stateDir . '/' . $device . '.v6';
    $current = implode(' ', $addresses) . "\n" . $router . "\n";
    $previous = @file_get_contents($stateFile);

    if ($previous === $current) {
        continue;
    }
    if ($previous === false && empty($addresses)) {
        /* never acquired anything, nothing to announce */
        continue;
    }

    if (empty($addresses)) {
        @unlink($stateFile);
    } else {
        file_put_contents($stateFile, $current);
        chmod($stateFile, 0600);
    }

    $args = ['-i', $device, '-6rd'];
    if (filter_var($router, FILTER_VALIDATE_IP, FILTER_FLAG_IPV6)) {
        $args[] = '-a';
        $args[] = $router;
    }
    ifctl($args);

    echo 'ipv6 change on ' . $device . ' (' . (empty($addresses) ? 'released' : implode(' ', $addresses)) . ')' . PHP_EOL;
    exec('/usr/local/sbin/configctl -d interface newipv6 ' . escapeshellarg($device) . ' force > /dev/null 2>&1');
}

/* A lease that disappeared (released, expired, interface reset) leaves the
 * published router and resolver behind, so clear them and let the chain run
 * once more to drop the gateway and reload the ruleset. */
foreach (glob($stateDir . '/*') as $stateFile) {
    $device = basename($stateFile);
    /* the IPv6 state files of the loop above are not DHCPv4 leases */
    if (substr($device, -3) === '.v6') {
        continue;
    }
    if (isset($seen[$device]) || (!empty($only) && !in_array($device, $only, true))) {
        continue;
    }
    @unlink($stateFile);
    publish($device, []);
    echo 'lease gone on ' . $device . PHP_EOL;
    exec('/usr/local/sbin/configctl -d interface newip ' . escapeshellarg($device) . ' > /dev/null 2>&1');
}

exit(0);
