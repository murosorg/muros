#!/bin/sh
# MurOS offline post-install, run from preseed/late_command in the d-i
# environment (not in-target). Installs muros from the on-disc pool and
# the Python wheelhouse with no network, then restores clean online apt
# sources for the installed system.
#
# Kept as a real file (not an inline late_command) so the shell quoting
# stays sane: an inline multi-line command was misparsed by debconf.
set -e

SRC=/cdrom/muros
TPOOL=/target/var/cache/muros-pool

# 1. Copy the wheelhouse and the package pool into the target.
mkdir -p /target/opt/muros/wheelhouse "$TPOOL"
cp -a "$SRC"/wheelhouse/. /target/opt/muros/wheelhouse/ 2>/dev/null || true
cp -a "$SRC"/pool/. "$TPOOL"/ 2>/dev/null || true

# 2. Use ONLY the local pool during install: no network is available, so
#    any Debian/live apt source would make apt hang on timeouts.
rm -f /target/etc/apt/sources.list.d/* 2>/dev/null || true
: > /target/etc/apt/sources.list
echo 'deb [trusted=yes] file:/var/cache/muros-pool ./' > /target/etc/apt/sources.list.d/muros-offline.list

# 3. Install muros and its dependencies from the local pool.
in-target apt-get -o Acquire::Languages=none -o Acquire::Retries=0 update || true
in-target env DEBIAN_FRONTEND=noninteractive DEBCONF_NONINTERACTIVE_SEEN=true \
  apt-get install -y \
    -o Acquire::Retries=0 -o Acquire::http::Timeout=5 -o Acquire::https::Timeout=5 \
    muros

# 3b. Appliance mode: enable the data-plane units so the box assigns its
#     interfaces and loads the firewall on first boot. The package ships
#     them disabled on purpose (an apt install on an existing host must
#     never steal the uplink it is reached on); on a dedicated appliance we
#     want them, and there is no reserved management interface, so the
#     reserved list is left empty and every physical NIC is assignable.
mkdir -p /target/usr/local/etc/muros
: > /target/usr/local/etc/muros/reserved.conf
in-target systemctl enable muros-interface-assign.service muros-interfaces.service muros-firewall.service || true

# 4. Restore clean online sources for the installed system (used later,
#    when it has a WAN), and drop the offline pool source.
rm -f /target/etc/apt/sources.list.d/muros-offline.list
cat > /target/etc/apt/sources.list <<'SRCLIST'
deb http://deb.debian.org/debian trixie main contrib non-free-firmware
deb http://security.debian.org/debian-security trixie-security main contrib non-free-firmware
deb http://deb.debian.org/debian trixie-updates main contrib non-free-firmware
SRCLIST

# 5. Register the signed MurOS apt repository so the installed system
#    receives MurOS updates online once it reaches a WAN, exactly like the
#    install.sh path. This is offline-safe: build-iso.sh stages the
#    pre-dearmored keyring on the ISO, so we only copy a file here (no
#    network, no gpg in the d-i environment). If the keyring is missing
#    (e.g. it could not be fetched at build time), we skip silently and
#    the operator can still register the repo by hand later.
if [ -f "$SRC/muros-archive-keyring.gpg" ]; then
  mkdir -p /target/usr/share/keyrings
  cp "$SRC/muros-archive-keyring.gpg" /target/usr/share/keyrings/muros-archive-keyring.gpg
  chmod 0644 /target/usr/share/keyrings/muros-archive-keyring.gpg
  echo 'deb [signed-by=/usr/share/keyrings/muros-archive-keyring.gpg] https://download.muros.org stable main' \
    > /target/etc/apt/sources.list.d/muros.list
fi

exit 0
