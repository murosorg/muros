#!/bin/sh

# Copyright (C) 2020-2021 Franco Fichtner <franco@opnsense.org>
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

export DEBIAN_FRONTEND=noninteractive

DID_INSTALL=

for PACKAGE in $(/usr/local/sbin/pluginctl -g system.firmware.plugins | \
    /usr/bin/sed 's/,/ /g'); do
	# install any configured plugin that is not present yet; apt resolves the
	# dependency on the core package itself, so the FreeBSD core-version gate is
	# no longer needed.
	if [ -z "$(dpkg-query -W -f='${Version}' "${PACKAGE}" 2> /dev/null)" ]; then
		output_cmd apt-get install -y "${PACKAGE}"
		output_cmd ${BASEDIR}/register.php install "${PACKAGE}"
		DID_INSTALL=1
	fi
done

if [ -n "${DID_INSTALL}" ]; then
	output_cmd apt-get autoremove -y
fi
