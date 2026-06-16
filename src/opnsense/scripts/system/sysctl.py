#!/usr/bin/python3

"""
    Copyright (c) 2021-2022 Franco Fichtner <franco@opnsense.org>
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
    return current kernel information (Debian / procfs + sysctl)

    On FreeBSD this wrapped sysctl(8) and its BSD-only OIDs. On Debian we emulate
    the handful of BSD OIDs the GUI relies on (kern.boottime, vm.loadavg, ...) from
    /proc and fall back to the Linux sysctl for dotted Linux keys.
"""

import argparse
import datetime
import json
import os
import subprocess
import sys
import time

_cache_filename = "/tmp/sysctl_map.cache"


def emulated_oids():
    vals = {}
    try:
        with open('/proc/uptime') as fh:
            uptime = float(fh.read().split()[0])
        sec = int(time.time() - uptime)
        when = datetime.datetime.fromtimestamp(sec).strftime('%a %b %e %H:%M:%S %Y')
        vals['kern.boottime'] = '{ sec = %d, usec = 0 } %s' % (sec, when)
    except Exception:
        pass
    try:
        with open('/proc/loadavg') as fh:
            la = fh.read().split()
        vals['vm.loadavg'] = '{ %s %s %s }' % (la[0], la[1], la[2])
    except Exception:
        pass
    try:
        with open('/proc/meminfo') as fh:
            for line in fh:
                if line.startswith('MemTotal:'):
                    nbytes = int(line.split()[1]) * 1024
                    vals['hw.physmem'] = str(nbytes)
                    vals['hw.realmem'] = str(nbytes)
                    break
    except Exception:
        pass
    try:
        ncpu = os.cpu_count() or 1
        vals['hw.ncpu'] = str(ncpu)
        vals['kern.smp.cpus'] = str(ncpu)
    except Exception:
        pass
    return vals


def linux_sysctl(names):
    out = {}
    if not names:
        return out
    sp = subprocess.run(['/usr/sbin/sysctl', '-e'] + names, capture_output=True, text=True)
    for line in sp.stdout.split("\n"):
        parts = line.strip().split(' = ', 1)
        if len(parts) > 1:
            out[parts[0].strip()] = parts[1].strip()
    return out


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--gather', help='gather sysctl info', action='store_true')
    parser.add_argument('--values', help='comma-separated list of sysctl values to fetch')
    inputargs = parser.parse_args()

    output = '{}'

    if inputargs.values:
        emap = emulated_oids()
        result = {}
        missing = []
        for param in inputargs.values.split(','):
            param = param.strip()
            if param in emap:
                result[param] = emap[param]
            else:
                missing.append(param)
        result.update(linux_sysctl(missing))
        output = json.dumps(result)
    elif inputargs.gather:
        if os.path.exists(_cache_filename):
            with open(_cache_filename, 'r') as fh:
                print(fh.read())
            sys.exit(0)

        result = {}
        for name, value in emulated_oids().items():
            result[name] = {'name': name, 'value': value, 'type': 'r', 'description': ''}
        sp = subprocess.run(['/usr/sbin/sysctl', '-a'], capture_output=True, text=True)
        for line in sp.stdout.split("\n"):
            parts = line.strip().split(' = ', 1)
            if len(parts) > 1:
                name = parts[0].strip()
                result[name] = {'name': name, 'value': parts[1].strip(), 'type': 'w', 'description': ''}
        output = json.dumps(result)
        with open(_cache_filename, 'w') as fh:
            fh.write(output)

    print(output)
