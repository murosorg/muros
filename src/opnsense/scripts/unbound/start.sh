#!/bin/sh

# Copyright (c) 2020-2022 Deciso B.V.
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
#
# 1. Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in the
#    documentation and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE AUTHOR AND CONTRIBUTORS ``AS IS'' AND
# ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED.  IN NO EVENT SHALL THE AUTHOR OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS
# OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION)
# HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
# LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY
# OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF
# SUCH DAMAGE.

# prepare and startup unbound, so we can easily background it

DOMAIN=${1}

for FILE in $(find /var/unbound/etc -mindepth 1 -maxdepth 1 2>/dev/null); do
	rm -rf ${FILE}
done

cd /var/unbound/

# Ensure the DNSSEC root trust anchor exists. FreeBSD bootstrapped it with
# unbound-anchor, which Debian does not ship as a separate binary; seed it from
# the maintained copy in the dns-root-data package instead and let unbound keep
# it current through RFC 5011 once it is running.
if [ ! -s /var/unbound/root.key ] && [ -s /usr/share/dns/root.key ]; then
	cp /usr/share/dns/root.key /var/unbound/root.key
fi

# Generate the unbound-control TLS material on first start. Privileges are
# dropped to the unbound user with runuser; the FreeBSD "chroot -u user -g group /"
# trick has no GNU equivalent and no chroot is used here anyway.
if [ ! -f /var/unbound/unbound_control.key ]; then
	runuser -u unbound -- /usr/sbin/unbound-control-setup -d /var/unbound
fi

for FILE in $(find /usr/local/etc/unbound.opnsense.d -mindepth 1 -maxdepth 1 -name '*.conf' 2>/dev/null); do
	cp ${FILE} /var/unbound/etc/
done

chown -R unbound:unbound /var/unbound

/usr/sbin/unbound -c /var/unbound/unbound.conf
/usr/local/opnsense/scripts/unbound/cache.sh load

if [ -n "${DOMAIN}" ]; then
	/usr/local/opnsense/scripts/unbound/unbound_watcher.py --domain ${DOMAIN}
fi

if [ -f /var/unbound/data/stats ]; then
    # MurOS: muros-daemon is the Debian replacement for FreeBSD daemon(8); it
    # detaches the logger and writes its pidfile, accepting the same flags.
    /usr/local/sbin/muros-daemon -p /var/run/unbound_logger.pid -f -S -m 2 -s err -l local4 \
        -T unbound /usr/local/opnsense/scripts/unbound/logger.py
fi
