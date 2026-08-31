#!/usr/bin/python3

"""
    Copyright (c) 2026 MurOS
    All rights reserved.

    Redistribution and use in source and binary forms, with or without
    modification, are permitted provided that the following conditions are met:

    1. Redistributions of source code must retain the above copyright notice,
     this list of conditions and the following disclaimer.
    2. Redistributions in binary form must reproduce the above copyright notice.

    THIS SOFTWARE IS PROVIDED ``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES.
    --------------------------------------------------------------------------------------
    Netisr tab of Diagnostics: Network Insight: netstat.

    FreeBSD dispatched received packets through netisr(9) worker threads and
    `netstat -Q` reported one workstream per CPU. Linux does the same work in
    the softirq receive path and accounts it per CPU in /proc/net/softnet_stat,
    so that file is reported here in the shape the tab expects: one entry per
    CPU, plus the totals the FreeBSD output carried in its summary.
"""
import json

COLUMNS = [
    'processed',
    'dropped',
    'time-squeezed',
    None,
    None,
    None,
    None,
    None,
    'cpu-collisions',
    'received-rps',
    'flow-limit-count',
    'backlog-length',
    'index',
    'input-queue-length',
    'process-queue-length',
]


if __name__ == '__main__':
    workstreams = []
    totals = {'processed': 0, 'dropped': 0, 'time-squeezed': 0}

    try:
        with open('/proc/net/softnet_stat') as fh:
            lines = fh.read().strip().split('\n')
    except Exception:
        lines = []

    for cpu, line in enumerate(lines):
        fields = line.split()
        if not fields:
            continue
        entry = {'cpu': cpu}
        for offset, name in enumerate(COLUMNS):
            if name is None or offset >= len(fields):
                continue
            try:
                entry[name] = int(fields[offset], 16)
            except ValueError:
                continue
        for name in totals:
            totals[name] += entry.get(name, 0)
        workstreams.append(entry)

    print(json.dumps({'netisr-statistics': {
        'workstream-count': len(workstreams),
        'total': totals,
        'workstream': workstreams,
    }}))
