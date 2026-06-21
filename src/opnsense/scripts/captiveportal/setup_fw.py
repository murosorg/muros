#!/usr/local/bin/python3
"""
    Copyright (c) 2025-2026 Deciso B.V.
    All rights reserved.

    Redistribution and use in source and binary forms, with or without
    modification, are permitted provided that the following conditions are met:

    1. Redistributions of source code must retain the above copyright notice,
     this list of conditions and the following disclaimer.

    2. Redistributions in binary form must reproduce the above copyright
     notice, this list of conditions and the following disclaimer in the
     documentation and/or other materials provided with the distribution.

    THIS SOFTWARE IS PROVIDED ``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES,
    INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY
    AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
    AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY,
    OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
    SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
    INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
    CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
    ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
    POSSIBILITY OF SUCH DAMAGE.

    --------------------------------------------------------------------------------------
    Build the captive portal nftables enforcement from the running config.

    Reads the captive portal zones from /conf/config.xml, resolves their
    interface devices and rebuilds the dedicated "inet captiveportal" table
    (redirect, forward gate and portal input rules) via lib/cpfw.build().
    Authenticated client membership and accounting are managed separately by
    the background process and are preserved across a rebuild.
"""
import ipaddress
import os
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import cpfw

CONFIG_XML = '/conf/config.xml'


def _csv(text):
    if not text:
        return []
    return [x.strip() for x in text.split(',') if x.strip()]


def _split_addresses(items):
    """ split a list of ip/network strings into v4 and v6 buckets """
    v4, v6 = [], []
    for item in items:
        try:
            net = ipaddress.ip_network(item, strict=False)
        except ValueError:
            continue
        (v6 if net.version == 6 else v4).append(item)
    return v4, v6


def load_zones(config_xml=CONFIG_XML):
    """ return the enforcement zone descriptors read from config.xml """
    tree = ET.parse(config_xml)
    root = tree.getroot()

    # map interface tag -> device name
    devices = {}
    interfaces = root.find('interfaces')
    if interfaces is not None:
        for intf in list(interfaces):
            dev = intf.findtext('if')
            if dev:
                devices[intf.tag] = dev.strip()

    zones = []
    for zone in root.findall('./OPNsense/captiveportal/zones/zone'):
        if zone.findtext('enabled') != '1':
            continue
        zoneid = zone.findtext('zoneid')
        if zoneid is None or zoneid.strip() == '':
            continue
        zone_devs = []
        for tag in _csv(zone.findtext('interfaces')):
            if tag in devices:
                zone_devs.append(devices[tag])
        if not zone_devs:
            continue
        allowed_v4, allowed_v6 = _split_addresses(_csv(zone.findtext('allowedAddresses')))
        zones.append({
            'zoneid': int(zoneid),
            'devices': zone_devs,
            'http_port': int(zoneid) + 9000,
            'https_port': int(zoneid) + 8000,
            'allowed_v4': allowed_v4,
            'allowed_v6': allowed_v6,
            'allowed_macs': _csv(zone.findtext('allowedMACAddresses')),
        })
    return zones


def main():
    try:
        zones = load_zones()
    except (OSError, ET.ParseError) as exc:
        print('captiveportal setup_fw: cannot read config (%s)' % exc)
        return 1
    if not zones:
        cpfw.teardown()
        print('OK (no enabled zones, captive portal firewall cleared)')
        return 0
    result = cpfw.build(zones)
    if result is not None and result.returncode != 0:
        print('captiveportal setup_fw failed: %s' % (result.stderr or '').strip())
        return 1
    print('OK (%d zone(s) applied)' % len(zones))
    return 0


if __name__ == '__main__':
    sys.exit(main())
