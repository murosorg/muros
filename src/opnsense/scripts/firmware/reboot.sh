#!/bin/sh

# Copyright (C) 2018-2023 Franco Fichtner <franco@opnsense.org>
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in the
#    documentation and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED ``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES,
# INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY
# AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
# AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY,
# OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

. /usr/local/opnsense/scripts/firmware/config.sh

WANT_REBOOT=1

# Debian flags a pending reboot (typically after a kernel or core library
# update) by creating this marker; that replaces the FreeBSD base/kernel set
# comparison done with opnsense-update -bk -c.
if [ -e /var/run/reboot-required ]; then
	WANT_REBOOT=0
fi

COREPKG=$(opnsense-version -n)

LQUERY=$(dpkg-query -W -f='${Version}' "${COREPKG}" 2> /dev/null)
RQUERY=$(apt-cache policy "${COREPKG}" 2> /dev/null | awk '/Candidate:/ { print $2 }')

# Additionally return the next version number if an update to the core package
# is available. The shell menu uses it to display the matching changelog hint.
if [ -n "${LQUERY}" ] && [ -n "${RQUERY}" ] && [ "${RQUERY}" != "(none)" ]; then
	if dpkg --compare-versions "${LQUERY}" lt "${RQUERY}"; then
		echo "${RQUERY}"
	fi
fi

ALWAYS_REBOOT=$(/usr/local/sbin/pluginctl -g system.firmware.reboot)
if [ "${ALWAYS_REBOOT}" = "1" ]; then
	WANT_REBOOT=0
fi

# success is reboot:
exit ${WANT_REBOOT}
