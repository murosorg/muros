#!/bin/sh

# Copyright (C) 2024 Franco Fichtner <franco@opnsense.org>
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

PACKAGE=${1}

# Render the package description and maintainer the way the GUI expects:
# short summary, blank line, long description, then the maintainer line.
apt-cache show "${PACKAGE}" 2>/dev/null | awk '
    /^Package:/ { pk++; if (pk > 1) exit }
    /^Description(-[a-z][a-z])?:/ && !seen { sub(/^[^:]*: */, ""); print; print ""; seen=1; desc=1; next }
    desc && /^[ \t]/ { s=$0; sub(/^[ \t]/, "", s); if (s == ".") s=""; print s; next }
    desc && /^[^ \t]/ { desc=0 }
    /^Maintainer:/ { m=$0; sub(/^Maintainer: */, "", m) }
    END { if (m != "") { print ""; print "Maintainer: " m } }
'
