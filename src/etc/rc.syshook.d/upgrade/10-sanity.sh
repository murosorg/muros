#!/bin/sh

# MurOS: upgrade sanity checks against dpkg, the FreeBSD package manager is not
# part of this platform.

CORE=$(opnsense-version -n)

if [ -z "${CORE}" ]; then
	echo "Could not determine core package name."
	exit 1
fi

if [ ! -x /usr/bin/dpkg-query ]; then
	echo "No package manager is installed to perform upgrades."
	exit 1
fi

if [ ! -x /usr/bin/apt-get ]; then
	echo "No package manager is installed to perform upgrades."
	exit 1
fi

if [ "$(dpkg-query -W -f='${db:Status-Status}' "${CORE}" 2> /dev/null)" != "installed" ]; then
	echo "Core package \"${CORE}\" not known to package database."
	exit 1
fi

# A half configured dpkg database makes any upgrade fail in the middle, which is
# the situation the pkg reinstall test used to catch.
if [ -n "$(dpkg-query -W -f='${binary:Package} ${db:Status-Status}\n' 2> /dev/null | awk '$2 != "installed" && $2 != "not-installed" && $2 != "config-files" {print $1}')" ]; then
	echo "The package database is not in a clean state, run \"dpkg --configure -a\" first."
	exit 1
fi

echo "Passed all upgrade tests."

exit 0
