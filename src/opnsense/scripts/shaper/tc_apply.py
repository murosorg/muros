#!/usr/bin/python3

"""
    Copyright (c) 2026 Ecritel
    All rights reserved.

    MurOS traffic shaper backend.

    FreeBSD shaped with dnctl: pipes and queues were dummynet objects and the
    classification was a set of ipfw rules. Neither exists on Debian, so the
    same model is expressed with the Linux queueing disciplines: a pipe becomes
    an HTB class carrying its bandwidth on every interface it is used on, a
    queue becomes a child class weighted inside its pipe, and the leaf discipline
    is fq_codel, or netem when the pipe adds delay.

    Classification is not done here: the ruleset marks a packet with the class
    of its pipe or queue through the packet priority, which HTB uses directly.
"""

import ipaddress
import os
import subprocess
import sys
import xml.etree.ElementTree as ET

import ujson

CONFIG = '/conf/config.xml'
TC = '/usr/sbin/tc'
IP = '/usr/sbin/ip'
ROOT_HANDLE = '1:'
DEFAULT_CLASS = '0xffff'
METRICS = {'bit': 1, 'Kbit': 1000, 'Mbit': 1000000, 'Gbit': 1000000000}


def run(args, quiet=True):
    """ run a tc command, returning its exit code
    """
    sp = subprocess.run([TC] + args, capture_output=True, text=True)
    if sp.returncode != 0 and not quiet:
        print('tc %s: %s' % (' '.join(args), sp.stderr.strip()), file=sys.stderr)
    return sp.returncode


def run_ip(args, quiet=True):
    """ run an ip command, returning its exit code
    """
    sp = subprocess.run([IP] + args, capture_output=True, text=True)
    if sp.returncode != 0 and not quiet:
        print('ip %s: %s' % (' '.join(args), sp.stderr.strip()), file=sys.stderr)
    return sp.returncode


def text(node, tag, default=''):
    if node is None:
        return default
    found = node.find(tag)
    return default if found is None or found.text is None else found.text.strip()


def load_model():
    """ read the shaper model plus the interface to device mapping
    """
    model = {'pipes': {}, 'queues': {}, 'rules': [], 'devices': {}}

    if not os.path.isfile(CONFIG):
        return model

    root = ET.parse(CONFIG).getroot()

    for node in root.findall('./interfaces/*'):
        device = text(node, 'if')
        if device:
            model['devices'][node.tag] = device

    shaper = root.find('./OPNsense/TrafficShaper')
    if shaper is None:
        return model

    for node in shaper.findall('./pipes/pipe'):
        if text(node, 'enabled', '1') != '1':
            continue
        number = text(node, 'number')
        if not number.isdigit():
            continue
        bandwidth = text(node, 'bandwidth')
        metric = METRICS.get(text(node, 'bandwidthMetric', 'Kbit'), 1000)
        model['pipes'][node.get('uuid')] = {
            'number': int(number),
            # tc takes a rate in bits per second
            'rate': (int(bandwidth) if bandwidth.isdigit() else 0) * metric,
            'delay': text(node, 'delay', '0'),
            'queue': text(node, 'queue', ''),
            'description': text(node, 'description'),
            'scheduler': text(node, 'scheduler'),
            'codel_enable': text(node, 'codel_enable'),
            'codel_target': text(node, 'codel_target'),
            'codel_interval': text(node, 'codel_interval'),
            'codel_ecn_enable': text(node, 'codel_ecn_enable'),
            'pie_enable': text(node, 'pie_enable'),
            'fqcodel_quantum': text(node, 'fqcodel_quantum'),
            'fqcodel_limit': text(node, 'fqcodel_limit'),
            'fqcodel_flows': text(node, 'fqcodel_flows'),
            'mask': text(node, 'mask'),
            'buckets': text(node, 'buckets'),
        }

    for node in shaper.findall('./queues/queue'):
        if text(node, 'enabled', '1') != '1':
            continue
        number = text(node, 'number')
        pipe = text(node, 'pipe')
        if not number.isdigit() or pipe not in model['pipes']:
            continue
        weight = text(node, 'weight', '1')
        model['queues'][node.get('uuid')] = {
            'number': int(number),
            'pipe': pipe,
            'weight': int(weight) if weight.isdigit() else 1,
            'description': text(node, 'description'),
            'delay': '0',
            'codel_enable': text(node, 'codel_enable'),
            'codel_target': text(node, 'codel_target'),
            'codel_interval': text(node, 'codel_interval'),
            'codel_ecn_enable': text(node, 'codel_ecn_enable'),
            'pie_enable': text(node, 'pie_enable'),
            'mask': text(node, 'mask'),
            'buckets': text(node, 'buckets'),
        }

    for node in shaper.findall('./rules/rule'):
        if text(node, 'enabled', '1') != '1':
            continue
        model['rules'].append({
            'uuid': node.get('uuid'),
            'target': text(node, 'target'),
            'direction': text(node, 'direction'),
            'interfaces': [text(node, 'interface'), text(node, 'interface2')],
            'proto': text(node, 'proto', 'ip'),
            'source': text(node, 'source'),
            'source_not': text(node, 'source_not'),
            'src_port': text(node, 'src_port'),
            'destination': text(node, 'destination'),
            'destination_not': text(node, 'destination_not'),
            'dst_port': text(node, 'dst_port'),
            'dscp': text(node, 'dscp'),
            'iplen': text(node, 'iplen'),
            'description': text(node, 'description'),
        })

    return model


# What a pipe or a queue asks of its queueing discipline. The shaper page has
# always offered these knobs, the port read none of them and gave every pipe
# the same default discipline, so a pipe configured to fight bufferbloat with
# tighter targets, or to use a different scheduler, behaved like one that had
# been left alone. Everything the kernel here can express is passed on, and
# what it cannot is named rather than dropped in silence.
UNTRANSLATED = {
    'wf2q+': 'the weighted fair queueing scheduler',
    'rr': 'the round robin scheduler',
    'qfq': 'the quick fair queueing scheduler',
    'prio': 'the strict priority scheduler',
}


def notes(model):
    """ the settings of the model that have no counterpart on this platform
    """
    found = []

    for kind in ('pipes', 'queues'):
        for entry in model[kind].values():
            label = '%s %d' % (kind[:-1], entry['number'])
            scheduler = entry.get('scheduler', '')
            if scheduler in UNTRANSLATED:
                found.append('%s: %s has no equivalent, fq_codel is used instead'
                             % (label, UNTRANSLATED[scheduler]))
            if entry.get('mask', '') not in ('', 'none'):
                found.append('%s: a dynamic pipe per %s is not translated, the discipline shares '
                             'the bandwidth between flows instead' % (label, entry['mask']))
            if entry.get('buckets', ''):
                found.append('%s: the hash bucket count is not translated' % label)

    for rule in model['rules']:
        if rule.get('direction') == 'out':
            continue
        if any(flower_match(rule, family) is not None for family in families_of(rule)):
            continue
        found.append('rule %s: what arrives on an interface is classified by a filter of the '
                     'traffic control layer, which cannot express this match, so only the '
                     'outbound half of the rule applies' % rule['uuid'])

    return found


def leaf_qdisc(entry):
    """ the queueing discipline at the bottom of a pipe or a queue, built from
        what the model asks for
    """
    delay = entry.get('delay', '0')
    if delay.isdigit() and int(delay) > 0:
        # a pipe that adds delay is asking for netem, and nothing else fits
        return ['netem', 'delay', '%sms' % delay]

    scheduler = entry.get('scheduler', '')
    codel = entry.get('codel_enable') == '1'
    ecn = entry.get('codel_ecn_enable') == '1'
    target = entry.get('codel_target', '')
    interval = entry.get('codel_interval', '')

    if entry.get('pie_enable') == '1' or scheduler == 'fq_pie':
        qdisc = ['fq_pie']
        if entry.get('fqcodel_limit', '').isdigit():
            qdisc += ['limit', entry['fqcodel_limit']]
        if entry.get('fqcodel_flows', '').isdigit():
            qdisc += ['flows', entry['fqcodel_flows']]
        if entry.get('fqcodel_quantum', '').isdigit():
            qdisc += ['quantum', entry['fqcodel_quantum']]
        if target.isdigit():
            qdisc += ['target', '%sms' % target]
        if ecn:
            qdisc += ['ecn']
        return qdisc

    if codel and scheduler not in ('fq_codel', 'fq_pie'):
        # a single queue with the controlled delay algorithm on it
        qdisc = ['codel']
        if target.isdigit():
            qdisc += ['target', '%sms' % target]
        if interval.isdigit():
            qdisc += ['interval', '%sms' % interval]
        if ecn:
            qdisc += ['ecn']
        return qdisc

    qdisc = ['fq_codel']
    if entry.get('fqcodel_limit', '').isdigit():
        qdisc += ['limit', entry['fqcodel_limit']]
    elif entry.get('queue', '').isdigit():
        qdisc += ['limit', entry['queue']]
    if entry.get('fqcodel_flows', '').isdigit():
        qdisc += ['flows', entry['fqcodel_flows']]
    if entry.get('fqcodel_quantum', '').isdigit():
        qdisc += ['quantum', entry['fqcodel_quantum']]
    if codel:
        if target.isdigit():
            qdisc += ['target', '%sms' % target]
        if interval.isdigit():
            qdisc += ['interval', '%sms' % interval]
        if ecn:
            qdisc += ['ecn']

    return qdisc


# Shaping what arrives on an interface.
#
# A queueing discipline only ever works on what leaves a device, so the older
# system, whose shaper sat inside the packet filter, could shape both
# directions from the same rule while here the inbound half of such a rule did
# nothing at all. The way around it is an intermediate device: the ingress of
# the real interface is redirected onto it, and its egress carries the same
# tree of pipes and queues as any other device.
#
# The classification cannot come from the packet filter this time. Ingress
# redirection happens before netfilter is ever consulted, so the priority a
# rule of the ruleset would set arrives too late. The match is therefore built
# as a filter of the traffic control layer, which sets the priority and
# redirects in one step. That filter is less expressive than the ruleset: it
# matches an address, a prefix, a protocol and ports, and the rules asking for
# more than that are named rather than half applied.
INGRESS_HANDLE = 'ffff:'
FLOWER_PROTO = {
    'tcp': 'tcp', 'udp': 'udp', 'icmp': 'icmp', 'ipv6-icmp': 'icmpv6',
    'igmp': '2', 'esp': '50', 'ah': '51', 'gre': '47',
}
PORTED_PROTO = ('tcp', 'udp')


def ifb_name(device):
    """ the intermediate device carrying what arrives on a real one """
    return ('ifb-%s' % device)[:15]


def families_of(rule):
    """ the address families a rule covers, as tc names them """
    proto = rule.get('proto', 'ip')
    if proto == 'ip4':
        return ['ip']
    if proto in ('ip6', 'ipv6-icmp'):
        return ['ipv6']
    if proto == 'icmp':
        return ['ip']
    return ['ip', 'ipv6']


def port_match(value):
    """ a port or a port range as a filter of the traffic control layer wants
        it, None when the value is not one
    """
    value = value.strip()
    if value == '':
        return None
    if value.isdigit():
        return value
    for separator in ('-', ':'):
        if separator in value:
            low, _, high = value.partition(separator)
            if low.isdigit() and high.isdigit():
                return '%s-%s' % (low, high)

    return None


def flower_match(rule, family):
    """ the match of a shaper rule as a flower filter, or None when the rule
        asks for something a flower filter cannot express
    """
    match = []
    proto = rule.get('proto', 'ip')

    if proto in ('tcp_ack', 'tcp_ack_not'):
        return None
    if rule.get('source_not') == '1' or rule.get('destination_not') == '1':
        return None
    if rule.get('iplen', '') != '' or rule.get('dscp', '') != '':
        return None

    l4 = FLOWER_PROTO.get(proto)
    if l4 is not None:
        match += ['ip_proto', l4]

    for field, keyword in (('source', 'src_ip'), ('destination', 'dst_ip')):
        value = rule.get(field, '').strip()
        if value in ('', 'any'):
            continue
        address = value.split('/')[0]
        colon = ':' in address
        if (family == 'ipv6') != colon:
            """ an address of the other family, this filter does not apply """
            return None
        try:
            ipaddress.ip_network(value, strict=False)
        except ValueError:
            return None
        match += [keyword, value]

    for field, keyword in (('src_port', 'src_port'), ('dst_port', 'dst_port')):
        value = rule.get(field, '').strip()
        if value == '':
            continue
        if proto not in PORTED_PROTO:
            return None
        ports = port_match(value)
        if ports is None:
            return None
        match += [keyword, ports]

    return match


def ingress_rules(model):
    """ the rules that shape what arrives, per real device """
    result = {}

    for rule in model['rules']:
        if rule.get('direction') == 'out':
            continue
        pipe = rule['target']
        if pipe in model['queues']:
            classid = '%s%x' % (ROOT_HANDLE, model['queues'][pipe]['number'])
            pipe = model['queues'][pipe]['pipe']
        elif pipe in model['pipes']:
            classid = '%s%x' % (ROOT_HANDLE, model['pipes'][pipe]['number'])
        else:
            continue
        if pipe not in model['pipes']:
            continue
        for interface in rule['interfaces']:
            device = model['devices'].get(interface)
            if device and os.path.isdir('/sys/class/net/%s' % device):
                result.setdefault(device, []).append((rule, classid))

    return result


def usable_ingress(model):
    """ the devices that need an intermediate one, with the rules that can be
        expressed as a filter
    """
    result = {}

    for device, rules in ingress_rules(model).items():
        usable = [(rule, classid) for rule, classid in rules
                  if any(flower_match(rule, family) is not None for family in families_of(rule))]
        if usable:
            result[device] = usable

    return result


def create_ingress_devices(model):
    """ the intermediate devices have to exist before a tree can be built on
        them, so they are made first
    """
    mapping = {}

    for device in sorted(usable_ingress(model)):
        ifb = ifb_name(device)
        if not os.path.isdir('/sys/class/net/%s' % ifb):
            run_ip(['link', 'add', ifb, 'type', 'ifb'], quiet=False)
        run_ip(['link', 'set', ifb, 'up'])
        if os.path.isdir('/sys/class/net/%s' % ifb):
            mapping[device] = ifb

    return mapping


def attach_ingress_filters(model, mapping):
    """ redirect what arrives on a shaped device onto its intermediate one,
        classified on the way
    """
    for device, rules in sorted(usable_ingress(model).items()):
        ifb = mapping.get(device)
        if ifb is None:
            continue

        run(['qdisc', 'del', 'dev', device, 'ingress'])
        if run(['qdisc', 'add', 'dev', device, 'handle', INGRESS_HANDLE, 'ingress'], quiet=False) != 0:
            continue

        preference = 100
        for rule, classid in rules:
            for family in families_of(rule):
                match = flower_match(rule, family)
                if match is None:
                    continue
                preference += 1
                run([
                    'filter', 'add', 'dev', device, 'parent', INGRESS_HANDLE,
                    'protocol', family, 'pref', str(preference), 'flower',
                ] + match + [
                    'action', 'skbedit', 'priority', classid,
                    'action', 'mirred', 'egress', 'redirect', 'dev', ifb,
                ], quiet=False)


def clear_ingress():
    """ remove the redirections and the intermediate devices of a past run """
    for device in os.listdir('/sys/class/net'):
        if device.startswith('ifb-'):
            run(['qdisc', 'del', 'dev', device, 'root'])
            run_ip(['link', 'del', device])
            real = device[4:]
            if os.path.isdir('/sys/class/net/%s' % real):
                run(['qdisc', 'del', 'dev', real, 'ingress'])


def pipe_devices(model):
    """ the devices each pipe has to be instantiated on, taken from the rules
        that target it directly or through one of its queues
    """
    result = {}

    ingress = usable_ingress(model)

    for rule in model['rules']:
        pipe = rule['target']
        if pipe in model['queues']:
            pipe = model['queues'][pipe]['pipe']
        if pipe not in model['pipes']:
            continue
        for interface in rule['interfaces']:
            device = model['devices'].get(interface)
            if not device or not os.path.isdir('/sys/class/net/%s' % device):
                continue
            direction = rule.get('direction', '')
            if direction != 'in':
                result.setdefault(pipe, set()).add(device)
            if direction != 'out' and device in ingress:
                # what arrives is shaped on the intermediate device
                result.setdefault(pipe, set()).add(ifb_name(device))

    return result


def flush_all_ingress():
    """ used by the flush action, which knows nothing of the model """
    clear_ingress()


def flush(devices=None):
    """ drop the shaper tree, leaving the interfaces with the kernel default
    """
    if devices is None:
        devices = os.listdir('/sys/class/net')
    for device in devices:
        run(['qdisc', 'del', 'dev', device, 'root'])


def apply_model():
    model = load_model()
    clear_ingress()
    ingress_mapping = create_ingress_devices(model)
    devices_for_pipe = pipe_devices(model)

    # every device the shaper touches is rebuilt from scratch: a partial update
    # would leave classes of a deleted pipe behind
    devices = set()
    for pipes in devices_for_pipe.values():
        devices |= pipes

    flush(devices)

    for device in sorted(devices):
        run(['qdisc', 'replace', 'dev', device, 'root', 'handle', ROOT_HANDLE,
             'htb', 'default', DEFAULT_CLASS], quiet=False)

    for uuid, pipe in model['pipes'].items():
        rate = '%dbit' % pipe['rate'] if pipe['rate'] > 0 else '1000000000bit'
        for device in sorted(devices_for_pipe.get(uuid, [])):
            classid = '%s%x' % (ROOT_HANDLE, pipe['number'])
            run(['class', 'replace', 'dev', device, 'parent', ROOT_HANDLE,
                 'classid', classid, 'htb', 'rate', rate, 'ceil', rate], quiet=False)

            # the discipline at the bottom of the pipe, as the model asks.
            # tc cannot swap one discipline for another under the same handle,
            # so whatever sits there goes first
            run(['qdisc', 'del', 'dev', device, 'parent', classid])
            leaf = ['qdisc', 'replace', 'dev', device, 'parent', classid,
                    'handle', '%x:' % pipe['number']] + leaf_qdisc(pipe)
            if run(leaf) != 0:
                # a kernel without the discipline the model asks for should not
                # cost the pipe its shaping
                run(['qdisc', 'replace', 'dev', device, 'parent', classid,
                     'handle', '%x:' % pipe['number'], 'fq_codel'], quiet=False)

    # queues share the bandwidth of their pipe proportionally to their weight
    for pipe_uuid, pipe in model['pipes'].items():
        queues = [q for q in model['queues'].values() if q['pipe'] == pipe_uuid]
        total = sum([q['weight'] for q in queues]) or 1
        for queue in queues:
            share = max(int(pipe['rate'] * queue['weight'] / total), 8000)
            for device in sorted(devices_for_pipe.get(pipe_uuid, [])):
                run(['class', 'replace', 'dev', device,
                     'parent', '%s%x' % (ROOT_HANDLE, pipe['number']),
                     'classid', '%s%x' % (ROOT_HANDLE, queue['number']),
                     'htb', 'rate', '%dbit' % share,
                     'ceil', '%dbit' % (pipe['rate'] or share)], quiet=False)
                run(['qdisc', 'del', 'dev', device,
                     'parent', '%s%x' % (ROOT_HANDLE, queue['number'])])
                leaf = ['qdisc', 'replace', 'dev', device,
                        'parent', '%s%x' % (ROOT_HANDLE, queue['number']),
                        'handle', '%x:' % queue['number']] + leaf_qdisc(queue)
                if run(leaf) != 0:
                    run(['qdisc', 'replace', 'dev', device,
                         'parent', '%s%x' % (ROOT_HANDLE, queue['number']),
                         'handle', '%x:' % queue['number'], 'fq_codel'], quiet=False)

    # the redirection comes last, once the tree it feeds exists
    attach_ingress_filters(model, ingress_mapping)

    return len(devices)


def nft_rule_counters():
    """ packet and byte counters of the classification rules, keyed by the rule
        uuid the ruleset carries in the comment ("shaper,<uuid>")
    """
    counters = {}

    sp = subprocess.run(['/usr/sbin/nft', '-j', 'list', 'chain', 'inet', 'muros', 'mangle_postrouting'],
                        capture_output=True, text=True)
    try:
        parsed = ujson.loads(sp.stdout)
    except ValueError:
        return counters

    for item in parsed.get('nftables', []):
        rule = item.get('rule')
        if rule is None or not str(rule.get('comment', '')).startswith('shaper,'):
            continue
        uuid = str(rule['comment']).split(',', 1)[1]
        for expression in rule.get('expr', []):
            if 'counter' in expression:
                counters[uuid] = {
                    'pkts': expression['counter'].get('packets', 0),
                    'bytes': expression['counter'].get('bytes', 0),
                }

    return counters


def class_counters(devices):
    """ counters of every class, summed over the interfaces a pipe lives on
    """
    counters = {}

    for device in sorted(devices):
        sp = subprocess.run([TC, '-s', '-j', 'class', 'show', 'dev', device],
                            capture_output=True, text=True)
        try:
            classes = ujson.loads(sp.stdout)
        except ValueError:
            continue
        for item in classes:
            handle = item.get('handle', '')
            if ':' not in handle:
                continue
            try:
                number = int(handle.split(':')[1] or '0', 16)
            except ValueError:
                continue
            stats = item.get('stats', {})
            entry = counters.setdefault(number, {'pkts': 0, 'bytes': 0, 'drops': 0, 'backlog': 0})
            entry['pkts'] += stats.get('packets', 0)
            entry['bytes'] += stats.get('bytes', 0)
            entry['drops'] += stats.get('drops', 0)
            entry['backlog'] += stats.get('backlog', 0)

    return counters


def statistics():
    """ statistics in the shape the shaper page reads, which is the one dnctl
        used to produce: pipes and queues keyed by their number, the queues
        pointing at their pipe through sched_nr, and the classification rules
        attached to the object they target.
    """
    model = load_model()
    devices_for_pipe = pipe_devices(model)

    devices = set()
    for pipes in devices_for_pipe.values():
        devices |= pipes

    counters = class_counters(devices)
    rule_counters = nft_rule_counters()

    result = {'pipes': {}, 'queues': {}, 'rules': {'pipes': [], 'queues': []}}

    for uuid, pipe in model['pipes'].items():
        number = pipe['number']
        stats = counters.get(number, {})
        result['pipes'][str(number)] = {
            'pipe': number,
            'bandwidth': pipe['rate'],
            'delay': pipe['delay'],
            'interfaces': sorted(devices_for_pipe.get(uuid, [])),
            'pkts': stats.get('pkts', 0),
            'bytes': stats.get('bytes', 0),
            'drops': stats.get('drops', 0),
            'backlog_bytes': stats.get('backlog', 0),
            'flows': [],
        }

    for uuid, queue in model['queues'].items():
        number = queue['number']
        stats = counters.get(number, {})
        result['queues'][str(number)] = {
            'flow_set_nr': number,
            'sched_nr': model['pipes'][queue['pipe']]['number'],
            'weight': queue['weight'],
            'pkts': stats.get('pkts', 0),
            'bytes': stats.get('bytes', 0),
            'drops': stats.get('drops', 0),
            'backlog_bytes': stats.get('backlog', 0),
            'flows': [],
        }

    for rule in model['rules']:
        target = rule['target']
        if target in model['queues']:
            section = 'queues'
            number = model['queues'][target]['number']
        elif target in model['pipes']:
            section = 'pipes'
            number = model['pipes'][target]['number']
        else:
            continue
        stats = rule_counters.get(rule['uuid'], {})
        result['rules'][section].append({
            'attached_to': number,
            'rule_uuid': rule['uuid'],
            'pkts': stats.get('pkts', 0),
            'bytes': stats.get('bytes', 0),
        })

    return result


if __name__ == '__main__':
    action = sys.argv[1] if len(sys.argv) > 1 else 'apply'

    if action == 'flush':
        flush()
        flush_all_ingress()
        print('shaper flushed')
    elif action == 'statistics':
        print(ujson.dumps(statistics()))
    elif action == 'notes':
        for note in notes(load_model()):
            print(note)
    else:
        count = apply_model()
        for note in notes(load_model()):
            print('shaper: %s' % note, file=sys.stderr)
        print('shaper applied on %d interface(s)' % count)
