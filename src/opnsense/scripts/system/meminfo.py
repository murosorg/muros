#!/usr/bin/python3

"""
    Copyright (c) 2026 MurOS
    All rights reserved.

    Redistribution and use in source and binary forms, with or without
    modification, are permitted provided that the following conditions are met:

    1. Redistributions of source code must retain the above copyright notice,
     this list of conditions and the following disclaimer.

    2. Redistributions in binary form must reproduce the above copyright notice,
     this list of conditions and the following disclaimer in the documentation
     and/or other materials provided with the distribution.

    THIS SOFTWARE IS PROVIDED ``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES
    ARE DISCLAIMED. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY DAMAGE.
    --------------------------------------------------------------------------------------
    report physical memory usage (Linux, read from /proc/meminfo)
"""
import json


def read_meminfo():
    values = {}
    with open('/proc/meminfo') as handle:
        for line in handle:
            key, _, rest = line.partition(':')
            parts = rest.split()
            if parts:
                # values in /proc/meminfo are reported in kB
                values[key.strip()] = int(parts[0]) * 1024
    return values


if __name__ == '__main__':
    result = {}
    try:
        info = read_meminfo()
        total = info.get('MemTotal', 0)
        # MemAvailable is the kernel estimate of memory available for new work
        # without swapping; fall back to MemFree on very old kernels.
        available = info.get('MemAvailable', info.get('MemFree', 0))
        used = total - available
        if used < 0:
            used = 0
        result = {
            'total': total,
            'used': used,
            'available': available,
        }
    except (OSError, ValueError):
        result = {}
    print(json.dumps(result))
