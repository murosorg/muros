#!/usr/bin/python3

"""
    Copyright (c) 2019 Ad Schellevis <ad@opnsense.org>
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
    manually delete a static route, when found in the routing table (by number or name)
"""
import subprocess
import sys
import argparse
import ipaddress
import json


def show_routes(family_arg):
    """ return the routing table for one family as parsed by iproute2,
        mirroring the representation used by show_routes.py """
    try:
        sp = subprocess.run(['/usr/sbin/ip', '-j', family_arg, 'route', 'show'],
                            capture_output=True, text=True)
        return json.loads(sp.stdout or '[]')
    except Exception:
        return []


if __name__ == '__main__':
    # parse input arguments
    parser = argparse.ArgumentParser()
    parser.add_argument('--destination', help='destination', required=True)
    parser.add_argument('--gateway', help='gateway', required=True)
    inputargs = parser.parse_args()

    inet = '-6' if inputargs.destination.find(':') > 0 else '-4'

    for route in show_routes(inet):
        dst = route.get('dst', '')
        # show_routes.py reports connected routes (no gateway) as "link#0"
        gateway = route.get('gateway', '') or 'link#0'
        if dst == inputargs.destination and gateway == inputargs.gateway:
            # route entry found, try to delete
            print("found")
            cmd = ['/usr/sbin/ip', inet, 'route', 'del', dst]
            try:
                ipaddress.ip_address(inputargs.gateway)
                # gateway is an ip address (v4/v6)
                cmd += ['via', inputargs.gateway]
            except ValueError:
                pass
            subprocess.run(cmd, capture_output=True)
            sys.exit(0)

    # not found
    print("not_found")
