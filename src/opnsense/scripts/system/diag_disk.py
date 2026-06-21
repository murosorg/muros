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
    report filesystem usage in the structure historically produced by
    `df --libxo json` on FreeBSD (Debian / coreutils df).
"""
import json
import os
import subprocess


def with_unit(value):
    """coreutils df -h prints a bare "0" for an empty column, while the
    FreeBSD libxo output this structure mimics always carries a unit (e.g.
    "0B"). Append a byte unit to unit-less numbers so downstream consumers
    that parse a "<number><unit>" pair keep working."""
    if value and value[-1].isdigit():
        return value + 'B'
    return value


if __name__ == '__main__':
    filesystems = []
    env = dict(os.environ, LC_ALL='C', LANG='C')
    sp = subprocess.run(['/bin/df', '-h', '-T', '-P'], capture_output=True, text=True, env=env)
    for line in sp.stdout.strip().split('\n')[1:]:
        parts = line.split(None, 6)
        if len(parts) < 7:
            continue
        filesystems.append({
            'name': parts[0],
            'type': parts[1],
            'blocks': with_unit(parts[2]),
            'used': with_unit(parts[3]),
            'available': with_unit(parts[4]),
            'used-percent': parts[5].rstrip('%'),
            'mounted-on': parts[6],
        })
    print(json.dumps({'storage-system-information': {'filesystem': filesystems}}))
