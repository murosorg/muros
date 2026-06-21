#!/usr/bin/python3

"""
    Copyright (c) 2024 MurOS
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
    Report per-protocol network statistics for the Diagnostics protocol page.

    The FreeBSD original used `netstat -s --libxo json`. The kernel keeps the
    same SNMP-style counters on Linux under procfs, so this rebuilds an
    equivalent tree from /proc/net/snmp and /proc/net/netstat (IPv4 plus the
    IP/TCP extensions) and /proc/net/snmp6 (IPv6). Each protocol becomes a
    dictionary of named counters, which is what the protocol view renders.
"""
import ujson


def parse_paired(path):
    """ /proc/net/snmp and /proc/net/netstat store, per protocol, a header row
        of names and a matching row of values, both prefixed by the protocol
        label. Pair them up into {protocol: {name: value}}. """
    result = {}
    try:
        with open(path) as fh:
            lines = fh.read().strip().split('\n')
    except OSError:
        return result
    for i in range(0, len(lines) - 1, 2):
        head = lines[i].split()
        data = lines[i + 1].split()
        if not head or not data or head[0] != data[0]:
            continue
        proto = head[0].rstrip(':')
        section = result.setdefault(proto, {})
        for name, value in zip(head[1:], data[1:]):
            try:
                section[name] = int(value)
            except ValueError:
                section[name] = value
    return result


def parse_snmp6(path):
    """ /proc/net/snmp6 is a flat "Ip6InReceives 123" list; the protocol is the
        leading word of each counter name (Ip6, Icmp6, Udp6, Tcp6, ...). """
    result = {}
    try:
        with open(path) as fh:
            rows = fh.read().strip().split('\n')
    except OSError:
        return result
    for row in rows:
        parts = row.split()
        if len(parts) != 2:
            continue
        name, value = parts
        proto = 'Ip6'
        for prefix in ('Icmp6', 'Udp6', 'UdpLite6', 'Tcp6', 'Ip6'):
            if name.startswith(prefix):
                proto = prefix
                break
        section = result.setdefault(proto, {})
        try:
            section[name] = int(value)
        except ValueError:
            section[name] = value
    return result


if __name__ == '__main__':
    result = {}
    result.update(parse_paired('/proc/net/snmp'))
    for proto, counters in parse_paired('/proc/net/netstat').items():
        result.setdefault(proto, {}).update(counters)
    for proto, counters in parse_snmp6('/proc/net/snmp6').items():
        result.setdefault(proto, {}).update(counters)
    print(ujson.dumps(result))
