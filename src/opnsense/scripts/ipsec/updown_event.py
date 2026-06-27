#!/usr/local/bin/python3

"""
    Copyright (c) 2022-2023 Ad Schellevis <ad@opnsense.org>
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
    handle swanctl.conf updown event
"""
import os
import subprocess
import argparse
import syslog
from configparser import ConfigParser

events_filename = '/etc/swanctl/reqid_events.conf'

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--connection_child', help='uuid of the connection child')
    parser.add_argument('--reqid', default=os.environ.get('PLUTO_REQID'))
    parser.add_argument('--local', default=os.environ.get('PLUTO_ME'))
    parser.add_argument('--remote', default=os.environ.get('PLUTO_PEER'))
    parser.add_argument('--action', default=os.environ.get('PLUTO_VERB'))
    cmd_args = parser.parse_args()
    # init spd's on up-host[-v6], up-client[-v6]
    if cmd_args.action and cmd_args.action.startswith('up'):
        syslog.openlog('charon', facility=syslog.LOG_LOCAL4)
        syslog.syslog(syslog.LOG_NOTICE, '[UPDOWN] <%s> received %s event for reqid %s' % (cmd_args.connection_child, cmd_args.action, cmd_args.reqid))
        if os.path.exists(events_filename):
            cnf = ConfigParser()
            cnf.read(events_filename)
            spds = []
            for section in cnf.sections():
                if (cnf.has_option(section, 'reqid') and cnf.get(section, 'reqid') == cmd_args.reqid) or (
                    cnf.has_option(section, 'connection_child') and
                    cnf.get(section, 'connection_child') == cmd_args.connection_child
                ):
                    if section.startswith('spd_'):
                        spds.append({
                            'reqid': cmd_args.reqid,
                            'local' : cmd_args.local,
                            'remote' : cmd_args.remote,
                            'destination': os.environ.get('PLUTO_PEER_CLIENT')
                        })
                        for opt in cnf.options(section):
                            if cnf.get(section, opt).strip() != '':
                                spds[-1][opt] = cnf.get(section, opt).strip()

            # Route-based (VTI) tunnels are not configured here on Linux. The
            # ipsec<reqid> XFRM interface is created up front by
            # ipsec_configure_vti() with if_id = reqid, and charon binds the
            # negotiated SAs to it through if_id_in/if_id_out. There is no
            # per-interface tunnel source/destination to set on the device,
            # unlike the FreeBSD if_ipsec model, so the vti_ sections of
            # reqid_events.conf carry no work for the updown event. (FreeBSD ran
            # "ifconfig ipsecN reqid/tunnel" here, which has no Linux equivalent
            # and would abort the event on a missing ifconfig binary.)

            # (re)apply the manual security policies configured on a phase 2
            # (the "spd" field). FreeBSD installed these KAME policies with
            # setkey; on Linux the equivalent lives in the XFRM stack and is
            # driven through "ip xfrm policy". charon installs the policies it
            # negotiates by itself, so we only (re)install the extra manual
            # outbound policies the user defined, tagged with the same reqid
            # charon uses for the tunnel so the matching traffic is steered into
            # the negotiated SA. Each policy is deleted before being added again
            # so repeated up events stay idempotent and never stack duplicates;
            # only the exact manual selector is touched, never charon's own
            # policies.
            for spd in spds:
                if None in spd.values():
                    # incomplete, skip
                    continue
                selector_src = spd['source']
                selector_dst = spd['destination']
                proto = spd.get('protocol', 'esp')
                subprocess.run(
                    ['/usr/sbin/ip', 'xfrm', 'policy', 'delete',
                     'src', selector_src, 'dst', selector_dst, 'dir', 'out'],
                    capture_output=True, text=True
                )
                add_cmd = [
                    '/usr/sbin/ip', 'xfrm', 'policy', 'add',
                    'src', selector_src, 'dst', selector_dst, 'dir', 'out',
                    'tmpl', 'src', spd['local'], 'dst', spd['remote'],
                    'proto', proto, 'mode', 'tunnel', 'reqid', spd['reqid']
                ]
                syslog.syslog(
                    syslog.LOG_NOTICE,
                    '[UPDOWN] <%s> add manual policy: %s' % (cmd_args.connection_child, ' '.join(add_cmd[3:]))
                )
                result = subprocess.run(add_cmd, capture_output=True, text=True)
                if result.returncode != 0:
                    syslog.syslog(
                        syslog.LOG_ERR,
                        '[UPDOWN] <%s> failed to add manual policy: %s' % (
                            cmd_args.connection_child, result.stderr.strip()
                        )
                    )
