#!/usr/bin/python3

"""
    Copyright (c) 2016 Ad Schellevis <ad@opnsense.org>
    Copyright (c) 2026 MurOS
    All rights reserved.

    Redistribution and use in source and binary forms, with or without
    modification, are permitted provided that the following conditions are met:

    1. Redistributions of source code must retain the above copyright notice,
     this list of conditions and the following disclaimer.

    2. Redistributions in binary form must reproduce the above copyright
     notice, this list of conditions and the following disclaimer in the
     documentation and/or other materials provided with the distribution.

    THIS SOFTWARE IS PROVIDED ``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES.
    --------------------------------------------------------------------------------------
    report system activity (process list), Debian / procps
"""
import json
import subprocess
import sys


def human(kb):
    try:
        value = float(kb) * 1024.0
    except Exception:
        return str(kb)
    for unit in ['B', 'K', 'M', 'G', 'T']:
        if value < 1024.0:
            return ('%d%s' % (int(value), unit)) if unit == 'B' else ('%.1f%s' % (value, unit))
        value /= 1024.0
    return '%.1fP' % value


def headers():
    out = []
    try:
        with open('/proc/loadavg') as fh:
            la = fh.read().split()
        with open('/proc/uptime') as fh:
            up = float(fh.read().split()[0])
        days = int(up // 86400)
        hrs = int((up % 86400) // 3600)
        mins = int((up % 3600) // 60)
        out.append('load averages: %s, %s, %s  up %dd %02d:%02d' % (la[0], la[1], la[2], days, hrs, mins))
    except Exception:
        pass
    try:
        mem = {}
        with open('/proc/meminfo') as fh:
            for line in fh:
                key, _, val = line.partition(':')
                mem[key] = val.strip()
        out.append('Mem: %s total, %s free, %s available' % (
            mem.get('MemTotal', ''), mem.get('MemFree', ''), mem.get('MemAvailable', '')))
        out.append('Swap: %s total, %s free' % (mem.get('SwapTotal', ''), mem.get('SwapFree', '')))
    except Exception:
        pass
    return out


if __name__ == '__main__':
    result = {'headers': headers(), 'details': []}
    fields = ['tid', 'pid', 'user:32', 'pri', 'ni', 'vsz', 'rss', 'stat', 'psr', 'time', 'pcpu', 'comm']
    sp = subprocess.run(['/usr/bin/ps', '-eL', '-o', ','.join(fields), '--sort=-pcpu'],
                        capture_output=True, text=True)
    for line in sp.stdout.strip().split('\n')[1:]:
        parts = line.split(None, 11)
        if len(parts) < 12:
            continue
        result['details'].append({
            'THR': parts[0],
            'PID': parts[1],
            'USERNAME': parts[2],
            'PRI': parts[3],
            'NICE': parts[4],
            'SIZE': human(parts[5]),
            'RES': human(parts[6]),
            'STATE': parts[7],
            'C': parts[8],
            'TIME': parts[9],
            'WCPU': parts[10] + '%',
            'COMMAND': parts[11],
        })

    if len(sys.argv) > 1 and sys.argv[1] == 'json':
        print(json.dumps(result))
    else:
        for header_line in result['headers']:
            print(header_line)
        print()
        for detail in result['details']:
            print(detail)
