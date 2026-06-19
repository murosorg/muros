#!/usr/bin/python3

"""
    Copyright (c) 2016-2019 Ad Schellevis <ad@opnsense.org>
    Copyright (c) 2026 MurOS
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
    returns the system routing table (Debian / iproute2)
"""
import json
import subprocess
import sys

FIELDNAMES = ['proto', 'destination', 'gateway', 'flags', 'netif', 'mtu']


def is_host(dst):
    if dst == 'default' or not dst:
        return False
    if '/' not in dst:
        return True
    return dst.rsplit('/', 1)[1] in ('32', '128')


def collect(proto, family_arg):
    rows = []
    try:
        sp = subprocess.run(['/usr/sbin/ip', '-j', family_arg, 'route', 'show'],
                            capture_output=True, text=True)
        data = json.loads(sp.stdout or '[]')
    except Exception:
        data = []
    for r in data:
        dst = r.get('dst', '')
        gw = r.get('gateway', '')
        flags = 'U'
        if gw:
            flags += 'G'
        if is_host(dst):
            flags += 'H'
        rows.append({
            'proto': proto,
            'destination': dst,
            'gateway': gw if gw else 'link#0',
            'flags': flags,
            'netif': r.get('dev', ''),
            'mtu': str(r.get('mtu', '')),
        })
    return rows


if __name__ == '__main__':
    result = collect('ipv4', '-4') + collect('ipv6', '-6')

    if 'json' in sys.argv:
        print(json.dumps(result))
    else:
        print('\t\t'.join(FIELDNAMES))
        for record in result:
            print('\t'.join(str(record.get(f, '')) for f in FIELDNAMES))
