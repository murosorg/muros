#!/usr/bin/python3

"""
    Copyright (c) 2016 Ad Schellevis <ad@opnsense.org>
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
    returns the aggregated statistics of the netflow exporters (softflowd)
"""
import glob
import os
import subprocess
import sys
import ujson

# MurOS: netgraph and flowctl do not exist on Debian, the exporters are
# softflowd instances started by /usr/local/etc/rc.d/netflow. Each one owns a
# control socket named after the interface it captures and the rank of its
# collector, and reports its own counters through softflowctl.
CTL_GLOB = '/var/run/softflowd_*.ctl'
SOFTFLOWCTL = '/usr/sbin/softflowctl'


def instance_statistics(ctl):
    ''' collect the counters of a single exporter
    '''
    stats = {'Pkts': 0, 'SrcIPaddresses': 0, 'DstIPaddresses': 0}
    sp = subprocess.run([SOFTFLOWCTL, '-c', ctl, 'statistics'], capture_output=True, text=True)
    for line in sp.stdout.split("\n"):
        parts = line.strip().split(':', 1)
        if len(parts) != 2:
            continue
        key, value = parts[0].strip().lower(), parts[1].strip().split()
        if not value or not value[0].isdigit():
            continue
        if key == 'packets processed':
            stats['Pkts'] = int(value[0])
        elif key == 'flows expired':
            # the closest equivalent of the per address counts of flowctl:
            # softflowd only exposes flow totals, not the addresses seen
            stats['Flows'] = int(value[0])
    return stats


if __name__ == '__main__':
    result = dict()

    for ctl in sorted(glob.glob(CTL_GLOB)):
        node = os.path.basename(ctl)[len('softflowd_'):-len('.ctl')]
        stats = instance_statistics(ctl)
        # the node name is "<interface>_<collector rank>"
        stats['if'] = node.rsplit('_', 1)[0]
        result['netflow_%s' % node] = stats

    # handle command line argument (type selection)
    if len(sys.argv) > 1 and 'json' in sys.argv:
        print(ujson.dumps(result))
    else:
        print('[contents of netflow cache]')
        for node in result:
            print('node : %s' % node)
            print('  interface                : %s' % result[node]['if'])
            print('  #flows expired           : %d' % result[node].get('Flows', 0))
            print('  #packets                 : %d' % result[node]['Pkts'])
