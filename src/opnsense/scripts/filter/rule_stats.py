#!/usr/bin/python3

"""
    Copyright (c) 2019-2025 Ad Schellevis <ad@opnsense.org>
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

"""

import ujson
import subprocess


def collect_rule_stats():
    """ Aggregate per-rule packet/byte counters from the nftables ruleset.

        The MurOS ruleset generator (nft_build.php) attaches a `counter` and a
        `comment` carrying the rule uuid to every rule it emits. A single GUI
        rule can expand to several nft rules (e.g. an inet46 rule produces one
        line per family), so counters sharing the same uuid are summed.

        nftables exposes packet and byte counters but no separate "evaluations"
        or live "states" metric like pf did. We map evaluations to the matched
        packet count and report states as 0, which keeps the GUI columns
        populated with the information actually available on Linux.
    """
    results = dict()
    sp = subprocess.run(['/usr/sbin/nft', '-j', 'list', 'ruleset'], capture_output=True, text=True)
    try:
        ruleset = ujson.loads(sp.stdout)
    except ValueError:
        return results

    for item in ruleset.get('nftables', []):
        rule = item.get('rule') if isinstance(item, dict) else None
        if not rule:
            continue
        uuid = rule.get('comment')
        if not uuid:
            continue
        packets = 0
        nbytes = 0
        found = False
        for expr in rule.get('expr', []):
            if isinstance(expr, dict) and 'counter' in expr:
                counter = expr['counter']
                packets += int(counter.get('packets', 0))
                nbytes += int(counter.get('bytes', 0))
                found = True
        if not found:
            continue
        if uuid not in results:
            results[uuid] = {'evaluations': 0, 'packets': 0, 'bytes': 0, 'states': 0}
        results[uuid]['packets'] += packets
        results[uuid]['evaluations'] += packets
        results[uuid]['bytes'] += nbytes

    return results


if __name__ == '__main__':
    print(ujson.dumps(collect_rule_stats()))
