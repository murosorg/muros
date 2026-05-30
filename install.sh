#!/bin/bash
# MurOS installer - registers the signed apt repository (apt.muros.org)
# and installs the muros package on a fresh Debian 13 (trixie). Logs
# everything to /var/log/muros-install.log for later debugging.
#
# Usage:
#   curl -fsSL https://apt.muros.org/install.sh | sudo bash
#
# Optional variable:
#   MUROS_VERSION=0.9.0-rcN   install a specific version (it must still
#                             be available in the repo; the repo keeps
#                             only the latest version).

set -eu

LOG=/var/log/muros-install.log
APT_KEYRING=/usr/share/keyrings/muros-archive-keyring.gpg
APT_LIST=/etc/apt/sources.list.d/muros.list

if [ "$(id -u)" -ne 0 ]; then
  echo "Ce script doit etre lance en root (sudo bash install.sh)" >&2
  exit 1
fi

# Redirige stdout+stderr vers le fichier de log tout en gardant
# l'affichage temps reel a l'ecran.
exec > >(tee -a "$LOG") 2>&1
echo
echo "=============================================================="
echo "MurOS install - $(date -Is)"
echo "=============================================================="

echo "[1/4] Prerequisites"

# DNS preflight. A box that had MurOS's "Unbound as system resolver"
# enabled keeps /etc/resolv.conf pointing at 127.0.0.1; after an
# uninstall Unbound is gone, so every DNS lookup stalls on a dead local
# resolver and apt hangs at 0%. If resolv.conf has only a loopback
# nameserver, fall back to public resolvers so this installer (and apt)
# can actually reach the repository.
if [ -f /etc/resolv.conf ]; then
  RC_LOCAL=$(grep -E '^[[:space:]]*nameserver[[:space:]]+127\.' /etc/resolv.conf 2>/dev/null | wc -l)
  RC_PUBLIC=$(grep -E '^[[:space:]]*nameserver[[:space:]]+' /etc/resolv.conf 2>/dev/null \
              | grep -Ecv 'nameserver[[:space:]]+127\.')
  if [ "$RC_LOCAL" -gt 0 ] && [ "$RC_PUBLIC" -eq 0 ]; then
    echo "    -> /etc/resolv.conf only points at a local resolver; using public DNS for install"
    printf 'nameserver 1.1.1.1\nnameserver 8.8.8.8\n' > /etc/resolv.conf
  fi
fi

# Detection d'un etat dpkg cassé herite d'une MAJ ratée : si muros est
# marqué "ReinstReq" / "half-configured" / "half-installed", apt-get
# refuse de bouger tant qu'on n'a pas reinstalle, mais l'archive d'origine
# n'est plus dispo (cas typique : backend tue en plein postinst). On
# nettoie d'abord pour que la suite passe.
MUROS_STATUS=$(dpkg-query -W -f='${Status}' muros 2>/dev/null || true)
case "${MUROS_STATUS}" in
  *reinstreq*|*half-configured*|*half-installed*|*unpacked*|*triggers-pending*|*failed-config*)
    echo "    -> paquet muros en etat incoherent (${MUROS_STATUS}), force-remove avant install..."
    dpkg --remove --force-remove-reinstreq muros 2>/dev/null || true
    dpkg --purge --force-all muros 2>/dev/null || true
    rm -f /var/lib/dpkg/info/muros.* 2>/dev/null || true
    dpkg --configure -a 2>/dev/null || true
    ;;
esac

apt-get update -qq
apt-get install -y -qq curl ca-certificates gnupg

# Register the signed apt repository (apt.muros.org). The whole install,
# and every later upgrade through apt / unattended-upgrades, flows from
# here. A leading "v" in MUROS_VERSION is tolerated (tags are vX, the apt
# version is X).
echo "[2/4] Registering apt.muros.org"
install -d -m 0755 /usr/share/keyrings
if ! curl -fsSL https://apt.muros.org/muros.asc | gpg --dearmor --batch --yes -o "${APT_KEYRING}"; then
  echo "Cannot fetch the repository signing key from https://apt.muros.org" >&2
  echo "Check DNS / network and retry." >&2
  exit 1
fi
echo "deb [signed-by=${APT_KEYRING}] https://apt.muros.org stable main" > "${APT_LIST}"
apt-get update -qq
echo "    -> apt.muros.org registered"

# MurOS takes over the entire network/routing control plane (single
# source of truth = SQLite DB applied via iproute2). Competing managers
# must be uninstalled FIRST, otherwise apt refuses to install muros
# because of the Conflicts: line in debian/control.
#
# The kernel keeps the current IP and routes on the interfaces during
# this purge (typical DHCP lease is 24h+, so even if the renewer is
# gone for a few seconds, the IP stays). muros-boot then captures the
# kernel state in the DB at install time and replays it on every reboot.
echo "[3/4] Removing competing network managers"
PURGE_LIST=""
for pkg in network-manager network-manager-gnome network-manager-config-connectivity-debian \
           ifupdown resolvconf netplan.io \
           isc-dhcp-client dhcpcd5 dhcpcd-base \
           systemd-resolved \
           connman; do
  if dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q "install ok installed"; then
    PURGE_LIST="$PURGE_LIST $pkg"
  fi
done
if [ -n "$PURGE_LIST" ]; then
  echo "    Found:$PURGE_LIST"
  DEBIAN_FRONTEND=noninteractive apt-get purge -y --auto-remove $PURGE_LIST || \
    echo "    Warning: purge had errors, continuing anyway"
else
  echo "    None installed, nothing to do."
fi

echo "[4/4] Installing MurOS"
# If a previous uninstall stashed a data snapshot in /var/backups/muros,
# we restore it BEFORE installing so the new postinst sees an existing
# DB and skips first-boot seeding. Picks the most recent stash.
LATEST_STASH=""
if [ -d /var/backups/muros ] && [ ! -f /var/lib/muros/muros.db ]; then
  LATEST_STASH=$(ls -1dt /var/backups/muros/data-* 2>/dev/null | head -1)
  if [ -n "$LATEST_STASH" ]; then
    echo "    Restoring previous data snapshot: $LATEST_STASH"
    mkdir -p /var/lib/muros
    cp -a "$LATEST_STASH"/. /var/lib/muros/ 2>/dev/null || true
    echo "    (skip restore by deleting /var/backups/muros before reinstall)"
  fi
fi

# Block auto-start of feature daemons during apt install. The muros
# package ships every feature daemon (kea-dhcp4-server, unbound, snmpd,
# ...) as a hard Depends so the binaries are present from the start.
# Daemons that need a per-site configuration must stay dormant until the
# admin enables the corresponding feature from the UI. Debian otherwise
# starts daemons right after package configure with their stock config,
# which is unwanted for HA/VPN units. The no-config services (Kea,
# Unbound, chrony, snmpd) are (re)enabled cleanly by the MurOS postinst
# right after, with a MurOS-managed config.
#
# We install a selective policy-rc.d that returns 101 only for the
# feature daemons. muros core services (muros-backend, muros-boot,
# nginx, ...) start normally so the UI is reachable right after install.
# The file is removed at the end of the script (and a trap covers
# unexpected exits).
POLICY=/usr/sbin/policy-rc.d
POLICY_BAK="${POLICY}.muros-bak.$"
if [ -e "$POLICY" ]; then
  mv "$POLICY" "$POLICY_BAK"
fi
cat > "$POLICY" <<'EOF'
#!/bin/sh
# Installed by MurOS install.sh to block auto-start of feature daemons
# during apt install. Removed at the end of install.sh.
case "$1" in
  kea-dhcp4-server|unbound|snmpd|fail2ban|keepalived|conntrackd|\
  strongswan|strongswan-starter|strongswan-swanctl|\
  wg-quick@*|systemd-resolved)
    exit 101
    ;;
esac
exit 0
EOF
chmod +x "$POLICY"
trap 'rm -f "$POLICY"; if [ -e "$POLICY_BAK" ]; then mv "$POLICY_BAK" "$POLICY"; fi' EXIT INT TERM

if [ -n "${MUROS_VERSION:-}" ]; then
  # Tolerate a leading "v" (release tag form) in the requested version.
  WANT="${MUROS_VERSION#v}"
  echo "    -> installing muros=${WANT}"
  apt-get install -y "muros=${WANT}"
else
  apt-get install -y muros
fi

# Clean up the policy file; trap covers the case where apt-get fails.
rm -f "$POLICY"
if [ -e "$POLICY_BAK" ]; then mv "$POLICY_BAK" "$POLICY"; fi
trap - EXIT INT TERM

# Defensive sweep: make sure no CONFIG-REQUIRED feature daemon is left
# running or in a "failed" state after install. policy-rc.d blocks
# auto-start for daemons (re)configured during this apt transaction, but
# it cannot help when a daemon was already installed AND enabled before
# MurOS (it is not reconfigured, so its own postinst never runs again and
# it keeps whatever state it had). We stop, disable and clear the failed
# status of every config-required daemon so a fresh install always lands
# on a clean "inactive (dead)" baseline for those. The admin re-enables
# each from the UI once configured.
#
# Note: kea-dhcp4-server, unbound, snmpd, chrony and fail2ban are
# intentionally absent. They run without per-site configuration and
# belong to the default always-on stack (DHCP/DNS/NTP/SNMP + mgmt-plane
# protection); the MurOS postinst enables them, so they must keep running.
for svc in keepalived conntrackd \
           strongswan strongswan-starter wg-quick@wg0 \
           muros-watcher muros-wan-monitor; do
  if systemctl list-unit-files "${svc}.service" >/dev/null 2>&1; then
    systemctl disable --now "${svc}.service" 2>/dev/null || true
    systemctl reset-failed "${svc}.service" 2>/dev/null || true
  fi
done

VER=$(dpkg-query -W -f='${Version}' muros 2>/dev/null || echo "?")
IP=$(hostname -I 2>/dev/null | awk '{print $1}')
cat <<EOF

MurOS ${VER} installed.

  UI     : https://${IP:-<ip-vm>}/  (self-signed snakeoil cert, accept the browser warning)
  Login  : root  (use the existing root password; MurOS does not change it)
  Log    : ${LOG}

Checks:
  systemctl status muros-backend
  journalctl -u muros-backend -n 50 -f

Later upgrades:
  apt-get update && apt-get install --only-upgrade muros

Full uninstall (official, single method):
  curl -fsSL https://apt.muros.org/uninstall.sh | sudo bash

EOF
