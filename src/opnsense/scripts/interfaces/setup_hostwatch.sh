#!/bin/sh

# MurOS: the database and run directories of the host discovery daemon.
#
# FreeBSD handed them to the hostd user the hostwatch package created, an
# account Debian has no reason to know about, so every start failed on the
# chown. The daemon needs a packet socket and stays root, so the directories
# belong to root and are only readable by it.

for DIR in /var/db/hostwatch /var/run/hostwatch; do
	mkdir -p ${DIR}
	chown -R root:root ${DIR}
	chmod 750 ${DIR}
done
