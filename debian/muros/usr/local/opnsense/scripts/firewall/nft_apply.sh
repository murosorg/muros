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
