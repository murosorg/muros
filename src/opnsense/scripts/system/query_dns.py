#!/usr/local/bin/python3

"""
    Copyright (c) 2022 Ad Schellevis <ad@opnsense.org>
    Copyright (c) 2026 MurOS
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
    Resolve a hostname (forward) or an IP address (reverse) for the web GUI DNS
    lookup diagnostic. Uses dnspython instead of the FreeBSD host(1)/drill(1)
    command line tools, which are not part of a Debian base system.
"""
import argparse
import ipaddress
import json
import time

import dns.resolver
import dns.reversename
import dns.rdatatype
import dns.exception


def build_resolver(server):
    resolver = dns.resolver.Resolver(configure=True)
    if server:
        resolver.nameservers = [server]
    # keep the diagnostic responsive on unreachable servers
    resolver.timeout = 5.0
    resolver.lifetime = 5.0
    return resolver


def format_rrset(rrset):
    """Render an answer rrset as zone-file style lines."""
    lines = []
    name = rrset.name.to_text()
    rdtype = dns.rdatatype.to_text(rrset.rdtype)
    for item in rrset:
        lines.append('%s\t%d\tIN\t%s\t%s' % (name, rrset.ttl, rdtype, item.to_text()))
    return lines


def run_query(resolver, qname, qtype):
    """Return (record, fatal_error). record is None when there is no answer."""
    entry = {'answers': [], 'query_time': None, 'server': None}
    start = time.time()
    try:
        answer = resolver.resolve(qname, qtype, raise_on_no_answer=False)
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        return None, None
    except dns.exception.DNSException as exc:
        # timeout, no reachable nameserver, ... : surface to the user
        return None, str(exc) or exc.__class__.__name__

    entry['query_time'] = '%d msec' % int(round((time.time() - start) * 1000))
    try:
        entry['server'] = answer.nameserver
    except AttributeError:
        entry['server'] = resolver.nameservers[0] if resolver.nameservers else None
    if answer.rrset is not None:
        entry['answers'] = format_rrset(answer.rrset)
    return (entry if entry['answers'] else None), None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('domain', help='domain name or IP address to query')
    parser.add_argument('--types', help='list of types to query (when querying hostnames)',
                        default='A,AAAA,MX,TXT')
    parser.add_argument('--server', help='server to query', default=None)
    inputargs = parser.parse_args()

    try:
        ipaddress.ip_address(inputargs.domain)
        is_ipaddr = True
    except ValueError:
        is_ipaddr = False

    resolver = build_resolver(inputargs.server)
    result = {}

    if is_ipaddr:
        qname = dns.reversename.from_address(inputargs.domain)
        entry, error = run_query(resolver, qname, 'PTR')
        if error:
            result['error_message'] = error
        elif entry:
            result['PTR'] = entry
    else:
        for qtype in inputargs.types.split(','):
            qtype = qtype.strip()
            if not qtype:
                continue
            entry, error = run_query(resolver, inputargs.domain, qtype)
            if error:
                result['error_message'] = error
                break
            if entry:
                result[qtype] = entry

    print(json.dumps(result))


if __name__ == '__main__':
    main()
