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
    Bpf tab of Diagnostics: Network Insight: netstat.

    `netstat -B` listed the bpf(4) descriptors of FreeBSD, which is how every
    sniffer attached to an interface. The Linux equivalent of those descriptors
    are the packet sockets of /proc/net/packet, used by tcpdump, by the packet
    capture page and by the host discovery daemon, so they are listed here with
    the process holding them, resolved from the socket inode in /proc.
"""
import glob
import json
import os


def interface_names():
    result = {}
    for path in glob.glob('/sys/class/net/*/ifindex'):
        try:
            with open(path) as fh:
                result[int(fh.read().strip())] = path.split('/')[-2]
        except (OSError, ValueError):
            continue
    return result


def socket_owners():
    result = {}
    for path in glob.glob('/proc/[0-9]*/fd/*'):
        try:
            target = os.readlink(path)
        except OSError:
            continue
        if not target.startswith('socket:['):
            continue
        inode = target[8:-1]
        if inode in result:
            continue
        pid = path.split('/')[2]
        try:
            with open('/proc/%s/comm' % pid) as fh:
                name = fh.read().strip()
        except OSError:
            name = ''
        result[inode] = {'pid': int(pid), 'process': name}
    return result


if __name__ == '__main__':
    devices = interface_names()
    owners = socket_owners()
    entries = []

    try:
        with open('/proc/net/packet') as fh:
            lines = fh.read().strip().split('\n')[1:]
    except Exception:
        lines = []

    for line in lines:
        fields = line.split()
        if len(fields) < 9:
            continue
        owner = owners.get(fields[8], {'pid': 0, 'process': ''})
        entries.append({
            'pid': owner['pid'],
            'process': owner['process'],
            'interface': devices.get(int(fields[4]), 'any' if fields[4] == '0' else fields[4]),
            'protocol': '0x%s' % fields[3].lower(),
            'socket-type': int(fields[2]),
            'reference-count': int(fields[1]),
            'ring': int(fields[5]),
            'receive-memory': int(fields[6]),
            'user': int(fields[7]),
            'inode': int(fields[8]),
        })

    print(json.dumps({'bpf-statistics': {
        'peer-count': len(entries),
        'bpf-entry': entries,
    }}))
