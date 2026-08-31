#!/bin/sh
#
# MurOS firewall apply.
#
# Builds the nftables ruleset from the configuration, validates it in
# check mode, then loads it atomically with `nft -f`. The generated file
# replaces the `inet muros` table and only that one, so the tables owned by
# other parts of the system keep their contents across a reload. This is the Debian
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

# Several parts of the system register firewall rules of their own, without
# which they cannot work: the DHCP servers need the requests addressed to them
# to be let in, IPsec needs IKE and ESP from the peer, the captive portal needs
# name resolution before a client is authenticated. Those rules live in the
# plugin tree, not in the configuration file the generator reads, so collect
# them first. Best effort: a plugin that fails to answer must not stop the
# firewall from being applied, and the generator simply finds no file.
PLUGIN_RULES=/usr/local/opnsense/scripts/firewall/plugin_rules.php
if [ -r "$PLUGIN_RULES" ]; then
    php "$PLUGIN_RULES" /var/etc/muros/plugin_rules.json > /dev/null 2>&1 || true
fi

php "$BUILD" "$CONFIG" > "$RULES"
nft -c -f "$RULES"
nft -f "$RULES"

# The captive portal keeps its enforcement (redirect, forward gate and portal
# input rules) in a dedicated `inet captiveportal` table. That table is left
# untouched by the load above, which replaces `inet muros` alone, so the
# authenticated clients and their traffic counters survive a firewall reload.
# The rebuild below only reconciles the enforcement with the configuration,
# picking up a zone whose interfaces or allowed addresses changed. Best
# effort: a captive portal problem must never fail the firewall reload.
CP_SETUP=/usr/local/opnsense/scripts/captiveportal/setup_fw.py
if [ -x "$CP_SETUP" ] && grep -q '<captiveportal>' "$CONFIG" 2> /dev/null; then
    "$CP_SETUP" "$CONFIG" > /dev/null 2>&1 || true
fi

# Policy based routing (route-to and reply-to): the ruleset above only tags the
# flows with a gateway mark, the mark is worthless until the matching routing
# table and ip rule exist. Reconcile them from the configuration on every
# reload, which also picks up a gateway whose address or interface changed.
# Best effort: a routing provisioning problem must not fail the firewall
# reload, the ruleset itself is already committed.
POLICY_ROUTING=/usr/local/opnsense/scripts/routes/setup_policy_routing.php
if [ -f "$POLICY_ROUTING" ]; then
    php "$POLICY_ROUTING" --auto > /dev/null 2>&1 || true
fi
