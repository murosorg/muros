#!/bin/sh

SURICATA_DIRS="/var/log/suricata /usr/local/etc/suricata/conf.d"

for SURICATA_DIR in ${SURICATA_DIRS}; do
	mkdir -p ${SURICATA_DIR}
	chown -R root:root ${SURICATA_DIR}
	chmod -R 0700 ${SURICATA_DIR}
done

# make sure we can load our yaml file if we don't have rules installed yet
touch /usr/local/etc/suricata/installed_rules.yaml

# ensure the netfilter queue module is available so Suricata inline IPS mode
# (nftables 'queue' target / NFQUEUE) can be used. On FreeBSD this loaded the
# ipdivert kernel module for divert sockets.
modprobe nfnetlink_queue 2>/dev/null || true
