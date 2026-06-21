#!/bin/sh

# Copyright (C) 2017-2024 Franco Fichtner <franco@opnsense.org>
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

REQUEST="AUDIT HEALTH"

. /usr/local/opnsense/scripts/firmware/config.sh

CMD=${1}

export DEBIAN_FRONTEND=noninteractive

CORE=$(opnsense-version -n)

# The FreeBSD build verified the base/kernel "sets" against signed mtree
# manifests shipped by opnsense-update. On Debian those sets do not exist; the
# base system and the kernel are ordinary dpkg packages, so the equivalent
# integrity check is dpkg --verify on the matching package.
set_check()
{
	SET=${1}

	if [ "${SET}" = "kernel" ]; then
		PKGNAME=$(dpkg-query -W -f='${Package}\n' 'linux-image-[0-9]*' 2>/dev/null | tail -n 1)
		[ -z "${PKGNAME}" ] && PKGNAME=linux-image-amd64
	else
		PKGNAME=${CORE}
	fi

	output_txt ">>> Check installed ${SET} package (${PKGNAME})"

	if [ -z "${PKGNAME}" ] || ! dpkg-query -W "${PKGNAME}" >/dev/null 2>&1; then
		output_txt "Cannot verify ${SET}: package ${PKGNAME} is not installed."
		return
	fi

	output_txt "Installed version: $(dpkg-query -W -f='${Version}' "${PKGNAME}")"

	output_txt ">>> Check for missing or altered ${SET} files"

	VERIFY=$(dpkg --verify "${PKGNAME}" 2>/dev/null)
	if [ -z "${VERIFY}" ]; then
		output_txt "No problems detected."
	else
		output_txt "${VERIFY}"
	fi
}

# Verify the core meta package and the packages it depends on are all present.
# The FreeBSD original also checked pkg repository annotations and the
# automatic/vital flags; those are pkg-only concepts with no apt counterpart.
core_check()
{
	output_txt ">>> Check for core package consistency"

	if [ -z "${CORE}" ]; then
		output_txt "Could not determine core package name."
		return
	fi

	if ! dpkg-query -W "${CORE}" >/dev/null 2>&1; then
		output_txt "Core package \"${CORE}\" is not installed."
		return
	fi

	DEPS=$(apt-cache depends --installed --important "${CORE}" 2>/dev/null | \
		awk '/Depends:/ { print $2 }' | grep -v '^<' | sort -u)
	NDEPS=$(printf '%s\n' ${DEPS} | grep -c .)

	output_txt "Core package \"${CORE}\" at $(dpkg-query -W -f='${Version}' "${CORE}") has ${NDEPS} dependencies to check."

	PROGRESS=
	MISSING=0

	for DEP in ${CORE} ${DEPS}; do
		if [ -z "${PROGRESS}" ]; then
			output_txt -n "Checking packages: ."
			PROGRESS=1
		else
			output_txt -n "."
		fi

		if ! dpkg-query -W -f='${Status}' "${DEP}" 2>/dev/null | grep -q 'install ok installed'; then
			[ -n "${PROGRESS}" ] && output_txt
			output_txt "Package not installed: ${DEP}"
			PROGRESS=
			MISSING=$((MISSING + 1))
		fi
	done

	if [ -n "${PROGRESS}" ]; then
		output_txt " done"
	fi

	if [ "${MISSING}" = "0" ]; then
		output_txt "All core dependencies are installed."
	fi
}

output_txt ">>> Root file system: $(mount | awk '$3 == "/" { print $1 }')"

if [ -z "${CMD}" -o "${CMD}" = "kernel" ]; then
	set_check kernel
fi

if [ -z "${CMD}" -o "${CMD}" = "base" ]; then
	set_check base
fi

if [ -z "${CMD}" -o "${CMD}" = "repos" ]; then
	output_txt ">>> Check configured repositories"
	output_cmd sh -c 'cat /etc/apt/sources.list 2>/dev/null; cat /etc/apt/sources.list.d/*.list /etc/apt/sources.list.d/*.sources 2>/dev/null'
fi

if [ -z "${CMD}" -o "${CMD}" = "plugins" ]; then
	output_txt ">>> Check installed plugins"
	PLUGINS=$(dpkg-query -W -f='${Package} ${Version}\n' 'os-*' 2>/dev/null)
	if [ -n "${PLUGINS}" ]; then
		output_txt "${PLUGINS}"
	else
		output_txt "No plugins found."
	fi
fi

if [ -z "${CMD}" -o "${CMD}" = "locked" ]; then
	output_txt ">>> Check held packages"
	LOCKED=$(apt-mark showhold 2>/dev/null)
	if [ -n "${LOCKED}" ]; then
		output_txt "${LOCKED}"
	else
		output_txt "No holds found."
	fi
fi

if [ -z "${CMD}" -o "${CMD}" = "packages" ]; then
	output_txt ">>> Check for broken package dependencies"
	output_cmd sh -c 'apt-get -o Dpkg::Use-Pty=0 check 2>&1 || true'

	output_txt ">>> Check for missing or altered package files"
	if command -v debsums >/dev/null 2>&1; then
		output_cmd sh -c 'debsums -s 2>&1 || true'
	else
		output_txt "debsums is not installed; verifying packaged files through dpkg instead."
		output_cmd sh -c 'dpkg --verify 2>&1 || true'
	fi
fi

if [ -z "${CMD}" -o "${CMD}" = "core" ]; then
	core_check
fi

output_done
