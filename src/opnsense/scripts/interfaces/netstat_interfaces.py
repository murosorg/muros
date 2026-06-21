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
    per-interface counters in the structure historically produced by
    `netstat -i -b -d --libxo json` on FreeBSD (Debian / procfs).
"""
import json
import os


def mac_of(name):
    try:
        with open('/sys/class/net/%s/address' % name) as fh:
            return fh.read().strip()
    except Exception:
        return '00:00:00:00:00:00'


def mtu_of(name):
    try:
        with open('/sys/class/net/%s/mtu' % name) as fh:
            return fh.read().strip()
    except Exception:
        return ''


if __name__ == '__main__':
    interfaces = []
    idx = 0
    try:
        with open('/proc/net/dev') as fh:
            lines = fh.read().strip().split('\n')[2:]
    except Exception:
        lines = []
    for line in lines:
        name, _, rest = line.partition(':')
        name = name.strip()
        cols = rest.split()
        if not name or len(cols) < 16:
            continue
        idx += 1
        interfaces.append({
            'name': name,
            'flags': '',
            'mtu': mtu_of(name),
            'network': '<Link#%d>' % idx,
            'address': mac_of(name),
            'received-packets': cols[1],
            'received-errors': cols[2],
            'dropped-packets': cols[3],
            'received-bytes': cols[0],
            'sent-packets': cols[9],
            'send-errors': cols[10],
            'sent-bytes': cols[8],
            'collisions': cols[13],
        })
    print(json.dumps({'statistics': {'interface': interfaces}}))
