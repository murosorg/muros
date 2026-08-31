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

# The changelog of a Debian package travels with the package, so the history of
# the running version is already on the box and apt can fetch the one of a
# version not installed yet. Nothing is downloaded from a separate feed.
SOURCE="muros"

changelog_remove()
{
	mkdir -p ${DESTDIR}

	for FILE in $(find ${DESTDIR} -mindepth 1 -maxdepth 1); do
		rm -rf ${FILE}
	done

	echo '[]' > ${DESTDIR}/index.json
}

changelog_url()
{
	echo "https://muros.org/docs/changelog.html"
}

changelog_fetch()
{
	mkdir -p ${DESTDIR}

	RAW=$(mktemp)

	# the version that would be installed next, when the repository publishes
	# its changelog; a short timeout keeps the page responsive when it does not
	INSTALLED=$(dpkg-query -W -f='${Version}' ${SOURCE} 2> /dev/null || echo)
	CANDIDATE=$(apt-cache policy ${SOURCE} 2> /dev/null | awk '/Candidate:/ { print $2 }')
	if [ -n "${CANDIDATE}" -a "${CANDIDATE}" != "(none)" -a "${CANDIDATE}" != "${INSTALLED}" ]; then
		timeout 20 apt-get changelog ${SOURCE} 2> /dev/null >> ${RAW} || :
	fi

	# the history of the running version, shipped with the package itself
	if [ -f /usr/share/doc/${SOURCE}/changelog.gz ]; then
		zcat /usr/share/doc/${SOURCE}/changelog.gz >> ${RAW} 2> /dev/null || :
	elif [ -f /usr/share/doc/${SOURCE}/changelog.Debian.gz ]; then
		zcat /usr/share/doc/${SOURCE}/changelog.Debian.gz >> ${RAW} 2> /dev/null || :
	elif [ -f /usr/share/doc/${SOURCE}/changelog ]; then
		cat /usr/share/doc/${SOURCE}/changelog >> ${RAW} || :
	fi

	${BASEDIR}/changelog-parse.py ${RAW} ${DESTDIR} || echo '[]' > ${DESTDIR}/index.json
	rm -f ${RAW}
}

changelog_show()
{
	FILE="${DESTDIR}/${1}"

	if [ ! -f "${FILE}" ]; then
		changelog_fetch
	fi

	if [ -f "${FILE}" ]; then
		cat "${FILE}"
	fi
}

COMMAND=${1}
VERSION=${2}

if [ "${COMMAND}" = "fetch" ]; then
	changelog_fetch
elif [ "${COMMAND}" = "cron" ]; then
	# spread the refresh over the next 12 hours
	sleep $(shuf -i 600-43800 -n 1)
	changelog_fetch
elif [ "${COMMAND}" = "remove" ]; then
	changelog_remove
elif [ "${COMMAND}" = "list" ]; then
	# the firmware page asks for this list on every visit, only rebuild it when
	# it is missing or an hour old, so apt is not asked over the network for it
	if [ ! -f ${DESTDIR}/index.json ] || [ -n "$(find ${DESTDIR}/index.json -mmin +60 2> /dev/null)" ]; then
		changelog_fetch
	fi
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
