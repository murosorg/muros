#!/usr/local/bin/python3

"""
    Copyright (c) 2023 Ad Schellevis <ad@opnsense.org>
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
#
# MurOS: this reporter used to read the FreeBSD pf state table through
# pfctl(8). On Debian the data plane is netfilter, so the same five datasets
# (state insert/remove rate, number of states, NAT states, distinct source
# and destination addresses) are derived from the netfilter connection
# tracking table via conntrack(8) and /proc/net/stat/nf_conntrack. The output
# format is unchanged: "pfrate:pfstates:pfnat:srcip:dstip".
import subprocess
import time

CONNTRACK = '/usr/sbin/conntrack'
NF_STAT = '/proc/net/stat/nf_conntrack'


def conntrack_count():
    """ Total number of tracked connections (cheap kernel-side count). """
    try:
        sp = subprocess.run([CONNTRACK, '-C'], capture_output=True, text=True)
        return int(sp.stdout.strip() or 0)
    except (OSError, ValueError):
        return 0


def iter_entries():
    """ Yield (orig, reply) address dicts for every tracked connection.

    A line printed by `conntrack -L -o extended,id` carries the original
    direction tuple first, then the reply direction; both start with their own
    src= token, so the second src= switches the parser to the reply side. """
    try:
        sp = subprocess.run([CONNTRACK, '-L', '-o', 'extended,id'], capture_output=True, text=True)
    except OSError:
        return
    for line in sp.stdout.strip().split('\n'):
        parts = line.split()
        if len(parts) < 5 or parts[0] not in ('ipv4', 'ipv6'):
            continue
        orig = {}
        reply = {}
        target = orig
        seen_src = 0
        for token in parts:
            if '=' not in token:
                continue
            key, value = token.split('=', 1)
            if key == 'src':
                seen_src += 1
                target = orig if seen_src == 1 else reply
            if key in ('src', 'dst'):
                target[key] = value
        yield orig, reply


def state_rate():
    """ Approximate the pf insert/removal rate from the netfilter insert and
    delete counters. Two short-spaced samples give a per-second delta. Recent
    kernels zero these columns; the rate then reads 0, which is harmless. """
    def sample():
        total = 0
        try:
            with open(NF_STAT) as fh:
                lines = fh.read().strip().split('\n')
        except OSError:
            return None
        if len(lines) < 2:
            return None
        header = lines[0].split()
        cols = [i for i, name in enumerate(header) if name in ('insert', 'delete')]
        if not cols:
            return None
        for row in lines[1:]:
            fields = row.split()
            for i in cols:
                if i < len(fields):
                    try:
                        total += int(fields[i], 16)
                    except ValueError:
                        pass
        return total

    first = sample()
    if first is None:
        return 0.0
    time.sleep(1)
    second = sample()
    if second is None or second < first:
        return 0.0
    return float(second - first)


if __name__ == '__main__':
    result = {'pfrate': 0.0, 'pfstates': 0, 'pfnat': 0, 'srcip': 0, 'dstip': 0}

    src_ips = set()
    dst_ips = set()
    states = 0
    for orig, reply in iter_entries():
        states += 1
        src = orig.get('src')
        dst = orig.get('dst')
        if src:
            src_ips.add(src)
        if dst:
            dst_ips.add(dst)
        # NAT is in effect when the address the peer replies to (or from)
        # differs from the real original endpoint: SNAT changes reply.dst,
        # DNAT changes reply.src.
        if (reply.get('dst') and src and reply['dst'] != src) or \
           (reply.get('src') and dst and reply['src'] != dst):
            result['pfnat'] += 1

    result['pfstates'] = states or conntrack_count()
    result['srcip'] = len(src_ips)
    result['dstip'] = len(dst_ips)
    result['pfrate'] = state_rate()

    print('%(pfrate)0.1f:%(pfstates)d:%(pfnat)d:%(srcip)d:%(dstip)d' % result, end='')
