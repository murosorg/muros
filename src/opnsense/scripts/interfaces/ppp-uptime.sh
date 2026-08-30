#!/bin/sh

# MurOS: seconds since the session came up. GNU stat and date, the BSD
# "date -j" and "stat -f %m" spellings do not exist on Debian.
if [ -f "/tmp/${1}_uptime" ]; then
	echo $(($(date +%s) - $(/usr/bin/stat -c %Y "/tmp/${1}_uptime")))
fi
