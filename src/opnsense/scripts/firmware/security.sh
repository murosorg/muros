#!/bin/sh

# Copyright (C) 2016-2021 Franco Fichtner <franco@opnsense.org>
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

REQUEST="AUDIT SECURITY"

. /usr/local/opnsense/scripts/firmware/config.sh

export DEBIAN_FRONTEND=noninteractive

output_cmd apt-get -o Dpkg::Use-Pty=0 update

# Debian has no pkg-audit; surface packages with a pending security update
SEC=$(apt-get -s -o Dpkg::Use-Pty=0 upgrade 2>/dev/null | awk '/^Inst / && /[Ss]ecurity/{print $2}')
if [ -n "${SEC}" ]; then
	COUNT=$(printf '%s\n' "${SEC}" | wc -l | tr -d ' ')
	output_txt "${COUNT} problem(s) in installed package(s) found."
	for P in ${SEC}; do
		output_txt "  ${P} is affected by a pending security update"
	done
else
	output_txt "0 problem(s) in installed package(s) found."
fi

output_done
