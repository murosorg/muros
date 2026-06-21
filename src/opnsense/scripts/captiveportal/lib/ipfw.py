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
    Per client accounting for the captive portal.

    The legacy implementation used ipfw "count" rules (rule numbers 30000-50000)
    to count traffic per client and read a per rule last access timestamp. On
    Debian the accounting is carried by nftables sets with the "counter" flag
    (see lib/cpfw.py). nftables does not keep a per element last access time, so
    last_accessed is derived here from byte deltas between polls and cached in a
    small state file. The class name and method signatures are preserved so the
    captive portal background process keeps working unchanged.
"""
import os
import time
import json
from . import cpfw

STATE_DIR = '/var/run/captiveportal'
STATE_FILE = os.path.join(STATE_DIR, 'accounting.state')


class IPFW(object):
    @staticmethod
    def _as_list(addresses):
        if isinstance(addresses, str):
            return [addresses]
        return list(addresses)

    @staticmethod
    def add_accounting(zoneid, addresses):
        """ start accounting for one or more client addresses """
        cpfw.ensure_zone(zoneid)
        names = cpfw.set_names(zoneid)
        for address in IPFW._as_list(addresses):
            fam = cpfw.family_of(address)
            cpfw.add_element(names[('in', fam)], address)
            cpfw.add_element(names[('out', fam)], address)

    @staticmethod
    def del_accounting(zoneid, address):
        """ stop accounting for a client address """
        names = cpfw.set_names(zoneid)
        fam = cpfw.family_of(address)
        cpfw.del_element(names[('in', fam)], address)
        cpfw.del_element(names[('out', fam)], address)

    @staticmethod
    def _load_state():
        try:
            with open(STATE_FILE) as fhandle:
                return json.load(fhandle)
        except (OSError, ValueError):
            return {}

    @staticmethod
    def _save_state(state):
        try:
            os.makedirs(STATE_DIR, exist_ok=True)
            tmp = STATE_FILE + '.tmp'
            with open(tmp, 'w') as fhandle:
                json.dump(state, fhandle)
            os.replace(tmp, STATE_FILE)
        except OSError:
            pass

    @staticmethod
    def list_accounting_info():
        """ list accounting info per ip address across all zones.

        :return: {ip: {rule, last_accessed, in_pkts, in_bytes, out_pkts, out_bytes}}
        """
        counters = cpfw.list_accounting_all()
        prev = IPFW._load_state()
        now = int(time.time())
        result = {}
        new_state = {}
        for ip_address, acc in counters.items():
            total_bytes = int(acc['in_bytes']) + int(acc['out_bytes'])
            prev_entry = prev.get(ip_address)
            if prev_entry is None:
                # first observation, only stamp when traffic was already seen
                last_accessed = now if total_bytes > 0 else 0
            elif total_bytes > int(prev_entry.get('bytes', 0)):
                last_accessed = now
            else:
                last_accessed = int(prev_entry.get('last_accessed', 0))
            new_state[ip_address] = {'bytes': total_bytes, 'last_accessed': last_accessed}
            result[ip_address] = {
                'rule': 0,
                'last_accessed': last_accessed,
                'in_pkts': int(acc['in_pkts']),
                'in_bytes': int(acc['in_bytes']),
                'out_pkts': int(acc['out_pkts']),
                'out_bytes': int(acc['out_bytes']),
            }
        IPFW._save_state(new_state)
        return result
