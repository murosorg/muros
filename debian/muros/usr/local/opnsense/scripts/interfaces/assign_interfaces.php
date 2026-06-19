#!/usr/bin/php
<?php

/*
 * MurOS automatic interface assignment for Debian.
 *
 * The factory configuration ships logical interfaces (wan, lan, optX) whose
 * physical device is a placeholder (mismatchN), exactly like a fresh
 * OPNsense before the installer's interface assignment step. On FreeBSD that
 * step maps the placeholders to em0/igb0/...; here it maps them to the real
 * Linux devices (eth0, ens19, ...).
 *
 * The mapping is idempotent: an interface is (re)assigned only when its
 * current device is empty, a placeholder, or no longer present. A device
 * that already carries the default route is preferred for the WAN so the
 * uplink keeps its role. Already-valid assignments are never disturbed.
 *
 * Usage: assign_interfaces.php [config.xml]   (defaults to /conf/config.xml)
 */

$configFile = isset($argv[1]) ? $argv[1] : '/conf/config.xml';
if (!is_writable($configFile)) {
    fwrite(STDERR, 'config not writable: ' . $configFile . PHP_EOL);
    exit(1);
}

function is_placeholder($dev)
{
    return $dev === '' || preg_match('/^mismatch[0-9]*$/', $dev) === 1;
}

function dev_present($dev)
{
    return $dev !== '' && is_dir('/sys/class/net/' . $dev);
}

/* Enumerate the physical ethernet devices: a real NIC has a backing device
 * link, is of type ARPHRD_ETHER and is neither a bridge nor wireless. */
function physical_devices()
{
    $devs = array();
    foreach (glob('/sys/class/net/*') as $path) {
        $name = basename($path);
        if ($name === 'lo') {
            continue;
        }
        if (!file_exists($path . '/device')) {
            continue;
        }
        if (is_dir($path . '/bridge') || is_dir($path . '/wireless') || file_exists($path . '/phy80211')) {
            continue;
        }
        if (trim(@file_get_contents($path . '/type')) !== '1') {
            continue;
        }
        $devs[] = $name;
    }
    sort($devs);
    return $devs;
}

function default_route_device()
{
    $out = shell_exec("ip -o route show default 2>/dev/null | awk '{print $5; exit}'");
    return $out === null ? '' : trim($out);
}

$dom = new DOMDocument();
$dom->preserveWhiteSpace = true;
$dom->formatOutput = false;
if (!$dom->load($configFile)) {
    fwrite(STDERR, 'cannot parse ' . $configFile . PHP_EOL);
    exit(1);
}
$xp = new DOMXPath($dom);
$ifaceNodes = $xp->query('/opnsense/interfaces/*');
if ($ifaceNodes === false || $ifaceNodes->length === 0) {
    fwrite(STDERR, 'no interfaces in config' . PHP_EOL);
    exit(1);
}

$candidates = physical_devices();
$assigned = array();
$needing = array();

foreach ($ifaceNodes as $iface) {
    $ifEl = null;
    foreach ($iface->getElementsByTagName('if') as $c) {
        $ifEl = $c;
        break;
    }
    $dev = $ifEl === null ? '' : trim($ifEl->nodeValue);
    if (!is_placeholder($dev) && dev_present($dev)) {
        $assigned[$dev] = true;
        continue;
    }
    $needing[] = array('node' => $iface, 'if' => $ifEl);
}

if (count($needing) === 0) {
    echo 'interface assignment: nothing to do' . PHP_EOL;
    exit(0);
}

/* Build the queue of free devices, default-route device first (WAN). */
$free = array();
$defDev = default_route_device();
if ($defDev !== '' && in_array($defDev, $candidates, true) && !isset($assigned[$defDev])) {
    $free[] = $defDev;
}
foreach ($candidates as $d) {
    if ($d !== $defDev && !isset($assigned[$d])) {
        $free[] = $d;
    }
}

$changed = false;
foreach ($needing as $item) {
    if (count($free) === 0) {
        break;
    }
    $dev = array_shift($free);
    $ifEl = $item['if'];
    if ($ifEl === null) {
        $ifEl = $dom->createElement('if');
        $item['node']->insertBefore($ifEl, $item['node']->firstChild);
    }
    $ifEl->nodeValue = $dev;
    $changed = true;
    echo 'interface assignment: ' . $item['node']->nodeName . ' -> ' . $dev . PHP_EOL;
}

if ($changed) {
    @copy($configFile, $configFile . '.assign.bak');
    if ($dom->save($configFile) === false) {
        fwrite(STDERR, 'failed to write ' . $configFile . PHP_EOL);
        exit(1);
    }
}
exit(0);
