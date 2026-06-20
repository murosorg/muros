#!/usr/bin/python3

"""
    Copyright (c) 2021 Deciso B.V.
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
    MurOS: returns the allocated and used size of the firewall alias tables.
    Aliases are nftables named sets (table inet muros) rather than FreeBSD pf
    tables, so the live element counts come from the PF abstraction (nft) while
    the planned size and last-updated timestamp still derive from the persisted
    /var/db/aliastables/<name>.txt files. The overall capacity is the configured
    maximum table-entries limit.
"""
import os
import sys
import ujson
import xml.etree.cElementTree as ET
from datetime import datetime

sys.path.insert(0, "/usr/local/opnsense/scripts/filter")
from lib.alias.pf import PF

# fallback capacity when the configuration does not pin a maximum, matching the
# historical default applied by the ruleset generator.
DEFAULT_TABLE_ENTRIES = 200000
CONFIG_XML = '/conf/config.xml'


def configured_table_entries():
    try:
        node = ET.ElementTree(file=CONFIG_XML).find('./system/maximumtableentries')
        if node is not None and node.text and node.text.strip().isdigit():
            return int(node.text.strip())
    except (ET.ParseError, OSError):
        pass
    return DEFAULT_TABLE_ENTRIES


if __name__ == '__main__':
    result = {
        'status': 'ok',
        'size': configured_table_entries(),
        'used': 0,
        'details': {}
    }

    for table_name, info in PF.list_tables():
        table_size = info.get('addresses', 0)
        table_updated = None
        filename = "/var/db/aliastables/%s.txt" % table_name
        if os.path.isfile(filename):
            tmp = open(filename).read()
            planned_size = tmp.count('\n') + 1 if len(tmp) > 0 else 0
            # if the planned size does not match the loaded set (auto-merge may
            # coalesce overlapping entries), report the larger intended size.
            table_size = max(planned_size, table_size)
            table_updated = datetime.fromtimestamp(os.path.getmtime(filename)).isoformat()

        result['details'][table_name] = {
            'count': table_size,
            'updated': table_updated,
        }
        result['used'] += table_size

    print(ujson.dumps(result))
