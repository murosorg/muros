#!/usr/bin/python3

"""
    Copyright (c) 2023 Ad Schellevis <ad@opnsense.org>
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
import subprocess
import time
import ujson


interfaces = {}
sp_links = subprocess.run(['/usr/sbin/ip', '-j', 'link', 'show'], capture_output=True, text=True)
try:
    links = ujson.loads(sp_links.stdout or '[]')
except ValueError:
    links = []
for link in links:
    ifname = link.get('ifname')
    if ifname:
        interfaces[ifname] = 'up' if 'UP' in link.get('flags', []) else 'down'

sp = subprocess.run(['/usr/bin/wg', 'show', 'all', 'dump'], capture_output=True, text=True)
result = {'records': []}
if sp.returncode == 0:
    for line in sp.stdout.split("\n"):
        record = {}
        parts = line.split("\t")
        # parse fields as explained in 'man wg'
        record['if'] = parts[0] if len(parts) else None
        if len(parts) == 5:
            # intentially skip private key, should not expose it
            record['type'] = 'interface'
            record['public-key'] = parts[2]
            record['listen-port'] = parts[3]
            record['fwmark'] = parts[4]
            # convenience, copy listen-port to endpoint
            record['endpoint'] = parts[3]
            record['status'] = interfaces.get(record['if'], 'down')
        elif len(parts) == 9:
            record['type'] = 'peer'
            record['public-key'] = parts[1]
            # intentially skip preshared-key, should not expose it
            record['endpoint'] = parts[3]
            record['allowed-ips'] = parts[4]
            record['latest-handshake'] = int(parts[5]) if parts[5].isdigit() else 0
            record['transfer-rx'] = int(parts[6]) if parts[6].isdigit() else 0
            record['transfer-tx'] = int(parts[7]) if parts[7].isdigit() else 0
            record['persistent-keepalive'] = parts[8]
            # Derive a coarse connection status from the last handshake so the
            # UI does not have to. WireGuard refreshes a session roughly every
            # two minutes, so treat a peer as online when it handshaked within
            # the last three minutes and offline otherwise.
            handshake = record['latest-handshake']
            # Seconds since the last handshake, or -1 when the peer has never
            # completed one. Lets the UI render a "last seen" without repeating
            # the clock arithmetic.
            record['handshake-age'] = int(time.time() - handshake) if handshake else -1
            record['status'] = 'online' if handshake and (time.time() - handshake) <= 180 else 'offline'
        else:
            continue
        result['records'].append(record)

    # Annotate every interface record with how many peers it has and how many
    # are currently online, so a dashboard can summarise a tunnel at a glance
    # without grouping the flat record list itself.
    for record in result['records']:
        if record.get('type') != 'interface':
            continue
        peers = [r for r in result['records']
                 if r.get('type') == 'peer' and r.get('if') == record['if']]
        record['peers'] = len(peers)
        record['peers-online'] = sum(1 for r in peers if r.get('status') == 'online')

    result['status'] = 'ok'
else:
    result['status'] = 'failed'

print(ujson.dumps(result))
