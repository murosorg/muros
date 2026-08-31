#!/usr/bin/python3

"""
    Copyright (c) 2026 Ecritel
    All rights reserved.

    Kernel memory usage, in the shape the diagnostics API expects.

    FreeBSD answered this with "vmstat -m -z --libxo json": the malloc types and
    the uma zones of the kernel. On Linux the same information lives in
    /proc/meminfo for the coarse kernel allocations and in /proc/slabinfo for the
    per cache detail, so both are mapped onto the two sections the API reads,
    keeping the field names it already sums.
"""

import os

import ujson

MEMINFO = '/proc/meminfo'
SLABINFO = '/proc/slabinfo'

# the kernel allocations of /proc/meminfo, as malloc types
MALLOC_TYPES = [
    'Slab',
    'SReclaimable',
    'SUnreclaim',
    'KernelStack',
    'PageTables',
    'VmallocUsed',
    'Percpu',
]


def malloc_statistics():
    """ coarse kernel allocations, reported in kilobytes like /proc/meminfo
    """
    result = []

    if not os.path.isfile(MEMINFO):
        return result

    with open(MEMINFO) as handle:
        for line in handle:
            parts = line.split(':', 1)
            if len(parts) != 2 or parts[0] not in MALLOC_TYPES:
                continue
            value = parts[1].strip().split()
            if not value or not value[0].isdigit():
                continue
            result.append({
                'type': parts[0],
                'memory-use': int(value[0]),
                'requests': 0,
            })

    return result


def zone_statistics():
    """ per cache detail, the closest equivalent of the uma zones
    """
    result = []

    if not os.access(SLABINFO, os.R_OK):
        return result

    pagesize = os.sysconf('SC_PAGE_SIZE')

    with open(SLABINFO) as handle:
        for line in handle:
            if line.startswith('#') or line.startswith('slabinfo'):
                continue
            fields = line.split()
            if len(fields) < 6 or not fields[1].isdigit():
                continue
            name, active, total, size, per_slab, pages_per_slab = fields[:6]
            slabs = int(fields[14]) if len(fields) > 14 and fields[14].isdigit() else 0
            result.append({
                'name': name,
                'size': int(size),
                'limit': 0,
                'used': int(active),
                'free': int(total) - int(active),
                'memory-use': int(slabs) * int(pages_per_slab) * pagesize // 1024,
            })

    return result


if __name__ == '__main__':
    print(ujson.dumps({
        'malloc-statistics': {'memory': malloc_statistics()},
        'memory-zone-statistics': {'zone': zone_statistics()},
    }))
