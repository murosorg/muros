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

import ipaddress
import json
import subprocess

# MurOS: firewall aliases are no longer FreeBSD pf tables but nftables named
# sets living in `table inet muros`. Each address alias maps to a pair of
# interval sets, "<name>_v4" (ipv4_addr) and "<name>_v6" (ipv6_addr), both
# created by the ruleset generator (nft_build.php). This class keeps the same
# interface the alias updater relies on, while addressing the v4/v6 split and
# the difference between the pf table and nft set element models. Port aliases
# ("<name>_p") are macros, not dynamic tables, and are intentionally ignored
# here.
NFT = '/usr/sbin/nft'
NFT_FAMILY = 'inet'
NFT_TABLE = 'muros'


class PF:

    @staticmethod
    def _run(args, _input=None):
        return subprocess.run(
            [NFT] + args, input=_input, capture_output=True, text=True
        )

    @staticmethod
    def _set_name(table_name, family):
        return '%s_%s' % (table_name, 'v6' if family == 'v6' else 'v4')

    @staticmethod
    def _format_element(elem):
        """ render an nft JSON set element back to its textual form """
        if isinstance(elem, dict):
            if 'prefix' in elem:
                return '%s/%s' % (elem['prefix'].get('addr', ''), elem['prefix'].get('len', ''))
            if 'range' in elem and len(elem['range']) == 2:
                return '%s-%s' % (elem['range'][0], elem['range'][1])
            if 'set' in elem:
                return None
        return str(elem)

    @staticmethod
    def _set_elements(set_name):
        """ yield the textual elements of a single nft set (empty when absent) """
        sp = PF._run(['-j', 'list', 'set', NFT_FAMILY, NFT_TABLE, set_name])
        if sp.returncode != 0:
            return
        try:
            data = json.loads(sp.stdout or '{}')
        except ValueError:
            return
        for item in data.get('nftables', []):
            if 'set' not in item:
                continue
            for elem in item['set'].get('elem', []):
                value = PF._format_element(elem)
                if value:
                    yield value

    @staticmethod
    def _classify(entries):
        """ split a list of address/CIDR entries into v4 and v6 buckets """
        buckets = {'v4': [], 'v6': []}
        for entry in entries:
            entry = entry.strip()
            if entry == '' or entry.startswith('!'):
                continue
            host = entry.split('/')[0]
            try:
                fam = 'v6' if ipaddress.ip_address(host).version == 6 else 'v4'
            except ValueError:
                continue
            buckets[fam].append(entry)
        return buckets

    @staticmethod
    def _replace_sets(table_name, buckets):
        """ atomically flush and repopulate the v4/v6 sets for an alias """
        script = []
        for family in ('v4', 'v6'):
            set_name = PF._set_name(table_name, family)
            script.append('flush set %s %s %s' % (NFT_FAMILY, NFT_TABLE, set_name))
            if buckets.get(family):
                script.append('add element %s %s %s { %s }' % (
                    NFT_FAMILY, NFT_TABLE, set_name, ', '.join(sorted(set(buckets[family])))
                ))
        sp = PF._run(['-f', '-'], _input='\n'.join(script) + '\n')
        return sp.stderr.strip()

    @staticmethod
    def list_tables():
        """ enumerate address aliases with their element counts, grouping the
            "<name>_v4"/"<name>_v6" sets back into a single logical alias name """
        sp = PF._run(['-j', 'list', 'table', NFT_FAMILY, NFT_TABLE])
        if sp.returncode != 0:
            return
        try:
            data = json.loads(sp.stdout or '{}')
        except ValueError:
            return

        counts = {}
        for item in data.get('nftables', []):
            if 'set' not in item:
                continue
            set_def = item['set']
            name = set_def.get('name', '')
            if name.endswith('_v4') or name.endswith('_v6'):
                alias = name[:-3]
            else:
                continue
            counts.setdefault(alias, 0)
            counts[alias] += len(set_def.get('elem', []))

        for alias, total in counts.items():
            yield alias, {'addresses': total}

    @staticmethod
    def list_table(table_name):
        """ list the contents of an alias as the union of its v4 and v6 sets """
        for family in ('v4', 'v6'):
            for value in PF._set_elements(PF._set_name(table_name, family)):
                yield value

    @staticmethod
    def flush_network(table_name, ifname):
        """ set the alias to the directly connected network(s) of an interface """
        sp = subprocess.run(
            ['/usr/sbin/ip', '-j', 'addr', 'show', 'dev', ifname], capture_output=True, text=True
        )
        buckets = {'v4': [], 'v6': []}
        try:
            links = json.loads(sp.stdout or '[]')
        except ValueError:
            links = []
        for link in links:
            for addr in link.get('addr_info', []):
                local = addr.get('local')
                prefixlen = addr.get('prefixlen')
                if not local or prefixlen is None:
                    continue
                if addr.get('address') and addr.get('address') != local:
                    continue
                try:
                    network = ipaddress.ip_network('%s/%s' % (local, prefixlen), strict=False)
                except ValueError:
                    continue
                buckets['v6' if network.version == 6 else 'v4'].append(str(network))
        PF._replace_sets(table_name, buckets)

    @staticmethod
    def flush(table_name):
        for family in ('v4', 'v6'):
            PF._run(['flush', 'set', NFT_FAMILY, NFT_TABLE, PF._set_name(table_name, family)])

    @staticmethod
    def replace(table_name, filename):
        try:
            with open(filename, 'r') as handle:
                entries = handle.read().split('\n')
        except OSError as exc:
            return 'nft: cannot read %s: %s' % (filename, exc)
        return PF._replace_sets(table_name, PF._classify(entries))

    @staticmethod
    def remove(table_name):
        for family in ('v4', 'v6'):
            PF._run(['delete', 'set', NFT_FAMILY, NFT_TABLE, PF._set_name(table_name, family)])

    @staticmethod
    def add_element(table_name, value):
        """ add a single address/CIDR to the matching family set """
        value = value.strip()
        try:
            family = 'v6' if ipaddress.ip_address(value.split('/')[0]).version == 6 else 'v4'
        except ValueError:
            return
        PF._run([
            'add', 'element', NFT_FAMILY, NFT_TABLE,
            PF._set_name(table_name, family), '{ %s }' % value
        ])

    @staticmethod
    def delete_element(table_name, value):
        """ remove a single address/CIDR from the matching family set """
        value = value.strip()
        try:
            family = 'v6' if ipaddress.ip_address(value.split('/')[0]).version == 6 else 'v4'
        except ValueError:
            return
        PF._run([
            'delete', 'element', NFT_FAMILY, NFT_TABLE,
            PF._set_name(table_name, family), '{ %s }' % value
        ])

    @staticmethod
    def test_element(table_name, value):
        """ return True when an address is contained in the alias (interval aware) """
        try:
            addr = ipaddress.ip_address(value.split('/')[0])
        except ValueError:
            return False
        family = 'v6' if addr.version == 6 else 'v4'
        for elem in PF._set_elements(PF._set_name(table_name, family)):
            try:
                if '-' in elem:
                    low, high = elem.split('-', 1)
                    if ipaddress.ip_address(low.strip()) <= addr <= ipaddress.ip_address(high.strip()):
                        return True
                elif addr in ipaddress.ip_network(elem, strict=False):
                    return True
            except ValueError:
                continue
        return False
