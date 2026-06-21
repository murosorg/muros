"""
    Copyright (c) 2025-2026 Deciso B.V.
    Copyright (c) 2015-2019 Ad Schellevis <ad@opnsense.org>
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
    Authenticated client membership for the captive portal.

    The legacy implementation kept clients in pf tables (__captiveportal_zone_*)
    and dropped their states with "pfctl -k". On Debian the same role is served
    by the nftables membership sets cp_<zone>_v4 / cp_<zone>_v6 and conntrack is
    used to drop established flows. The public method names are kept so the rest
    of the captive portal (allow/disconnect/background process) is unchanged.
"""
from . import cpfw


class PF(object):
    def __init__(self):
        pass

    @staticmethod
    def _is_ipv6(address):
        return cpfw.family_of(address) == 6

    @staticmethod
    def list_table(zoneid):
        """ yield the authenticated client addresses of a zone """
        names = cpfw.set_names(zoneid)
        for fam in (4, 6):
            for address in cpfw.list_set(names[('member', fam)]).keys():
                yield address

    @staticmethod
    def add_to_table(zoneid, address):
        """ mark an address as authenticated in a zone """
        cpfw.ensure_zone(zoneid)
        names = cpfw.set_names(zoneid)
        cpfw.add_element(names[('member', cpfw.family_of(address))], address)

    @staticmethod
    def remove_from_table(zoneid, address):
        """ remove an address from a zone and drop its existing flows """
        names = cpfw.set_names(zoneid)
        cpfw.del_element(names[('member', cpfw.family_of(address))], address)
        cpfw.kill_states(address)
