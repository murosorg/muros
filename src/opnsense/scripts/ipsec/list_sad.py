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

    List the kernel Security Association Database (SAD). On Linux the IPsec
    SAs are installed by charon into the XFRM stack, so we read them with
    'ip -s xfrm state' instead of the FreeBSD/KAME 'setkey -D'. The output
    keeps the same field names the SAD grid expects.
"""
import hashlib
import subprocess
import ujson

FIELDS = ['src', 'dst', 'satype', 'state', 'replay', 'spi', 'reqid', 'mode',
          'alg_enc', 'alg_auth', 'addtime_created', 'bytes_current', 'ikeid']


def _strip(token):
    # '0x00000301(769)' -> '00000301' ; '16385(0x00004001)' -> '16385'
    token = token.split('(')[0]
    if token.startswith('0x'):
        token = token[2:]
    return token


def parse_states(payload):
    records = []
    entry = None
    section = None
    for raw in payload.split("\n"):
        if raw == '':
            continue
        indented = raw[0] in (' ', '\t')
        parts = raw.split()
        if not indented and len(parts) >= 4 and parts[0] == 'src' and parts[2] == 'dst':
            if entry is not None:
                records.append(entry)
            entry = {key: None for key in FIELDS}
            entry['src'] = parts[1]
            entry['dst'] = parts[3]
            section = None
            continue
        if entry is None:
            continue
        if parts and parts[0] == 'proto':
            for i, part in enumerate(parts):
                if part == 'proto' and i + 1 < len(parts):
                    entry['satype'] = parts[i + 1]
                elif part == 'spi' and i + 1 < len(parts):
                    entry['spi'] = _strip(parts[i + 1])
                elif part == 'reqid' and i + 1 < len(parts):
                    entry['reqid'] = _strip(parts[i + 1])
                elif part == 'mode' and i + 1 < len(parts):
                    entry['mode'] = parts[i + 1]
        elif parts and parts[0] == 'replay-window' and entry['replay'] is None:
            # The real replay-window line comes before the 'stats:' section,
            # which also starts with 'replay-window' (a replay error counter);
            # keep the first occurrence so the window size is not overwritten.
            entry['replay'] = parts[1] if len(parts) > 1 else None
        elif parts and parts[0] == 'enc':
            entry['alg_enc'] = parts[1] if len(parts) > 1 else None
        elif parts and parts[0] in ('auth-trunc', 'auth', 'aead'):
            entry['alg_auth'] = parts[1] if len(parts) > 1 else None
        elif raw.strip().startswith('lifetime current'):
            section = 'current'
        elif section == 'current' and '(bytes)' in raw:
            entry['bytes_current'] = raw.strip().split('(bytes)')[0].strip()
        elif section == 'current' and parts and parts[0] == 'add':
            tokens = []
            for part in parts[1:]:
                if part == 'use':
                    break
                tokens.append(part)
            if tokens:
                entry['addtime_created'] = ' '.join(tokens)
    if entry is not None:
        records.append(entry)
    return records


if __name__ == '__main__':
    result = {'records': []}
    try:
        payload = subprocess.run(
            ['/usr/sbin/ip', '-s', 'xfrm', 'state'], capture_output=True, text=True
        ).stdout
    except FileNotFoundError:
        payload = ''

    for entry in parse_states(payload):
        entry['id'] = hashlib.md5(
            ("%(src)s-%(dst)s-%(satype)s-%(spi)s" % entry).encode()
        ).hexdigest()
        for key in list(entry.keys()):
            value = entry[key]
            if value == '':
                entry[key] = None
            elif isinstance(value, str) and value.isdigit() and key not in ['spi']:
                entry[key] = int(value)
        result['records'].append(entry)

    print(ujson.dumps(result))
