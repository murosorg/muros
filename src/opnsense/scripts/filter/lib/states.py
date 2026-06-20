"""
    Copyright (c) 2015-2024 Ad Schellevis <ad@opnsense.org>
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
import subprocess
import ujson

# MurOS: the firewall state table is the Linux connection tracking table
# (nf_conntrack) instead of the FreeBSD pf state table. States are listed with
# the conntrack(8) tool and identified for deletion by their original-direction
# tuple. Unlike pf, conntrack does not associate a flow with the rule that
# created it, so the rule/label/interface columns are left empty.
CONNTRACK = '/usr/sbin/conntrack'


class AddressParser:
    def __init__(self):
        self._addresses = {}
        self._in_network = {}

    def split_ip_port(self, addr):
        if addr not in self._addresses:
            self._addresses[addr] = {
                'port': '0'
            }
            if addr.count(':') > 1:
                # parse IPv6 address
                tmp = addr.split('[')
                self._addresses[addr]['addr'] = tmp[0]
                self._addresses[addr]['ipproto'] = 'ipv6'
                if addr.find('[') > -1:
                    self._addresses[addr]['port'] = tmp[1].split(']')[0]
            else:
                # parse IPv4 address
                tmp = addr.split(':')
                self._addresses[addr]['ipproto'] = 'ipv4'
                self._addresses[addr]['addr'] = tmp[0]
                if len(tmp) > 1:
                    self._addresses[addr]['port'] = tmp[1]

        return self._addresses[addr]

    def overlaps(self, net, addr: str):
        if net not in self._in_network:
            self._in_network[net] = {}
        if addr not in self._in_network[net]:
            self._in_network[net][addr] = net.overlaps(ipaddress.ip_network(addr))

        return self._in_network[net][addr]


def fetch_rule_labels():
    """ Generate dict with labels per rule.

        The FreeBSD pf engine tagged every state with the number of the rule
        that created it, which allowed mapping a state back to a rule label.
        The Linux connection tracking subsystem keeps no such association, so
        there are no rule labels to resolve and an empty mapping is returned.
        The function is kept for API compatibility with the consumers.
        :return: dict
    """
    return {}


def parse_conntrack_line(line):
    """ Parse a single `conntrack -L -o extended,id` line into a normalized
        dict with the layer 3/4 protocols, connection state, both directions
        (orig/reply) and the conntrack id. Returns None for unparsable lines. """
    parts = line.split()
    if len(parts) < 5 or parts[0] not in ('ipv4', 'ipv6'):
        return None

    entry = {
        'ipproto': parts[0],
        'proto': parts[2],
        'timeout': parts[4] if parts[4].isdigit() else None,
        'state': '',
        'orig': {},
        'reply': {},
        'flags': [],
        'id': None,
        'mark': None,
    }

    idx = 5
    # tcp (and a few others) print a connection state word before the tuples
    if len(parts) > idx and '=' not in parts[idx] and not parts[idx].startswith('['):
        entry['state'] = parts[idx]
        idx += 1

    seen_src = 0
    target = entry['orig']
    for token in parts[idx:]:
        if token.startswith('[') and token.endswith(']'):
            entry['flags'].append(token.strip('[]').lower())
            continue
        if '=' not in token:
            continue
        key, value = token.split('=', 1)
        if key == 'src':
            seen_src += 1
            target = entry['orig'] if seen_src == 1 else entry['reply']
        if key in ('src', 'dst', 'sport', 'dport', 'packets', 'bytes'):
            target[key] = value
        elif key == 'id':
            entry['id'] = value
        elif key == 'mark':
            entry['mark'] = value

    return entry


def iter_conntrack():
    """ Yield every connection tracking entry as a normalized dict. """
    sp = subprocess.run([CONNTRACK, '-L', '-o', 'extended,id'], capture_output=True, text=True)
    for line in sp.stdout.strip().split('\n'):
        entry = parse_conntrack_line(line)
        if entry is not None:
            yield entry


def delete_entry(entry):
    """ Delete a single connection tracking entry by its original-direction
        tuple. conntrack(8) cannot reliably delete by id (it may crash), so the
        full tuple is used. Returns True when a flow was removed. """
    orig = entry['orig']
    if not orig.get('src') or not orig.get('dst'):
        return False
    args = [
        CONNTRACK, '-D',
        '-f', 'ipv6' if entry['ipproto'] == 'ipv6' else 'ipv4',
        '-p', entry['proto'],
        '-s', orig['src'],
        '-d', orig['dst'],
    ]
    if orig.get('sport'):
        args += ['--sport', orig['sport']]
    if orig.get('dport'):
        args += ['--dport', orig['dport']]
    sp = subprocess.run(args, capture_output=True, text=True)
    return sp.returncode == 0


def delete_by_id(state_id):
    """ Delete the connection tracking entry whose conntrack id matches the
        given value, resolved to a tuple deletion. The id may be supplied as
        "<id>/<creator>"; only the id segment is significant on Linux. """
    state_id = str(state_id).split('/')[0]
    for entry in iter_conntrack():
        if entry['id'] == state_id:
            return delete_entry(entry)
    return False


def derived_state(entry):
    """ Provide a human readable state for protocols (udp, icmp) that carry no
        explicit connection state in conntrack, based on the tracking flags. """
    if entry['state']:
        return entry['state']
    if 'assured' in entry['flags']:
        return 'ESTABLISHED'
    if 'unreplied' in entry['flags']:
        return 'UNREPLIED'
    return 'NEW'


def state_to_record(entry):
    """ Convert a conntrack entry into the state record exposed to the GUI. """
    orig = entry['orig']
    reply = entry['reply']

    nat_addr = None
    nat_port = None
    # source NAT: the address the peer replies to differs from the real source
    if reply.get('dst') and orig.get('src') and reply['dst'] != orig['src']:
        nat_addr = reply['dst']
        nat_port = reply.get('dport')
    # destination NAT: the address replying differs from the original target
    elif reply.get('src') and orig.get('dst') and reply['src'] != orig['dst']:
        nat_addr = reply['src']
        nat_port = reply.get('sport')

    return {
        'label': '',
        'descr': '',
        'nat_addr': nat_addr,
        'nat_port': nat_port,
        'gateway': None,
        'iface': '',
        'proto': entry['proto'],
        'ipproto': entry['ipproto'],
        'flags': entry['flags'],
        'direction': 'out',
        'src_addr': orig.get('src'),
        'src_port': orig.get('sport', '0'),
        'dst_addr': orig.get('dst'),
        'dst_port': orig.get('dport', '0'),
        'state': derived_state(entry),
        # keep a "/" so the GUI delete route receives a creator id segment
        'id': '%s/0' % (entry['id'] or '0'),
        'rule': '',
        'age': 0,
        'expires': int(entry['timeout']) if entry['timeout'] else 0,
        'pkts': [int(orig.get('packets', 0)), int(reply.get('packets', 0))],
        'bytes': [int(orig.get('bytes', 0)), int(reply.get('bytes', 0))],
    }


def split_filter_clauses(filter_str):
    filter_clauses = []
    filter_net_clauses = []
    for filter_clause in filter_str.split():
        try:
            addr = filter_clause.strip()
            filter_port = None
            if addr.startswith('[') and addr.count(']') == 1:
                filter_port = addr.split(']')[1].split(':')[1] if addr.split(']')[1].count(':') == 1 else None
                addr = addr.split(']')[0]
            elif addr.count(':') == 1:
                filter_port = addr.split(':')[1]
                addr = addr.split(':')[0]
            filter_network = ipaddress.ip_network(addr)
            filter_net_clauses.append([filter_network, filter_port])
        except ValueError:
            filter_clauses.append(filter_clause)
    return (filter_net_clauses, filter_clauses)



def query_states(rule_label, filter_str):
    addr_parser = AddressParser()

    result = list()
    filter_net_clauses, filter_clauses = split_filter_clauses(filter_str)

    for entry in iter_conntrack():
        record = state_to_record(entry)

        # rule based filtering cannot be honored: connection tracking does not
        # record which firewall rule created a flow, so a rule selection yields
        # no matches rather than a misleading result.
        if rule_label != "":
            continue

        if filter_clauses or filter_net_clauses:
            # enforce network when specified, otherwise only use filter clause
            match = len(filter_net_clauses) == 0
            for filter_net in filter_net_clauses:
                match = False
                for field in ['src_addr', 'dst_addr', 'nat_addr', 'gateway']:
                    port_field = "%s_port" % field[0:3]
                    try:
                        if record[field] is not None and addr_parser.overlaps(filter_net[0], record[field]):
                            if filter_net[1] is None or filter_net[1] == record[port_field]:
                                match = True
                    except ValueError:
                        continue
                if not match:
                    break

            if not match:
                continue

            if filter_clauses:
                search_line = " ".join(str(item) for item in filter(None, record.values()))
                for filter_clause in filter_clauses:
                    if search_line.find(filter_clause) == -1:
                        match = False
                        break
                if not match:
                    continue

        result.append(record)

    return result



def query_top():
    result = {
        'details': [],
        'metadata': {
            'labels': fetch_rule_labels()
        }
    }

    for entry in iter_conntrack():
        orig = entry['orig']
        record = {
            'proto': entry['proto'],
            'dir': 'out',
            'src_addr': orig.get('src'),
            'src_port': orig.get('sport', '0'),
            'dst_addr': orig.get('dst'),
            'dst_port': orig.get('dport', '0'),
            'gw_addr': None,
            'gw_port': None,
            'state': derived_state(entry),
            'age': 0,
            'expire': int(entry['timeout']) if entry['timeout'] else 0,
            'pkts': int(orig.get('packets', 0)) + int(entry['reply'].get('packets', 0)),
            'bytes': int(orig.get('bytes', 0)) + int(entry['reply'].get('bytes', 0)),
            'avg': 0,
            'rule': '',
        }
        result['details'].append(record)

    return result
