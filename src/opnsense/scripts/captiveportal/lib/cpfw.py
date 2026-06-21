"""
    Copyright (c) 2025-2026 Deciso B.V.
    Copyright (c) 2015-2019 Ad Schellevis <ad@opnsense.org>
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
    Shared nftables data plane helpers for the captive portal.

    On FreeBSD the captive portal kept authenticated clients in pf tables and
    used ipfw "count" rules for per client accounting. On Debian both roles are
    served by a dedicated nftables table "inet captiveportal":

      * membership sets cp_<zone>_v4 / cp_<zone>_v6 hold the authenticated
        clients (the pf table role).
      * accounting sets acc_<zone>_{in,out}_{v4,v6} carry the "counter" flag so
        the kernel keeps per element packet/byte counters (the ipfw role). "in"
        counts traffic sourced by the client (saddr), "out" counts traffic
        destined to the client (daddr), matching the legacy semantics.
      * chain acct_<zone> hooks the forward path and references the accounting
        sets so matched elements get counted; it never issues a verdict.

    The table is recreated idempotently on demand. Because the main firewall
    ruleset is rebuilt with "flush ruleset", a reload wipes this table; the
    captive portal background process re-runs ensure_zone() and repopulates the
    member sets on its next sync cycle.
"""
import ipaddress
import json
import subprocess

NFT = '/usr/sbin/nft'
CONNTRACK = '/usr/sbin/conntrack'
TABLE_FAMILY = 'inet'
TABLE_NAME = 'captiveportal'


def _zid(zoneid):
    """ normalise a zone id to an integer usable in nft identifiers """
    return int(str(zoneid).strip())


def set_names(zoneid):
    """ return the set names used for a zone keyed by (role, family) """
    z = _zid(zoneid)
    return {
        ('member', 4): 'cp_%d_v4' % z,
        ('member', 6): 'cp_%d_v6' % z,
        ('in', 4): 'acc_%d_in_v4' % z,
        ('out', 4): 'acc_%d_out_v4' % z,
        ('in', 6): 'acc_%d_in_v6' % z,
        ('out', 6): 'acc_%d_out_v6' % z,
    }


def family_of(address):
    """ return 4 or 6 for an ip address (network notation accepted) """
    return 6 if str(address).find(':') > -1 else 4


def _run(args, **kwargs):
    kwargs.setdefault('capture_output', True)
    kwargs.setdefault('text', True)
    return subprocess.run([NFT] + args, **kwargs)


def _load(script):
    """ feed a multi line program to nft -f - """
    return subprocess.run([NFT, '-f', '-'], input=script, capture_output=True, text=True)


def ensure_zone(zoneid):
    """ idempotently create the table, sets and accounting chain for a zone """
    z = _zid(zoneid)
    names = set_names(z)
    chain = 'acct_%d' % z
    script = [
        'add table %s %s' % (TABLE_FAMILY, TABLE_NAME),
        'add set %s %s %s { type ipv4_addr; }' % (TABLE_FAMILY, TABLE_NAME, names[('member', 4)]),
        'add set %s %s %s { type ipv6_addr; }' % (TABLE_FAMILY, TABLE_NAME, names[('member', 6)]),
        'add set %s %s %s { type ipv4_addr; counter; }' % (TABLE_FAMILY, TABLE_NAME, names[('in', 4)]),
        'add set %s %s %s { type ipv4_addr; counter; }' % (TABLE_FAMILY, TABLE_NAME, names[('out', 4)]),
        'add set %s %s %s { type ipv6_addr; counter; }' % (TABLE_FAMILY, TABLE_NAME, names[('in', 6)]),
        'add set %s %s %s { type ipv6_addr; counter; }' % (TABLE_FAMILY, TABLE_NAME, names[('out', 6)]),
        'add chain %s %s %s { type filter hook forward priority -160; policy accept; }' % (
            TABLE_FAMILY, TABLE_NAME, chain
        ),
        'flush chain %s %s %s' % (TABLE_FAMILY, TABLE_NAME, chain),
        'add rule %s %s %s ip saddr @%s' % (TABLE_FAMILY, TABLE_NAME, chain, names[('in', 4)]),
        'add rule %s %s %s ip daddr @%s' % (TABLE_FAMILY, TABLE_NAME, chain, names[('out', 4)]),
        'add rule %s %s %s ip6 saddr @%s' % (TABLE_FAMILY, TABLE_NAME, chain, names[('in', 6)]),
        'add rule %s %s %s ip6 daddr @%s' % (TABLE_FAMILY, TABLE_NAME, chain, names[('out', 6)]),
    ]
    _load('\n'.join(script) + '\n')


def add_element(set_name, address):
    _run(['add', 'element', TABLE_FAMILY, TABLE_NAME, set_name, '{ %s }' % address])


def del_element(set_name, address):
    # deleting a missing element returns an error, which is expected and ignored
    _run(['delete', 'element', TABLE_FAMILY, TABLE_NAME, set_name, '{ %s }' % address])


def list_set(set_name):
    """ return {address: {'packets': int, 'bytes': int}} for a set, empty when
        the set (or table) does not exist """
    sp = _run(['-j', 'list', 'set', TABLE_FAMILY, TABLE_NAME, set_name])
    result = {}
    if sp.returncode != 0 or not sp.stdout:
        return result
    try:
        data = json.loads(sp.stdout)
    except ValueError:
        return result
    for item in data.get('nftables', []):
        elems = item.get('set', {}).get('elem', [])
        for entry in elems:
            elem = entry.get('elem') if isinstance(entry, dict) else None
            if isinstance(elem, dict):
                val = elem.get('val')
                counter = elem.get('counter', {})
            else:
                val = entry
                counter = {}
            if val is None:
                continue
            result[str(val)] = {
                'packets': int(counter.get('packets', 0)),
                'bytes': int(counter.get('bytes', 0)),
            }
    return result


def kill_states(address):
    """ drop conntrack entries for an address in both directions (replaces the
        FreeBSD "pfctl -k" state kill) """
    try:
        ipaddress.ip_address(address)
    except ValueError:
        return
    for flag in ('-s', '-d'):
        subprocess.run([CONNTRACK, '-D', flag, address], capture_output=True, text=True)


def list_accounting_all():
    """ aggregate per element counters of every zone's accounting sets.

        Returns {ip: {'in_pkts','in_bytes','out_pkts','out_bytes'}}. "in" is
        traffic sourced by the client, "out" is traffic destined to it. Every
        accounted address is reported even with zero counters so the background
        process can reconcile membership. """
    sp = _run(['-j', 'list', 'table', TABLE_FAMILY, TABLE_NAME])
    result = {}
    if sp.returncode != 0 or not sp.stdout:
        return result
    try:
        data = json.loads(sp.stdout)
    except ValueError:
        return result

    def _bucket(ip):
        if ip not in result:
            result[ip] = {'in_pkts': 0, 'in_bytes': 0, 'out_pkts': 0, 'out_bytes': 0}
        return result[ip]

    for item in data.get('nftables', []):
        s = item.get('set')
        if not s:
            continue
        name = s.get('name', '')
        parts = name.split('_')
        # acc_<zone>_<dir>_<fam>
        if len(parts) != 4 or parts[0] != 'acc' or parts[2] not in ('in', 'out'):
            continue
        direction = parts[2]
        for entry in s.get('elem', []):
            elem = entry.get('elem') if isinstance(entry, dict) else None
            if not isinstance(elem, dict):
                continue
            val = elem.get('val')
            if val is None:
                continue
            counter = elem.get('counter', {})
            bucket = _bucket(str(val))
            bucket['%s_pkts' % direction] += int(counter.get('packets', 0))
            bucket['%s_bytes' % direction] += int(counter.get('bytes', 0))
    return result
