#!/bin/sh

# MurOS: when an OpenVPN client disconnects, drop the netfilter connection
# tracking entries tied to the address it was using so stale flows do not
# linger. This replaces the FreeBSD `pfctl -k <host>` / `-K <host>` state kill
# (by source, then by destination) with the conntrack(8) equivalent.

CONNTRACK="/usr/sbin/conntrack"

if [ -n "${ifconfig_pool_remote_ip}" ] && [ -x "${CONNTRACK}" ]; then
	"${CONNTRACK}" -D -s "${ifconfig_pool_remote_ip}" >/dev/null 2>&1
	"${CONNTRACK}" -D -d "${ifconfig_pool_remote_ip}" >/dev/null 2>&1
fi

exit 0
