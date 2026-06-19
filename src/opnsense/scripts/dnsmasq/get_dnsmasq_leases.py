#!/usr/local/bin/python3

"""
    Copyright (c) 2025 Ad Schellevis <ad@opnsense.org>
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
"""

import ipaddress
import subprocess
import os
import ujson


if __name__ == '__main__':
    filename = '/var/db/dnsmasq.leases'
    ranges = {}
    leases = []
    this_interface = None
    # MurOS: build the interface -> network map from iproute2 (Debian) instead
    # of parsing FreeBSD `ifconfig -f inet:cidr`. Point-to-point addresses
    # (those carrying a distinct peer) are skipped, as before.
    addr_json = subprocess.run(
        ['/usr/sbin/ip', '-j', 'addr', 'show'], capture_output=True, text=True
    ).stdout
    try:
        links = ujson.loads(addr_json or '[]')
    except ValueError:
        links = []
    for link in links:
        this_interface = link.get('ifname')
        for addr in link.get('addr_info', []):
            local = addr.get('local')
            prefixlen = addr.get('prefixlen')
            if not local or prefixlen is None:
                continue
            if addr.get('address') and addr.get('address') != local:
                continue
            try:
                ranges[ipaddress.ip_network('%s/%s' % (local, prefixlen), strict=False)] = this_interface
            except ValueError:
                continue

    if os.path.isfile(filename):
        with open(filename, 'r') as leasefile:
            for line in leasefile:
                parts = line.split()
                if len(parts) > 4 and parts[0].isdigit():
                    lease = {
                        'expire': int(parts[0]),
                        'hwaddr': '',
                        'iaid': '',
                        'address': parts[2],
                        'hostname': parts[3],
                        'client_id': parts[4]
                    }

                    # MAC (IPv4) and IAID (IPv6) share the same spot
                    if ':' in parts[2]:
                        lease['iaid'] = parts[1]
                    else:
                        lease['hwaddr'] = parts[1]

                    # DUID-LL and DUID-LLT (IPv6) contain the hwaddr, extract it
                    if lease['hwaddr'] == '' and ':' in lease['client_id']:
                        parts_client_id = lease['client_id'].lower().split(":")
                        if len(parts_client_id) >= 10:
                            duid_type = parts_client_id[0:2]
                            if duid_type in [['00', '01'], ['00', '03']]:
                                lease['hwaddr'] = ":".join(parts_client_id[-6:])

                    for net in ranges:
                        if net.overlaps(ipaddress.ip_network(lease['address'])):
                            lease['if'] = ranges[net]
                            break
                    leases.append(lease)

    print (ujson.dumps({'records': leases}))
