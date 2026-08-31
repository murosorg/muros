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
    Network buffer usage, the Linux answer to `netstat -m`.

    FreeBSD allocated every packet from the mbuf and mbuf cluster zones, and
    both the Memory tab of Diagnostics: Network Insight: netstat and the Mbuf
    dashboard widget read that zone usage. Linux has no mbuf zone: packets live
    in socket buffers taken from the skbuff slab caches, and what the kernel
    actually caps is the memory charged to the sockets, in pages, through the
    tcp_mem and udp_mem pressure limits.

    Those pages are reported as the cluster figures the widget draws its gauge
    from, so it shows how far the box is from the point where the kernel starts
    reclaiming socket memory, and the skbuff caches are reported as the mbuf
    figures next to the per protocol detail of /proc/net/sockstat.
"""
import json
import os

PAGE_SIZE = os.sysconf('SC_PAGE_SIZE')
SKB_CACHES = ('skbuff_head_cache', 'skbuff_fclone_cache', 'skbuff_small_head', 'skbuff_ext_cache')


def read_values(filename):
    try:
        with open(filename) as fh:
            return [int(value) for value in fh.read().split()]
    except (OSError, ValueError):
        return []


def sockstat():
    result = {}
    for filename in ('/proc/net/sockstat', '/proc/net/sockstat6'):
        try:
            with open(filename) as fh:
                lines = fh.read().strip().split('\n')
        except OSError:
            continue
        for line in lines:
            name, _, remainder = line.partition(':')
            fields = remainder.split()
            entry = {}
            for offset in range(0, len(fields) - 1, 2):
                try:
                    entry[fields[offset]] = int(fields[offset + 1])
                except ValueError:
                    continue
            if entry:
                result[name.strip().lower()] = entry
    return result


def slab_caches():
    caches = []
    try:
        with open('/proc/slabinfo') as fh:
            lines = fh.read().strip().split('\n')
    except OSError:
        return caches
    for line in lines:
        fields = line.split()
        if len(fields) < 4 or fields[0] not in SKB_CACHES:
            continue
        try:
            caches.append({
                'name': fields[0],
                'used': int(fields[1]),
                'total': int(fields[2]),
                'size': int(fields[3]),
            })
        except ValueError:
            continue
    return caches


if __name__ == '__main__':
    protocols = sockstat()
    caches = slab_caches()

    charged = 0
    for name in ('tcp', 'udp'):
        charged += protocols.get(name, {}).get('mem', 0)
    for name in ('frag', 'frag6'):
        charged += protocols.get(name, {}).get('memory', 0) // PAGE_SIZE

    limit = 0
    for filename in ('/proc/sys/net/ipv4/tcp_mem', '/proc/sys/net/ipv4/udp_mem'):
        values = read_values(filename)
        if len(values) == 3:
            limit += values[2]

    used = sum(cache['used'] for cache in caches)
    total = sum(cache['total'] for cache in caches)

    print(json.dumps({'mbuf-statistics': {
        'mbuf-current': used,
        'mbuf-cache': total - used,
        'mbuf-total': total,
        'cluster-current': charged,
        'cluster-cache': 0,
        'cluster-total': charged,
        'cluster-max': limit,
        'cluster-size': PAGE_SIZE,
        'cluster-bytes': charged * PAGE_SIZE,
        'cluster-max-bytes': limit * PAGE_SIZE,
        'sockets-used': protocols.get('sockets', {}).get('used', 0),
        'protocol': protocols,
        'cache': caches,
    }}))
