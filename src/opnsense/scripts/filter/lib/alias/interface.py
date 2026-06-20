"""
    Copyright (c) 2021-2023 Ad Schellevis <ad@opnsense.org>
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
import ipaddress
import json
import subprocess
from .base import BaseContentParser


class InterfaceParser(BaseContentParser):
    """ Interface address parser
    """
    _ipv6_networks = dict()

    @classmethod
    def _update(cls):
        # collect per interface IPv6 networks (ifconfig inet6 lines on FreeBSD ->
        # iproute2 json on Linux). Link-local addresses are kept here and filtered
        # out later in iter_addresses() which only acts on global addresses.
        sp = subprocess.run(['/usr/sbin/ip', '-6', '-j', 'addr', 'show'], capture_output=True, text=True)
        try:
            links = json.loads(sp.stdout or '[]')
        except ValueError:
            links = []
        for link in links:
            this_interface = link.get('ifname')
            if not this_interface:
                continue
            for addr_info in link.get('addr_info', []):
                if addr_info.get('family') != 'inet6':
                    continue
                addr = addr_info.get('local')
                mask = addr_info.get('prefixlen')
                if addr is None or mask is None:
                    continue
                if this_interface not in cls._ipv6_networks:
                    cls._ipv6_networks[this_interface] = []
                cls._ipv6_networks[this_interface].append(
                    {"addr": ipaddress.IPv6Address(addr), "mask": str(mask)}
                )

    def __init__(self, interface, **kwargs):
        super().__init__(**kwargs)
        self._interface = interface
        # collect addresses on class init (singleton)
        if len(self._ipv6_networks) == 0:
            self._update()

    def iter_addresses(self, pattern):
        if self._interface in self._ipv6_networks:
            for network in self._ipv6_networks[self._interface]:
                # only global addresses apply
                if network["addr"].is_global:
                    base_mask = int(network["mask"])
                    base_size=int((128-base_mask)/16)
                    offset_address = ipaddress.IPv6Address('0' + pattern.split("/")[0])
                    calculated_address = ':'.join(
                        network["addr"].exploded.split(':')[:8-base_size] +
                        offset_address.exploded.split(':')[8-base_size:]
                    )
                    calculated_mask = pattern.split("/")[1] if pattern.find("/") > -1 else "128"
                    yield "%s/%s" % (calculated_address, calculated_mask)
