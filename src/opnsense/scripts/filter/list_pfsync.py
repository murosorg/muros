#!/usr/bin/python3

"""
    Copyright (c) 2015-2022 Ad Schellevis <ad@opnsense.org>
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
    List state-synchronisation peers.

    On FreeBSD this read the pf state table through pfctl, where every synced
    state carried the creator id of the node that owned it (pfsync). On Debian
    the equivalent is conntrackd: peers exchange connection tracking state and
    `conntrackd -s` reports the number of entries received from each peer.
    When conntrackd is not installed or not configured (the default, no high
    availability), the node list is simply empty. The local host id is derived
    from the stable machine id so the GUI can still flag the local node.
"""
import os
import shutil
import subprocess
import ujson


def local_hostid():
    """ stable per-host identifier, mirroring the old pf 'Hostid' field """
    for path in ('/etc/hostid', '/etc/machine-id'):
        try:
            with open(path) as fh:
                value = fh.read().strip()
                if value:
                    return value[:8]
        except OSError:
            continue
    return None


def conntrackd_nodes():
    """ parse `conntrackd -s` for synchronisation peers, if available """
    nodes = []
    binary = shutil.which('conntrackd')
    if binary is None or not os.path.exists('/etc/conntrackd/conntrackd.conf'):
        return nodes
    try:
        sp = subprocess.run([binary, '-s'], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return nodes
    # conntrackd reports a 'cache external' / per-peer section; expose any peer
    # line carrying an address as a node so the GUI can list the cluster.
    for line in sp.stdout.split('\n'):
        token = line.strip()
        if token.lower().startswith('peer') and '=' in token:
            addr = token.split('=', 1)[1].strip()
            if addr:
                nodes.append({'creatorid': addr, 'this': 0})
    return nodes


if __name__ == '__main__':
    result = {'hostid': local_hostid(), 'nodes': conntrackd_nodes()}
    print(ujson.dumps(result))
