#!/bin/sh

# MurOS: prepare the control file before monit reads it.
#
# monit refuses to start when its control file is readable by anyone else, and
# it aborts when an include path does not resolve, so the directory holding the
# user supplied fragments has to exist even when the operator never dropped a
# file in it. The Debian package of monit owns /etc/monit, MurOS renders its own
# control file under /usr/local/etc and drives the daemon from there.

mkdir -p /usr/local/etc/monit.opnsense.d
chmod 600 /usr/local/etc/monitrc
