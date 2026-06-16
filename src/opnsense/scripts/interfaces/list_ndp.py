#!/usr/bin/python3

"""
    Copyright (c) 2016 Ad Schellevis <ad@opnsense.org>
    Copyright (c) 2026 MurOS
    All rights reserved.

    Redistribution and use in source and binary forms, with or without
    modification, are permitted provided that the following conditions are met:

    1. Redistributions of source code must retain the above copyright notice,
     this list of conditions and the following disclaimer.

    2. Redistributions in binary form must reproduce the above copyright
     notice, this list of conditions and the following disclaimer in the
     documentation and/or other materials provided with the distribution.

    THIS SOFTWARE IS PROVIDED ``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES.
    --------------------------------------------------------------------------------------
    list NDP (IPv6 neighbour) table, Debian / iproute2
"""
import json
import subprocess
import sys
sys.path.insert(0, "/usr/local/opnsense/site-python")
from lib import OUI

if __name__ == '__main__':
    result = []
    sp = subprocess.run(['/usr/sbin/ip', '-j', '-6', 'neigh', 'show'], capture_output=True, text=True)
    try:
        neigh = json.loads(sp.stdout or '[]')
    except Exception:
        neigh = []
    for src in neigh:
        mac = src.get('lladdr')
        ip = src.get('dst')
        if not mac or not ip:
            continue
        result.append({
            'mac': mac,
            'ip': ip,
            'intf': src.get('dev', ''),
            'manufacturer': OUI().get_vendor(mac, ''),
        })

    if len(sys.argv) > 1 and sys.argv[1] == 'json':
        print(json.dumps(result))
    else:
        print('%-40s %-20s %-10s %s' % ('ip', 'mac', 'intf', 'manufacturer'))
        for record in result:
            print('%(ip)-40s %(mac)-20s %(intf)-10s %(manufacturer)s' % record)
