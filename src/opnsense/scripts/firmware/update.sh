#!/bin/sh

# Copyright (C) 2015-2025 Franco Fichtner <franco@opnsense.org>
# Copyright (C) 2014 Deciso B.V.
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

REQUEST="UPDATE"

. /usr/local/opnsense/scripts/firmware/config.sh

CMD=${1}

ALWAYS_REBOOT=$(/usr/local/sbin/pluginctl -g system.firmware.reboot)
PKGS_HASH=$(dpkg-query -W -f='${Package}-${Version}\n' 2> /dev/null | sha256sum | awk '{print $1}')
UPDATE_FAILED=

export DEBIAN_FRONTEND=noninteractive
APT_OPTS="-o Dpkg::Use-Pty=0 -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold"

# refresh metadata, then apply every pending package change non-interactively
if ! output_cmd apt-get -o Dpkg::Use-Pty=0 update; then
	UPDATE_FAILED=1
fi

if [ -z "${UPDATE_FAILED}" ]; then
	if ! output_cmd apt-get -y ${APT_OPTS} dist-upgrade; then
		UPDATE_FAILED=1
	fi
fi

# the GUI may have been replaced underneath us, restart it
output_cmd /usr/local/etc/rc.restart_webgui

if [ -n "${UPDATE_FAILED}" ]; then
	output_txt "Partial update failure detected: review this update log."
	output_txt "No further actions will be taken. Please restart the update now."
	output_done keep-log
fi

if [ "${CMD}" = "sync" ]; then
	/usr/local/opnsense/scripts/firmware/sync.subr.sh
fi

# reboot when the kernel or core libraries were refreshed (apt marks this), or
# when the appliance is configured to always reboot after a package change
if [ -e /var/run/reboot-required ]; then
	output_reboot keep-log
fi

if [ "${ALWAYS_REBOOT}" = "1" ]; then
	if [ "${PKGS_HASH}" != "$(dpkg-query -W -f='${Package}-${Version}\n' 2> /dev/null | sha256sum | awk '{print $1}')" ]; then
		output_reboot keep-log
	fi
fi

output_done
