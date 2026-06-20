#!/bin/sh

# Copyright (C) 2026 MurOS
#
# Control the Debian Kea systemd units from the enabled flags rendered into
# keactrl.conf by the configd template. Each Kea daemon maps to a stock Debian
# unit (which reads its configuration from /etc/kea); only the daemons enabled
# in the MurOS configuration are (re)started, the rest are stopped. This keeps
# a single control path for both the Services page and the apply/reconfigure
# flow, replacing the FreeBSD rc.d/keactrl mechanism that does not exist on
# Debian.

set -u

KEACTRL_CONF="/usr/local/etc/kea/keactrl.conf"

# Defaults: nothing runs unless the rendered configuration enables it.
dhcp4=no
dhcp6=no
dhcp_ddns=no
ctrl_agent=no

# shellcheck disable=SC1090
[ -r "$KEACTRL_CONF" ] && . "$KEACTRL_CONF"

unit_for() {
    case "$1" in
        dhcp4)      echo kea-dhcp4-server.service ;;
        dhcp6)      echo kea-dhcp6-server.service ;;
        dhcp_ddns)  echo kea-dhcp-ddns-server.service ;;
        ctrl_agent) echo kea-ctrl-agent.service ;;
    esac
}

# Servers first, control agent last (it connects to the server sockets).
DAEMONS="dhcp4 dhcp6 dhcp_ddns ctrl_agent"

enabled() {
    eval "_v=\${$1}"
    [ "$_v" = "yes" ]
}

apply() {
    _mode="$1"
    for _d in $DAEMONS; do
        _unit="$(unit_for "$_d")"
        if enabled "$_d"; then
            /usr/bin/systemctl "$_mode" "$_unit"
        else
            /usr/bin/systemctl stop "$_unit" 2>/dev/null || true
        fi
    done
}

stop_all() {
    for _d in $DAEMONS; do
        /usr/bin/systemctl stop "$(unit_for "$_d")" 2>/dev/null || true
    done
}

status() {
    _rc=1
    for _d in $DAEMONS; do
        enabled "$_d" || continue
        _unit="$(unit_for "$_d")"
        if /usr/bin/systemctl is-active --quiet "$_unit"; then
            echo "$_unit is running"
            _rc=0
        else
            echo "$_unit is not running"
        fi
    done
    [ "$_rc" -eq 0 ] || echo "kea is not running"
    return "$_rc"
}

case "${1:-}" in
    start)        apply start ;;
    restart)      apply restart ;;
    reload)       apply restart ;;
    stop)         stop_all ;;
    status)       status ;;
    *) echo "usage: $0 {start|stop|restart|reload|status}" >&2; exit 1 ;;
esac
