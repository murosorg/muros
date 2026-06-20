#!/usr/bin/python3

"""
    Copyright (c) 2021 Ad Schellevis <ad@opnsense.org>
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
import ujson
from lib.states import iter_conntrack, state_to_record, delete_entry, split_filter_clauses, AddressParser


def matches(record, addr_parser, filter_net_clauses, filter_clauses):
    """ Reuse the same matching logic as query_states for the kill selection. """
    if filter_net_clauses:
        match = False
        for filter_net in filter_net_clauses:
            for field in ['src_addr', 'dst_addr', 'nat_addr', 'gateway']:
                port_field = "%s_port" % field[0:3]
                try:
                    if record[field] is not None and addr_parser.overlaps(filter_net[0], record[field]):
                        if filter_net[1] is None or filter_net[1] == record[port_field]:
                            match = True
                except ValueError:
                    continue
            if not match:
                return False
    if filter_clauses:
        search_line = " ".join(str(item) for item in filter(None, record.values()))
        for filter_clause in filter_clauses:
            if search_line.find(filter_clause) == -1:
                return False
    return True


if __name__ == '__main__':
    # parse input arguments
    parser = argparse.ArgumentParser()
    parser.add_argument('--filter', help='filter results', default='')
    parser.add_argument('--label', help='label / rule id', default='')
    inputargs = parser.parse_args()

    dropped = 0
    # a rule selection cannot be honored: connection tracking keeps no rule
    # association, so killing by rule id drops nothing.
    if inputargs.label == '':
        addr_parser = AddressParser()
        filter_net_clauses, filter_clauses = split_filter_clauses(inputargs.filter)
        for entry in iter_conntrack():
            record = state_to_record(entry)
            if matches(record, addr_parser, filter_net_clauses, filter_clauses):
                if delete_entry(entry):
                    dropped += 1

    print(ujson.dumps({'dropped_states': dropped}))
