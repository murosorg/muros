#!/usr/bin/python3

"""
    Copyright (c) 2021 Ad Schellevis <ad@opnsense.org>
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

    --------------------------------------------------------------------------------------
    MurOS: report firewall engine statistics from the Linux netfilter stack.

    The FreeBSD pf engine exposed these numbers through `pfctl -s*`. On Debian
    the stateful firewall is driven by nftables and connection tracking
    (nf_conntrack), so the figures that have a direct equivalent are sourced
    from procfs:
      - the state table maps to the conntrack table (count / max),
      - state timeouts map to the nf_conntrack_* timeout sysctls,
      - uptime comes from /proc/uptime.

    The pf per-interface and per-rule packet/byte accounting has no built-in
    netfilter counterpart (it would require explicit named counters in the
    ruleset), so those sections are returned empty rather than fabricated.
"""
import sys
import ujson

CONNTRACK_DIR = '/proc/sys/net/netfilter'

# nf_conntrack timeout sysctl -> readable key mirroring the old pf naming
TIMEOUT_MAP = {
    'nf_conntrack_generic_timeout': 'generic',
    'nf_conntrack_tcp_timeout_syn_sent': 'tcp.first',
    'nf_conntrack_tcp_timeout_syn_recv': 'tcp.opening',
    'nf_conntrack_tcp_timeout_established': 'tcp.established',
    'nf_conntrack_tcp_timeout_fin_wait': 'tcp.closing',
    'nf_conntrack_tcp_timeout_close_wait': 'tcp.finwait',
    'nf_conntrack_tcp_timeout_close': 'tcp.closed',
    'nf_conntrack_udp_timeout': 'udp.single',
    'nf_conntrack_udp_timeout_stream': 'udp.multiple',
    'nf_conntrack_icmp_timeout': 'icmp.first',
}


def read_int(path):
    try:
        with open(path) as handle:
            return int(handle.read().strip())
    except (OSError, ValueError):
        return None


def uptime():
    try:
        with open('/proc/uptime') as handle:
            seconds = int(float(handle.read().split()[0]))
    except (OSError, ValueError, IndexError):
        return ''
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    return '%dd %02d:%02d:%02d' % (days, hours, minutes, secs)


def info():
    result = {'uptime': uptime(), 'state-table': {}}
    current = read_int('%s/nf_conntrack_count' % CONNTRACK_DIR)
    maximum = read_int('%s/nf_conntrack_max' % CONNTRACK_DIR)
    if current is not None:
        result['state-table']['current-entries'] = {'total': current}
    if maximum is not None:
        result['state-table']['max-entries'] = {'total': maximum}
    return result


def memory():
    result = {}
    maximum = read_int('%s/nf_conntrack_max' % CONNTRACK_DIR)
    if maximum is not None:
        # the pf "states" hard limit maps to the conntrack table size
        result['states'] = maximum
    return result


def timeouts():
    result = {}
    for sysctl, key in TIMEOUT_MAP.items():
        value = read_int('%s/%s' % (CONNTRACK_DIR, sysctl))
        if value is not None:
            result[key] = str(value)
    return result


def interfaces():
    # no built-in per-interface pass/block accounting in netfilter
    return {}


def rules():
    # no built-in per-rule accounting without explicit named counters
    return {}


def main():
    sections = {
        'info': info,
        'memory': memory,
        'timeouts': timeouts,
        'interfaces': interfaces,
        'rules': rules,
    }
    result = dict()
    for section in sections:
        if (len(sys.argv) > 1 and sys.argv[1] == section) or (len(sys.argv) == 1):
            result[section] = sections[section]()

    return result

print(ujson.dumps(main()))
