#!/bin/sh

# Copyright (C) 2015-2026 Franco Fichtner <franco@opnsense.org>
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

# This script generates a json structured file with the following content:
#
# connection: error|unauthenticated|misconfigured|unresolved|ok
# repository: error|untrusted|unsigned|revoked|incomplete|forbidden|ok
# last_check: <date_time_stamp>
# download_size: <size_of_total_downloads>[,<size_of_total_downloads>]
# new_packages: array with { name: <package_name>, version: <package_version> }
# reinstall_packages: array with { name: <package_name>, version: <package_version> }
# remove_packages: array with { name: <package_name>, version: <package_version> }
# downgrade_packages: array with { name: <package_name>, current_version: <current_version>, new_version: <new_version> }
# upgrade_packages: array with { name: <package_name>, current_version: <current_version>, new_version: <new_version> }

# clear the file before we may wait for other init glue below
JSONFILE="/tmp/pkg_upgrade.json"
rm -f ${JSONFILE}

REQUEST="CHECK FOR UPDATES"

. /usr/local/opnsense/scripts/firmware/config.sh

LICENSEFILE="/usr/local/opnsense/version/core.license"
OUTFILE="/tmp/pkg_update.out"

CUSTOMPKG=${1}

base_to_reboot=
connection="error"
download_size=
force_all=
itemcount=0
kernel_to_reboot=
last_check="unknown"
linecount=0
needs_reboot="0"
packages_downgraded=
packages_new=
packages_upgraded=
product_repo="OPNsense"
repository="error"
sets_upgraded=
upgrade_needs_reboot="0"

product_reboot=$(/usr/local/sbin/pluginctl -g system.firmware.reboot)
if [ "${product_reboot}" = "1" ]; then
	needs_reboot="1"
fi

product_suffix="-$(/usr/local/sbin/pluginctl -g system.firmware.type)"
if [ "${product_suffix}" = "-" ]; then
    product_suffix=
fi

last_check=$(date)
os_version=$(uname -sr)
product_id=$(opnsense-version -n)
product_target=opnsense${product_suffix}
product_version=$(opnsense-version -v)
product_abi=$(opnsense-version -a)
product_xabi=$(opnsense-version -x)

if [ -n "${product_xabi}" -a "${product_abi}" != "${product_xabi}" ]; then
    force_all="-f"
fi

# --- Debian / apt update check -----------------------------------------------
# Upstream drives this with pkg(8) and opnsense-update; on Debian the package
# manager is apt/dpkg. We refresh the repository metadata, classify the
# connection and repository state from what apt reports, then enumerate the
# pending changes from a dry-run dist-upgrade. The emitted JSON contract is
# unchanged so the firmware GUI keeps working as-is.

packages_reinstall=
packages_removed=
upgrade_major_message=
upgrade_major_version=

: > ${OUTFILE}

output_txt -n "Updating repository metadata, please wait... "
output_cmd -o ${OUTFILE} apt-get -o Dpkg::Use-Pty=0 update
output_txt "done"

if grep -qiE 'Temporary failure resolving|Could not resolve host' ${OUTFILE}; then
    connection="unresolved"
elif grep -qiE 'Failed to fetch|Could not connect|Connection timed out|Unable to connect|Cannot initiate the connection|Network is unreachable' ${OUTFILE}; then
    connection="error"
elif grep -qiE 'is not signed|no longer signed|NO_PUBKEY|GPG error|following signatures couldn|does not have a Release file' ${OUTFILE}; then
    connection="ok"
    repository="unsigned"
elif grep -qiE ' 401 | 403 |Forbidden|Authentication failure' ${OUTFILE}; then
    connection="ok"
    repository="forbidden"
else
    connection="ok"
    repository="ok"
fi

if [ "${connection}" = "ok" ] && [ "${repository}" = "ok" ]; then
    SIMFILE="/tmp/pkg_update.sim"
    # A single solver pass: with dist-upgrade, --print-uris keeps apt's
    # Inst/Conf/Remv plan lines (consumed by parse-upgrade.awk) and additionally
    # appends the download URIs and their sizes. We used to run the dependency
    # solver twice (once for the package list, once for the size); reusing one
    # output halves that cost on the synchronous status check.
    apt-get -s -o Dpkg::Use-Pty=0 --print-uris dist-upgrade > ${SIMFILE} 2>/dev/null

    # split apt's Inst/Remv lines into the JSON arrays the GUI expects
    KERN=$(awk -f ${BASEDIR}/parse-upgrade.awk ${SIMFILE})
    packages_upgraded=$(cat /tmp/fw_parse_upg 2>/dev/null)
    packages_new=$(cat /tmp/fw_parse_new 2>/dev/null)
    packages_removed=$(cat /tmp/fw_parse_rem 2>/dev/null)
    rm -f /tmp/fw_parse_upg /tmp/fw_parse_new /tmp/fw_parse_rem

    # total download size of the pending .deb archives, rendered for display
    size_bytes=$(awk '/^.?http/ || /^.?file:/ || /^.?cdrom:/ {s+=$3} END{printf "%.0f", s+0}' ${SIMFILE})
    if [ -n "${size_bytes}" ] && [ "${size_bytes}" != "0" ]; then
        download_size=$(numfmt --to=iec --suffix=B "${size_bytes}" 2>/dev/null)
    fi

    # a fresh kernel, or a maintainer-flagged change, needs a reboot to apply
    if [ "${KERN}" = "1" ] || [ -e /var/run/reboot-required ]; then
        needs_reboot="1"
    fi
fi

# write our json structure
cat > ${JSONFILE} << JSON
{
    "api_version":"2",
    "connection":"${connection}",
    "downgrade_packages":[${packages_downgraded}],
    "download_size":"${download_size}",
    "last_check":"${last_check}",
    "needs_reboot":"${needs_reboot}",
    "new_packages":[${packages_new}],
    "os_version":"${os_version}",
    "product_id":"${product_id}",
    "product_target":"${product_target}",
    "product_version":"${product_version}",
    "product_abi":"${product_xabi}",
    "reinstall_packages":[${packages_reinstall}],
    "remove_packages":[${packages_removed}],
    "repository":"${repository}",
    "upgrade_major_message":"${upgrade_major_message}",
    "upgrade_major_version":"${upgrade_major_version}",
    "upgrade_needs_reboot":"${upgrade_needs_reboot}",
    "upgrade_packages":[${packages_upgraded}],
    "upgrade_sets":[${sets_upgraded}]
}
JSON

output_done
