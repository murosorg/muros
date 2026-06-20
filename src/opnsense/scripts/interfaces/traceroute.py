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
import argparse
import json
import subprocess

parser = argparse.ArgumentParser()
parser.add_argument('--domain', help='domain name or ip to trace', default='')
parser.add_argument('--ipproto', help='ip protocol version [inet,inet6]', default='inet')
parser.add_argument('--source_address', help='source address to use', default=None)
parser.add_argument('--timeout', help='timeout in seconds', type=int, default=20)
parser.add_argument('--probes', help='number of probes', type=int, default=1)
parser.add_argument('--protocol', help='protocol to use [icmp, udp]', default='udp')
inputargs = parser.parse_args()



result = {'rows': []}
if inputargs.ipproto == 'inet6':
    cmd = ['/usr/bin/traceroute6']
else:
    cmd = ['/usr/bin/traceroute']

# Debian ships the Butskoy traceroute: AS path lookups are -A (not -a), and the
# AS number is printed after the address as [ASxxx] (FreeBSD prints it before
# the host name).
cmd = cmd + ['-A', '-w', '2', '-q', '%d' % inputargs.probes]
if inputargs.source_address:
    cmd = cmd + ['-s', inputargs.source_address]
if inputargs.protocol.lower() == 'icmp':
    cmd.append('-I')

cmd.append(inputargs.domain)

proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
try:
    outs, errs = proc.communicate(timeout=inputargs.timeout)
except subprocess.TimeoutExpired:
    result['error'] = 'timeout reached'
    proc.kill()
    outs, errs = proc.communicate()

if errs:
    result['notice'] = errs.strip()


last_ttl = ''
for line in outs.strip().split('\n'):
    parts = line.split()
    if len(parts) < 2:
        continue
    # skip the "traceroute to host (addr), N hops max, ..." banner
    if parts[0] in ('traceroute', 'traceroute6'):
        continue

    # a new hop starts with the ttl; otherwise it is a continuation line for an
    # additional gateway answering for the previous ttl
    if parts[0].isdigit():
        last_ttl = parts[0]
        fields = parts[1:]
    else:
        fields = parts

    record = {'ttl': last_ttl, 'AS': '', 'host': '', 'address': '', 'probes': ''}

    if not fields or fields[0] == '*':
        # the whole hop timed out
        record['host'] = '*'
        record['probes'] = ' '.join(fields)
        result['rows'].append(record)
        continue

    idx = 0
    record['host'] = fields[idx]
    idx += 1
    if idx < len(fields) and fields[idx].startswith('('):
        record['address'] = fields[idx].strip('()')
        idx += 1
    if idx < len(fields) and fields[idx].startswith('['):
        as_token = fields[idx].strip('[]')
        record['AS'] = '' if as_token == '*' else as_token
        idx += 1
    record['probes'] = ' '.join(fields[idx:])
    result['rows'].append(record)

print(json.dumps(result))
