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

    List the kernel Security Policy Database (SPD). On Linux charon installs
    the negotiated policies into the XFRM stack, so we read them with
    'ip xfrm policy' instead of the FreeBSD/KAME 'setkey -DP'. The output
    keeps the same field names the SPD grid expects, and the record id
    (md5 of 'src dst dir') matches the one spddelete.py recomputes.
"""
import hashlib
import subprocess
import ujson


def _ident(rec):
    return hashlib.md5(("%s %s %s" % (rec['src'], rec['dst'], rec['dir'])).encode()).hexdigest()


def parse_policies(payload):
    records = []
    rec = None
    for raw in payload.split("\n"):
        if raw.strip() == '':
            continue
        indented = raw[0] in (' ', '\t')
        parts = raw.split()
        if not indented and len(parts) >= 4 and parts[0] == 'src' and parts[2] == 'dst':
            if rec is not None:
                records.append(rec)
            rec = {'src': parts[1], 'dst': parts[3], 'dir': None, 'type': None,
                   'upperspec': 'any', 'src-dst': None, 'level': None, 'proto': None,
                   'mode': None, 'reqid': None, 'ikeid': None}
            # optional selector protocol, e.g. 'proto tcp'
            for i, part in enumerate(parts):
                if part == 'proto' and i + 1 < len(parts):
                    rec['upperspec'] = parts[i + 1]
            continue
        if rec is None:
            continue
        if parts[0] == 'dir':
            rec['dir'] = parts[1] if len(parts) > 1 else None
            for i, part in enumerate(parts):
                if part == 'ptype' and i + 1 < len(parts):
                    rec['type'] = parts[i + 1]
        elif parts[0] == 'tmpl':
            for i, part in enumerate(parts):
                if part == 'src' and i + 1 < len(parts):
                    t_src = parts[i + 1]
                elif part == 'dst' and i + 1 < len(parts):
                    t_dst = parts[i + 1]
            try:
                rec['src-dst'] = [t_src, t_dst]
            except NameError:
                rec['src-dst'] = None
        elif parts[0] == 'proto':
            for i, part in enumerate(parts):
                if part == 'proto' and i + 1 < len(parts):
                    rec['proto'] = parts[i + 1]
                elif part == 'reqid' and i + 1 < len(parts):
                    rec['reqid'] = parts[i + 1].split('(')[0]
                elif part == 'mode' and i + 1 < len(parts):
                    rec['mode'] = parts[i + 1]
                elif part == 'level' and i + 1 < len(parts):
                    rec['level'] = parts[i + 1]
    if rec is not None:
        records.append(rec)
    return records


if __name__ == '__main__':
    result = {'records': []}
    try:
        payload = subprocess.run(
            ['/usr/sbin/ip', 'xfrm', 'policy'], capture_output=True, text=True
        ).stdout
    except FileNotFoundError:
        payload = ''

    for rec in parse_policies(payload):
        # socket policies (dir in/out/fwd absent) are kernel-internal; skip
        if not rec.get('dir'):
            continue
        rec['id'] = _ident(rec)
        result['records'].append(rec)

    all_keys = set()
    for record in result['records']:
        all_keys = all_keys.union(record.keys())
    for record in result['records']:
        for key in all_keys:
            if key not in record:
                record[key] = None

    print(ujson.dumps(result))
