#!/bin/sh

# MurOS: the collector and the aggregator are systemd units, flowd and its rc
# script are gone. The log file name is kept: the in tree collector writes the
# same record format to the same place, so the aggregator reads it unchanged.
if [ "$1" = "all" ]; then
    echo "flush all local netflow data"
    systemctl stop muros-netflow-aggregate muros-netflow-collector
    rm -f /var/netflow/*.sqlite
    rm -f /var/log/flowd.log*
    systemctl start muros-netflow-collector muros-netflow-aggregate
else
    echo "not flushing local netflow data, provide all as parameter to do so"
fi
