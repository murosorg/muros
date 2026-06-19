#!/bin/sh

# Copyright (C) 2017-2023 Franco Fichtner <franco@opnsense.org>
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

SEP="|||"

# Emit the installed/available package inventory used by the firmware UI.
#
# Each line is a flat record of nine fields separated by "|||":
#   name|||version|||comment|||flatsize|||locked|||automatic|||license|||repository|||origin
#
# On Debian the package manager is dpkg/apt, so the data is taken from
# dpkg-query (installed set) and the apt cache (available upgrades) instead
# of the FreeBSD pkg/opnsense-update tooling used upstream.

case "${1}" in
local)
	HOLD=$(apt-mark showhold 2>/dev/null)
	AUTO=$(apt-mark showauto 2>/dev/null)

	dpkg-query -W -f='${Package}\t${Version}\t${Installed-Size}\t${Section}\t${binary:Summary}\n' 2>/dev/null | \
	    awk -F'\t' -v sep="${SEP}" -v hold="${HOLD}" -v auto="${AUTO}" '
	    BEGIN {
	        n = split(hold, h, "\n"); for (i = 1; i <= n; i++) if (h[i] != "") held[h[i]] = 1;
	        n = split(auto, a, "\n"); for (i = 1; i <= n; i++) if (a[i] != "") amark[a[i]] = 1;
	    }
	    {
	        name = $1; ver = $2; isize = $3; sect = $4; summary = $5;
	        # Installed-Size is reported in KiB, the UI expects bytes
	        size = (isize ~ /^[0-9]+$/) ? isize * 1024 : 0;
	        locked = (name in held) ? 1 : 0;
	        automatic = (name in amark) ? 1 : 0;
	        printf "%s%s%s%s%s%s%s%s%s%s%s%s%s%s%s%s%s/%s\n",
	            name, sep, ver, sep, summary, sep, size, sep, locked, sep,
	            automatic, sep, "", sep, "Debian", sep, sect, name;
	    }'
	;;
remote)
	# Rely on the existing apt cache (no network access here); the dedicated
	# check/update actions are responsible for refreshing the metadata.
	apt list --upgradable 2>/dev/null | \
	    sed -n 's#^\([^/]*\)/\([^ ]*\) \([^ ]*\) .*#\1\t\3\t\2#p' | \
	    awk -F'\t' -v sep="${SEP}" '{
	        printf "%s%s%s%s%s%s%s%s%s%s%s%s%s%s%s%s%s\n",
	            $1, sep, $2, sep, "", sep, 0, sep, 0, sep, 0, sep, "", sep, $3, sep, $1;
	    }'
	;;
tiers)
	# Plugin tier annotations are not modelled on Debian yet
	;;
*)
	;;
esac
