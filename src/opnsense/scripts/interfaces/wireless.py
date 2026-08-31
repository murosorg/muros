#!/usr/bin/python3

"""
    Copyright (c) 2026 MurOS
    All rights reserved.

    Redistribution and use in source and binary forms, with or without
    modification, are permitted provided that the following conditions are met:

    1. Redistributions of source code must retain the above copyright notice,
     this list of conditions and the following disclaimer.

    2. Redistributions in binary form must reproduce the above copyright
     notice, this list of conditions and the following disclaimer in the
     documentation and/or other materials provided with the distribution.

    THIS SOFTWARE IS PROVIDED ``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES.
    --------------------------------------------------------------------------------------
    wireless radio information, scan results and associated stations, Debian / iw

    FreeBSD answered all three with ifconfig: "list caps", "list chan",
    "list txpower", "list scan" and "list sta". None of them exist here, the
    radio is driven by nl80211 and questioned with iw, so the pages that used
    to parse ifconfig get a JSON document instead of a text table.

    Every subcommand takes the network device name and prints JSON.
"""
import json
import os
import re
import subprocess
import sys

IW = '/usr/sbin/iw'


def run(args):
    """ run a command, return its output, an empty string when it fails """
    try:
        sp = subprocess.run(args, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ''
    return sp.stdout if sp.returncode == 0 else ''


def phy_of(device):
    """ the radio a network device belongs to, None when it is not wireless """
    if not re.match(r'^[0-9a-zA-Z._-]+$', device or ''):
        return None
    path = '/sys/class/net/%s/phy80211/name' % device
    if not os.path.isfile(path):
        return None
    with open(path) as fhandle:
        return fhandle.read().strip()


def channel_of(freq):
    """ the channel number of a frequency in MHz """
    if freq == 2484:
        return 14
    if 2412 <= freq <= 2472:
        return (freq - 2407) // 5
    if 5000 <= freq <= 5895:
        return (freq - 5000) // 5
    if 5955 <= freq <= 7115:
        return (freq - 5950) // 5
    return 0


def band_of(freq):
    """ the 802.11 band a frequency belongs to """
    if freq < 3000:
        return '11g'
    if freq < 5925:
        return '11a'
    return '11ax'


def radio_info(device):
    """ interface modes and channels the radio supports, with their power limit """
    phy = phy_of(device)
    result = {'device': device, 'phy': phy, 'modes': [], 'channels': [], 'ht': False, 'vht': False, 'he': False}
    if phy is None:
        return result

    output = run([IW, 'phy', phy, 'info'])
    in_modes = False
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith('Supported interface modes:'):
            in_modes = True
            continue
        if in_modes:
            if stripped.startswith('*'):
                result['modes'].append(stripped[1:].strip())
                continue
            in_modes = False
        if 'HT Capabilities' in stripped or 'Capabilities: 0x' in stripped:
            result['ht'] = True
        if 'VHT Capabilities' in stripped:
            result['vht'] = True
        if 'HE Iftypes' in stripped or 'HE MAC Capabilities' in stripped:
            result['he'] = True
        found = re.match(r'^\* (\d+(?:\.\d+)?) MHz \[(\d+)\] \(([^)]*)\)$', stripped)
        if found:
            freq = int(float(found.group(1)))
            limit = found.group(3)
            power = None
            dbm = re.match(r'^([0-9.]+) dBm$', limit)
            if dbm:
                power = float(dbm.group(1))
            result['channels'].append({
                'channel': int(found.group(2)) or channel_of(freq),
                'freq': freq,
                'band': band_of(freq),
                'txpower': power,
                'disabled': 'disabled' in limit,
            })

    return result


def scan(device):
    """ the access points and ad-hoc peers the radio can hear """
    if phy_of(device) is None:
        return []

    output = run([IW, 'dev', device, 'scan'])
    if output == '':
        """ a scan needs the device up and fails while it serves an access point """
        output = run([IW, 'dev', device, 'scan', 'dump'])

    cells = []
    current = None
    for line in output.splitlines():
        stripped = line.strip()
        found = re.match(r'^BSS ([0-9a-fA-F:]{17})', stripped)
        if found:
            current = {
                'bssid': found.group(1).lower(), 'ssid': '', 'channel': 0, 'freq': 0,
                'rate': 0, 'rssi': None, 'interval': 0, 'privacy': False, 'mode': 'ESS',
                'wpa': [],
            }
            cells.append(current)
            continue
        if current is None:
            continue
        if stripped.startswith('SSID: '):
            current['ssid'] = stripped[6:]
        elif stripped.startswith('freq: '):
            current['freq'] = int(float(stripped[6:].split()[0]))
            current['channel'] = current['channel'] or channel_of(current['freq'])
        elif stripped.startswith('DS Parameter set: channel '):
            current['channel'] = int(stripped.split()[-1])
        elif stripped.startswith('signal: '):
            current['rssi'] = float(stripped.split()[1])
        elif stripped.startswith('beacon interval: '):
            current['interval'] = int(stripped.split()[2])
        elif stripped.startswith('capability: '):
            current['privacy'] = 'Privacy' in stripped
            current['mode'] = 'IBSS' if 'IBSS' in stripped else 'ESS'
        elif stripped.startswith('Supported rates: ') or stripped.startswith('Extended supported rates: '):
            for token in stripped.split(':', 1)[1].split():
                try:
                    current['rate'] = max(current['rate'], float(token.rstrip('*')))
                except ValueError:
                    continue
        elif stripped.startswith('RSN:'):
            current['wpa'].append('WPA2')
        elif stripped.startswith('WPA:'):
            current['wpa'].append('WPA')

    for cell in cells:
        if not cell['wpa'] and cell['privacy']:
            cell['wpa'].append('WEP')
        cell['security'] = '/'.join(cell['wpa']) if cell['wpa'] else 'open'

    return sorted(cells, key=lambda item: (item['ssid'].lower(), item['bssid']))


def stations(device):
    """ the peers associated with the radio, in access point or ad-hoc mode """
    if phy_of(device) is None:
        return []

    output = run([IW, 'dev', device, 'station', 'dump'])
    peers = []
    current = None
    for line in output.splitlines():
        stripped = line.strip()
        found = re.match(r'^Station ([0-9a-fA-F:]{17})', stripped)
        if found:
            current = {
                'mac': found.group(1).lower(), 'inactive': 0, 'rx_bytes': 0, 'tx_bytes': 0,
                'rx_packets': 0, 'tx_packets': 0, 'signal': None, 'tx_rate': '', 'rx_rate': '',
                'connected': 0, 'flags': [],
            }
            peers.append(current)
            continue
        if current is None or ':' not in stripped:
            continue
        key, value = [part.strip() for part in stripped.split(':', 1)]
        key = key.lower()
        if key == 'inactive time':
            current['inactive'] = int(value.split()[0])
        elif key == 'rx bytes':
            current['rx_bytes'] = int(value)
        elif key == 'tx bytes':
            current['tx_bytes'] = int(value)
        elif key == 'rx packets':
            current['rx_packets'] = int(value)
        elif key == 'tx packets':
            current['tx_packets'] = int(value)
        elif key == 'signal':
            current['signal'] = float(value.split()[0])
        elif key == 'tx bitrate':
            current['tx_rate'] = value
        elif key == 'rx bitrate':
            current['rx_rate'] = value
        elif key == 'connected time':
            current['connected'] = int(value.split()[0])
        elif key in ('authorized', 'authenticated', 'associated', 'preamble', 'wmm/wme', 'tdls peer'):
            if value.lower() in ('yes', 'short'):
                current['flags'].append(key)

    return peers


if __name__ == '__main__':
    command = sys.argv[1] if len(sys.argv) > 1 else ''
    target = sys.argv[2] if len(sys.argv) > 2 else ''

    if command == 'info':
        print(json.dumps(radio_info(target)))
    elif command == 'scan':
        print(json.dumps(scan(target)))
    elif command == 'stations':
        print(json.dumps(stations(target)))
    else:
        print(json.dumps({'error': 'usage: wireless.py [info|scan|stations] <device>'}))
