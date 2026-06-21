#!/bin/sh
#
# MurOS firewall apply.
#
# Builds the nftables ruleset from the configuration, validates it in
# check mode, then loads it atomically with `nft -f`. This is the Debian
# replacement for the FreeBSD `pfctl -f` reload performed by filter.inc.
# Validating before committing guarantees a malformed ruleset can never
# replace a working one.
#
set -eu

CONFIG="${1:-/conf/config.xml}"
RUNDIR=/run/muros
RULES="$RUNDIR/rules.nft"
BUILD=/usr/local/opnsense/scripts/firewall/nft_build.php

mkdir -p "$RUNDIR"
php "$BUILD" "$CONFIG" > "$RULES"
nft -c -f "$RULES"
nft -f "$RULES"

# The captive portal keeps its enforcement (redirect, forward gate and portal
# input rules) in a dedicated `inet captiveportal` table. The `flush ruleset`
# performed by the rules above wipes it, so rebuild it from the configuration
# once the main ruleset is committed. Best effort: a captive portal problem
# must never fail the firewall reload, and the authenticated client sets are
# repopulated by the captive portal background process on its next cycle.
CP_SETUP=/usr/local/opnsense/scripts/captiveportal/setup_fw.py
if [ -x "$CP_SETUP" ] && grep -q '<captiveportal>' "$CONFIG" 2> /dev/null; then
    "$CP_SETUP" "$CONFIG" > /dev/null 2>&1 || true
fi
