#!/bin/sh

# Copyright (C) 2021-2026 Franco Fichtner <franco@opnsense.org>
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

REQUEST="AUDIT CONNECTIVITY"

. /usr/local/opnsense/scripts/firmware/config.sh

POPT="-c 4 -s 1500"

HOSTS=$(/usr/local/opnsense/scripts/firmware/hostnames.sh)
HOST=${HOSTS%%'
'*}

IPV4=$(getent ahostsv4 "${HOST}" 2>/dev/null | awk '{print $1; exit}')
IPV6=$(getent ahostsv6 "${HOST}" 2>/dev/null | awk '{print $1; exit}')

output_txt
output_txt "Current repository configuration:"
output_cmd sh -c 'cat /etc/apt/sources.list 2>/dev/null; cat /etc/apt/sources.list.d/*.list /etc/apt/sources.list.d/*.sources 2>/dev/null'

if [ -n "${IPV4}" ]; then
	output_txt
	output_txt "Checking connectivity for host: ${HOST} -> ${IPV4}"
	output_cmd ping -4 ${POPT} "${IPV4}"

	output_txt
	output_txt -n "Checking connectivity for repository (IPv4): "
	output_cmd apt-get -o Acquire::ForceIPv4=true -o Dpkg::Use-Pty=0 update
else
	output_txt
	output_txt "No IPv4 address could be found for host: ${HOST}"
fi

if [ -n "${IPV6}" ]; then
	output_txt
	output_txt "Checking connectivity for host: ${HOST} -> ${IPV6}"
	output_cmd ping -6 ${POPT} "${IPV6}"

	output_txt
	output_txt -n "Checking connectivity for repository (IPv6): "
	output_cmd apt-get -o Acquire::ForceIPv6=true -o Dpkg::Use-Pty=0 update
else
	output_txt
	output_txt "No IPv6 address could be found for host: ${HOST}"
fi

output_txt
for HOST in ${HOSTS}; do
	output_txt "Checking server certificate for host: ${HOST}"
	echo | output_cmd openssl s_client -quiet -no_ign_eof "${HOST}:443"
done

output_done
