#!/bin/sh

# MurOS: adapter between the pppd hook directories of Debian
# (/etc/ppp/ip-up.d, ip-down.d, ipv6-up.d, ipv6-down.d) and the OPNsense link
# scripts, which expect the argument layout mpd5 used. pppd exports the session
# through the environment (PPP_IFACE, PPP_REMOTE, DNS1, DNS2), the positional
# arguments it passes are ignored here.
#
# Usage: pppd-hook.sh up|down inet|inet6

set -u

export PATH=/bin:/usr/bin:/usr/local/bin:/sbin:/usr/sbin:/usr/local/sbin

ACTION="${1:-}"
AF="${2:-}"
IF="${PPP_IFACE:-${IFNAME:-}}"

if [ -z "${IF}" ] || [ -z "${ACTION}" ] || [ -z "${AF}" ]; then
	exit 0
fi

SCRIPTS=/usr/local/opnsense/scripts/interfaces

if [ "${ACTION}" = "down" ]; then
	exec "${SCRIPTS}/ppp-linkdown.sh" "${IF}" "${AF}"
fi

# The link scripts read the peer address from the fourth argument and the
# nameservers from the sixth and seventh, in the "dns1 <address>" form.
REMOTE="${PPP_REMOTE:-}"
D1=""
D2=""
if [ -n "${DNS1:-}" ]; then
	D1="dns1 ${DNS1}"
fi
if [ -n "${DNS2:-}" ]; then
	D2="dns2 ${DNS2}"
fi

exec "${SCRIPTS}/ppp-linkup.sh" "${IF}" "${AF}" "" "${REMOTE}" "" "${D1}" "${D2}"
