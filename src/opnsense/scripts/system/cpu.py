#!/usr/bin/python3

"""
    Copyright (c) 2024 Deciso B.V.
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
    streams cpu usage (Linux, derived from /proc/stat)
"""

import argparse
import json
import time


def read_cpu():
    """return the aggregate cpu time counters from /proc/stat"""
    with open('/proc/stat') as handle:
        for line in handle:
            if line.startswith('cpu '):
                return [int(x) for x in line.split()[1:]]
    return []


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--interval', help='poll interval', default='1')
    inputargs = parser.parse_args()
    interval = float(inputargs.interval)

    # counters: user nice system idle iowait irq softirq steal guest guest_nice
    previous = read_cpu()
    while True:
        time.sleep(interval)
        current = read_cpu()
        if not current or not previous or len(current) != len(previous):
            previous = current
            continue
        deltas = [c - p for c, p in zip(current, previous)]
        previous = current
        total = sum(deltas)
        if total <= 0:
            continue
        user = deltas[0]
        nice = deltas[1]
        system = deltas[2]
        idle = deltas[3] + (deltas[4] if len(deltas) > 4 else 0)
        intr = (deltas[5] if len(deltas) > 5 else 0) + (deltas[6] if len(deltas) > 6 else 0)
        result = {
            'total': round((total - idle) * 100.0 / total),
            'user': round(user * 100.0 / total),
            'nice': round(nice * 100.0 / total),
            'sys': round(system * 100.0 / total),
            'intr': round(intr * 100.0 / total),
            'idle': round(idle * 100.0 / total)
        }
        print(f"event: message\ndata: {json.dumps(result)}\n\n", flush=True)
