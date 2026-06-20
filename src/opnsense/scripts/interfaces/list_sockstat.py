#!/usr/bin/python3

"""
    Copyright (c) 2020 Ad Schellevis <ad@opnsense.org>
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
    dump sockstat
"""
import os
import pwd
import re
import subprocess
import ujson

# matches each ("command",pid=N,fd=M) entry of the ss process column
PROC_RE = re.compile(r'\("([^"]*)",pid=(\d+),fd=(\d+)\)')


def owner(pid):
    """ resolve the user owning a pid through procfs (ss only reports the
        process name, pid and fd, not the user the old sockstat showed) """
    try:
        return pwd.getpwuid(os.stat('/proc/%s' % pid).st_uid).pw_name
    except (OSError, KeyError, ValueError):
        return ''


if __name__ == '__main__':
    result = []
    # -t tcp, -u udp, -n numeric, -a all states, -p processes, -H no header.
    # ss is the iproute2 replacement for sockstat + netstat -anL. Unix domain
    # sockets are intentionally left out: their ss layout carries extra inode
    # columns and their addressing has no useful equivalent in this firewall
    # diagnostics view, which is concerned with IP sockets.
    sp = subprocess.run(['/usr/bin/ss', '-tunapH'], capture_output=True, text=True)
    for line in sp.stdout.split('\n'):
        parts = line.split()
        if len(parts) < 6:
            continue
        netid, state, recvq, sendq, local, peer = parts[:6]
        process = parts[6] if len(parts) > 6 else ''

        # listening sockets expose their accept/backlog queue depth through
        # the Recv-Q (pending) and Send-Q (configured backlog) columns
        queue = None
        if state in ('LISTEN', 'UNCONN'):
            queue = {'qlen': recvq, 'incqlen': '0', 'maxqlen': sendq}

        matches = PROC_RE.findall(process)
        if not matches:
            matches = [('', '0', '0')]
        for command, pid, fd in matches:
            record = {
                'user': owner(pid) if pid != '0' else '',
                'command': command,
                'pid': pid,
                'fd': fd,
                'proto': netid,
                'local': local,
                'remote': '' if peer in ('*', '0.0.0.0:*', '[::]:*', '*:*') else peer,
            }
            if queue is not None:
                record['listen-queue-sizes'] = queue
            result.append(record)
    print(ujson.dumps(result))
