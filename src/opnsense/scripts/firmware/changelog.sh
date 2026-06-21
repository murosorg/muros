#!/bin/sh

# Copyright (c) 2016-2023 Franco Fichtner <franco@opnsense.org>
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

. /usr/local/opnsense/scripts/firmware/config.sh

set -e

DESTDIR="/usr/local/opnsense/changelog"

changelog_remove()
{
	mkdir -p ${DESTDIR}

	for FILE in $(find ${DESTDIR} -mindepth 1 -maxdepth 1 \! -name 'changelog.txz*'); do
		rm -rf ${FILE}
	done

	echo '[]' > ${DESTDIR}/index.json
}

changelog_url()
{
	# MurOS does not publish a signed changelog set yet
	echo ""
}

changelog_fetch()
{
	# No remote changelog feed on Debian yet; keep an empty but valid index so
	# the GUI renders cleanly instead of erroring on a missing file.
	mkdir -p ${DESTDIR}
	[ -f ${DESTDIR}/index.json ] || echo '[]' > ${DESTDIR}/index.json
}

changelog_show()
{
	FILE="${DESTDIR}/${1}"

	if [ -f "${FILE}" ]; then
		cat "${FILE}"
	fi
}

COMMAND=${1}
VERSION=${2}

if [ "${COMMAND}" = "fetch" ]; then
	changelog_fetch
elif [ "${COMMAND}" = "cron" ]; then
	# spread the (currently no-op) refresh over the next 12 hours
	sleep $(shuf -i 600-43800 -n 1)
	changelog_fetch
elif [ "${COMMAND}" = "remove" ]; then
	changelog_remove
elif [ "${COMMAND}" = "list" ]; then
	changelog_fetch
	changelog_show index.json
elif [ "${COMMAND}" = "url" ]; then
	changelog_url
elif [ "${COMMAND}" = "html" -a -n "${VERSION}" ]; then
	changelog_show "$(basename ${VERSION}).htm"
elif [ "${COMMAND}" = "text" -a -n "${VERSION}" ]; then
	changelog_show "$(basename ${VERSION}).txt"
elif [ "${COMMAND}" = "date" -a -n "${VERSION}" ]; then
	/usr/local/opnsense/scripts/firmware/changelog-date.php "$(basename ${VERSION})"
fi
