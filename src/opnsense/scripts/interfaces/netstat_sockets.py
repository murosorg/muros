#!/usr/bin/python3

"""
    Copyright (c) 2024 MurOS
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
    Report the system-wide socket table for the Diagnostics socket page.

    The FreeBSD original produced this list with `netstat -na --libxo json`,
    yielding a statistics.socket array. On Debian the equivalent data comes
    from iproute2 `ss`, so the same structure is rebuilt here: IP sockets carry
    a protocol plus local/remote address+port, UNIX domain sockets carry a
    type and path. The Diagnostics controller uses this list as the base set
    and enriches it with the owning process from `dump sockstat`.
"""
import subprocess
import ujson

SS = '/usr/bin/ss'
UNIX_TYPE = {'u_str': 'stream', 'u_dgr': 'dgram', 'u_seq': 'seqpacket'}
WILDCARD = ('*', '*:*', '0.0.0.0:*', '[::]:*', ':::*')


def split_hostport(value):
    """ Split an ss endpoint into (address, port). ss prints IPv6 hosts in
        brackets and uses the last colon as the port separator. """
    if not value or value == '*':
        return '*', '*'
    host, sep, port = value.rpartition(':')
    if not sep:
        return value, '*'
    if host.startswith('[') and host.endswith(']'):
        host = host[1:-1]
    return host or '*', port or '*'


def ip_sockets():
    """ tcp and udp sockets in every state, numeric, no header line """
    sockets = []
    try:
        sp = subprocess.run([SS, '-tunaH'], capture_output=True, text=True)
    except OSError:
        return sockets
    for line in sp.stdout.split('\n'):
        parts = line.split()
        if len(parts) < 5:
            continue
        netid, state, _recvq, _sendq, local = parts[:5]
        peer = parts[5] if len(parts) > 5 else '*'
        laddr, lport = split_hostport(local)
        raddr, rport = split_hostport(peer)
        sockets.append({
            'protocol': netid,
            'state': state,
            'local': {'address': laddr, 'port': lport},
            'remote': {
                'address': '' if peer in WILDCARD else raddr,
                'port': '' if peer in WILDCARD else rport,
            },
        })
    return sockets


def unix_sockets():
    """ unix domain sockets; the path lives in the local-address column """
    sockets = []
    try:
        sp = subprocess.run([SS, '-xaH'], capture_output=True, text=True)
    except OSError:
        return sockets
    for line in sp.stdout.split('\n'):
        parts = line.split()
        if len(parts) < 6:
            continue
        netid, _state, _recvq, _sendq, local, inode = parts[:6]
        path = '' if local in ('*', '') else local
        sockets.append({
            'type': UNIX_TYPE.get(netid, netid),
            'path': path,
            'address': inode,
        })
    return sockets


if __name__ == '__main__':
    result = {'statistics': {'socket': ip_sockets() + unix_sockets()}}
    print(ujson.dumps(result))
