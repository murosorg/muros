#!/bin/sh

# Copyright (C) 2015-2019 Franco Fichtner <franco@opnsense.org>
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

REQUEST="REINSTALL"

. /usr/local/opnsense/scripts/firmware/config.sh

PACKAGE=${1}

export DEBIAN_FRONTEND=noninteractive

# opnsense-revert restored a package to the exact repo build; on Debian the
# equivalent is a forced reinstall of the same version from apt.
if [ "${PACKAGE}" = "base" ]; then
	output_txt "Reinstalling base system package"
	if output_cmd apt-get install --reinstall -y "$(opnsense-version -n)"; then
		output_reboot
	fi
elif [ "${PACKAGE}" = "kernel" ]; then
	output_txt "Reinstalling kernel package"
	if output_cmd apt-get install --reinstall -y linux-image-amd64; then
		output_reboot
	fi
else
	output_cmd apt-get install --reinstall -y "${PACKAGE}"
	output_cmd ${BASEDIR}/register.php install "${PACKAGE}"
	output_cmd apt-get autoremove -y
fi

output_done
