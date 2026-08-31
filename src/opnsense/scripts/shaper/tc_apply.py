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

import os
import subprocess
import sys
import xml.etree.ElementTree as ET

import ujson

CONFIG = '/conf/config.xml'
TC = '/usr/sbin/tc'
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
        }

    for node in shaper.findall('./rules/rule'):
        if text(node, 'enabled', '1') != '1':
            continue
        model['rules'].append({
            'target': text(node, 'target'),
            'interfaces': [text(node, 'interface'), text(node, 'interface2')],
        })

    return model


def pipe_devices(model):
    """ the devices each pipe has to be instantiated on, taken from the rules
        that target it directly or through one of its queues
    """
    result = {}

    for rule in model['rules']:
        pipe = rule['target']
        if pipe in model['queues']:
            pipe = model['queues'][pipe]['pipe']
        if pipe not in model['pipes']:
            continue
        for interface in rule['interfaces']:
            device = model['devices'].get(interface)
            if device and os.path.isdir('/sys/class/net/%s' % device):
                result.setdefault(pipe, set()).add(device)

    return result


def flush(devices=None):
    """ drop the shaper tree, leaving the interfaces with the kernel default
    """
    if devices is None:
        devices = os.listdir('/sys/class/net')
    for device in devices:
        run(['qdisc', 'del', 'dev', device, 'root'])


def apply_model():
    model = load_model()
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

            # the leaf discipline of a pipe without queues: fq_codel keeps the
            # latency down, netem is used instead when the pipe adds delay
            leaf = ['qdisc', 'replace', 'dev', device, 'parent', classid,
                    'handle', '%x:' % pipe['number']]
            if pipe['delay'].isdigit() and int(pipe['delay']) > 0:
                leaf += ['netem', 'delay', '%sms' % pipe['delay']]
            else:
                leaf += ['fq_codel']
                if pipe['queue'].isdigit():
                    leaf += ['limit', pipe['queue']]
            run(leaf)

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
                run(['qdisc', 'replace', 'dev', device,
                     'parent', '%s%x' % (ROOT_HANDLE, queue['number']),
                     'handle', '%x:' % queue['number'], 'fq_codel'])

    return len(devices)


def statistics():
    """ per class counters, keyed like the dnctl output the GUI used to read
    """
    model = load_model()
    devices_for_pipe = pipe_devices(model)
    names = {}

    for uuid, pipe in model['pipes'].items():
        names[pipe['number']] = {'type': 'pipe', 'description': pipe['description']}
    for uuid, queue in model['queues'].items():
        names[queue['number']] = {'type': 'queue', 'description': queue['description']}

    devices = set()
    for pipes in devices_for_pipe.values():
        devices |= pipes

    result = {}
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
            number = int(handle.split(':')[1] or '0', 16)
            if number not in names:
                continue
            result['%s_%d' % (device, number)] = {
                'device': device,
                'number': number,
                'type': names[number]['type'],
                'description': names[number]['description'],
                'bytes': item.get('stats', {}).get('bytes', 0),
                'packets': item.get('stats', {}).get('packets', 0),
                'drops': item.get('stats', {}).get('drops', 0),
                'backlog': item.get('stats', {}).get('backlog', 0),
            }

    return result


if __name__ == '__main__':
    action = sys.argv[1] if len(sys.argv) > 1 else 'apply'

    if action == 'flush':
        flush()
        print('shaper flushed')
    elif action == 'statistics':
        print(ujson.dumps(statistics()))
    else:
        print('shaper applied on %d interface(s)' % apply_model())
