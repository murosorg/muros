#!/usr/bin/python3

"""
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
    forget what the firewall remembers about the sources it is counting

    pf kept a source tracking node per address for the rules carrying a per
    source limit, and the GUI could throw them away. The port had no equivalent
    and the action was wired to a command that does nothing. The equivalent
    exists now: those counters live in the dynamic sets the rules add to, one
    per rule that carries a limit, so emptying them starts the counting over.

    The tables offenders are added to are left alone: they are a decision the
    firewall took, not a counter, and they are emptied from the alias page.
"""
import json
import subprocess
import sys

NFT = '/usr/sbin/nft'
TABLE = 'muros'
PREFIXES = ('srcconn_', 'srcrate_')


def sets_to_flush():
    """ the dynamic sets holding the per source counters of the rules """
    try:
        output = subprocess.run(
            [NFT, '-j', 'list', 'sets', 'inet'], capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        return []

    if output.returncode != 0:
        return []

    try:
        payload = json.loads(output.stdout)
    except ValueError:
        return []

    found = []
    for item in payload.get('nftables', []):
        definition = item.get('set')
        if not definition or definition.get('table') != TABLE:
            continue
        name = definition.get('name', '')
        if name.startswith(PREFIXES):
            found.append(name)

    return found


if __name__ == '__main__':
    flushed = 0
    for set_name in sets_to_flush():
        try:
            result = subprocess.run(
                [NFT, 'flush', 'set', 'inet', TABLE, set_name],
                capture_output=True, text=True, timeout=30
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if result.returncode == 0:
            flushed += 1

    print('flushed the source counters of %d rule%s' % (flushed, '' if flushed == 1 else 's'))
    sys.exit(0)
