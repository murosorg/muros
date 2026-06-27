#!/usr/local/bin/python3

"""
    Copyright (c) 2022 Ad Schellevis <ad@opnsense.org>
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

    --

    Flush selected entries from the kernel SPD. On Linux the policies live in
    the XFRM stack, so we delete them with 'ip xfrm policy delete' keyed on
    the same selector (src, dst, direction) the listing exposes.
"""
import argparse
import subprocess
import ujson

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('id', help='record id (md5 hash)')
    cmd_args = parser.parse_args()

    result = {'status': 'not_found'}
    sp = subprocess.run(
        ['/usr/local/opnsense/scripts/ipsec/list_spd.py'], capture_output=True, text=True
    )
    payload = ujson.loads(sp.stdout)
    spds = cmd_args.id.split(',')
    deleted_entries = []
    for record in payload['records']:
        if record['id'] in spds:
            result['status'] = 'found'
            deleted_entries.append({
                'src_range': record['src'],
                'dst_range': record['dst'],
                'direction': record['dir'],
            })
            subprocess.run([
                '/usr/sbin/ip', 'xfrm', 'policy', 'delete',
                'src', record['src'], 'dst', record['dst'], 'dir', record['dir']
            ], capture_output=True, text=True)

    result['items'] = deleted_entries
    print(ujson.dumps(result))
