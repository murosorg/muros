#!/usr/bin/python3

"""
    Copyright (c) 2017-2019 Ad Schellevis <ad@opnsense.org>
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
    read filter log, limit by number of records or last received digest (md5 hash of row)
"""
import argparse
import os
import sys
import time
import select
import datetime
import subprocess
import xml.etree.ElementTree as ET
from hashlib import md5
import ujson

# kernel netfilter (nft "log") prefix marker emitted by nft_build.php:
#   "muros,<action>,<uuid> IN=.. OUT=.. SRC=.. DST=.. PROTO=.. SPT=.. DPT=.. .."
LOG_MARKER = 'muros,'
CONFIG_PATH = '/conf/config.xml'

# nft proto name -> protocol number, for the GUI protonum column
PROTO_NUM = {
    'tcp': '6', 'udp': '17', 'icmp': '1', 'icmpv6': '58', 'igmp': '2',
    'esp': '50', 'ah': '51', 'gre': '47', 'sctp': '132', 'ospf': '89',
}


def fetch_rule_details():
    """ map rule uuid -> description from the running configuration so the log
        viewer can label each record (replaces the FreeBSD /tmp/rules.debug parse)
    """
    rule_map = {}
    if not os.path.isfile(CONFIG_PATH):
        return rule_map
    try:
        root = ET.parse(CONFIG_PATH).getroot()
    except ET.ParseError:
        return rule_map
    # modern MVC model rules (OPNsense/Firewall/Filter)
    for rule in root.findall('./OPNsense/Firewall/Filter/rules/rule'):
        uuid = rule.get('uuid')
        if uuid:
            rule_map[uuid] = (rule.findtext('description') or '').strip()
    # legacy filter rules carrying a uuid
    for rule in root.findall('./filter/rule'):
        uuid = rule.findtext('uuid')
        if uuid:
            rule_map[uuid.strip()] = (rule.findtext('descr') or '').strip()
    return rule_map


def iso_timestamp(realtime_us):
    """ journald __REALTIME_TIMESTAMP (microseconds since epoch) -> iso string """
    try:
        return datetime.datetime.fromtimestamp(int(realtime_us) / 1000000.0).isoformat()
    except (ValueError, TypeError):
        return ''


def parse_message(message):
    """ parse a kernel netfilter log MESSAGE into the firewall log schema,
        return None when the line is not one of our firewall log records
    """
    if not message.startswith(LOG_MARKER):
        return None
    head, _, rest = message.partition(' ')
    prefix = head.split(',')
    if len(prefix) < 2:
        return None
    action = prefix[1]
    rid = prefix[2] if len(prefix) > 2 else ''

    fields = {}
    for token in rest.split():
        if '=' in token:
            key, value = token.split('=', 1)
            # ICMP repeats ID= (ip id then icmp id); keep the first (ip id)
            if key not in fields:
                fields[key] = value

    in_if = fields.get('IN', '')
    out_if = fields.get('OUT', '')
    if in_if:
        interface, direction = in_if, 'in'
    elif out_if:
        interface, direction = out_if, 'out'
    else:
        interface, direction = '', ''

    src = fields.get('SRC', '')
    proto = fields.get('PROTO', '').lower()
    rule = {
        'action': action,
        'dir': direction,
        'interface': interface,
        'src': src,
        'dst': fields.get('DST', ''),
        'srcport': fields.get('SPT', ''),
        'dstport': fields.get('DPT', ''),
        'protoname': proto,
        'protonum': PROTO_NUM.get(proto, proto if proto.isdigit() else ''),
        'ipversion': '6' if ':' in src else '4',
        'length': fields.get('LEN', ''),
        'rid': rid,
        'label': '',
        'srchostname': '',
        'dsthostname': '',
    }
    return rule


def label_for(rule, running_conf_descr):
    if rule['rid'] and rule['rid'] in running_conf_descr:
        return running_conf_descr[rule['rid']]
    if rule['action'] not in ('pass', 'block'):
        return '%s rule' % rule['action']
    return ''


def build_record(entry, running_conf_descr):
    """ turn one journald json entry into a firewall log record """
    message = entry.get('MESSAGE', '')
    if isinstance(message, list):
        # journald encodes non utf-8 MESSAGE as a byte array
        try:
            message = bytes(message).decode('utf-8', 'replace')
        except (ValueError, TypeError):
            return None
    rule = parse_message(message)
    if rule is None:
        return None
    realtime = entry.get('__REALTIME_TIMESTAMP', '')
    rule['__timestamp__'] = iso_timestamp(realtime)
    rule['__host__'] = entry.get('_HOSTNAME', '')
    rule['__digest__'] = md5((message + str(realtime)).encode()).hexdigest()
    rule['label'] = label_for(rule, running_conf_descr)
    return rule


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', help='limit results', type=int, default=5)
    parser.add_argument('--digest', help='row digest', default='')
    parser.add_argument('--stream', help='stream mode', action='store_true')
    parser.add_argument('--nlines', help='stream lines', type=int, default=5)
    cmd_args = parser.parse_args()

    running_conf_descr = fetch_rule_details()

    if cmd_args.stream:
        # follow the kernel journal; python filters our firewall log records
        f = subprocess.Popen(
            ['journalctl', '-k', '-o', 'json', '-f', '-n', str(cmd_args.nlines), '--no-pager'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=1,
            text=True
        )
        last_t = time.time()
        line_threshold = 10
        line_count = 0
        throttle_interval = 100  # ms
        counter = {}
        start_t_ms = time.time() * 1000
        try:
            while True:
                ready, _, _ = select.select([f.stdout], [], [], 1)
                if not ready:
                    print("event: keepalive\ndata:\n\n", flush=True)
                    continue
                line = f.stdout.readline()
                if not line:
                    break
                try:
                    entry = ujson.loads(line)
                except ValueError:
                    continue
                t = time.time()
                if (t - last_t) > 30:
                    last_t = t
                    running_conf_descr = fetch_rule_details()
                rule = build_record(entry, running_conf_descr)
                if rule is not None:
                    counter[rule['rid']] = counter.get(rule['rid'], 0) + 1
                    rule['counter'] = counter[rule['rid']]
                    line_count += 1
                    elapsed = (time.time() * 1000) - start_t_ms
                    if elapsed < throttle_interval and line_count <= line_threshold:
                        print("event: message\ndata: %s\n\n" % ujson.dumps(rule), flush=True)
                    elif elapsed >= throttle_interval:
                        line_count = 0
                        start_t_ms = time.time() * 1000
        except KeyboardInterrupt:
            f.kill()
    else:
        result = []
        # newest first; python collects our records up to the limit / digest
        f = subprocess.Popen(
            ['journalctl', '-k', '-o', 'json', '-r', '--no-pager'],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=1,
            text=True
        )
        try:
            for line in f.stdout:
                try:
                    entry = ujson.loads(line)
                except ValueError:
                    continue
                rule = build_record(entry, running_conf_descr)
                if rule is None:
                    continue
                result.append(rule)
                if cmd_args.limit != 0 and len(result) >= cmd_args.limit:
                    break
                if cmd_args.digest.strip() != '' and cmd_args.digest == rule['__digest__']:
                    break
        finally:
            f.terminate()
        print(ujson.dumps(result))
