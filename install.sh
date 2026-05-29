#!/bin/bash
# MurOS installer - telecharge le dernier .deb de la release GitHub
# et l'installe sur une VM Debian 13 (trixie). Logge tout dans
# /var/log/muros-install.log pour pouvoir debug a posteriori.
#
# Usage :
#   curl -fsSL https://github.com/murosorg/muros/releases/latest/download/install.sh | sudo bash
#
# Variable optionnelle :
#   MUROS_VERSION=v0.9.0   force a specific version (otherwise latest)

set -eu

REPO="murosorg/muros"
LOG=/var/log/muros-install.log

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

echo "[1/4] Prerequis"

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
apt-get install -y -qq curl ca-certificates

echo "[2/4] Resolving version"
# Why the atom feed and not /releases/latest:
# - /releases/latest excludes pre-releases. While MurOS is in the 0.9.0
#   rc cycle every release is flagged pre-release, so /releases/latest
#   returns an outdated tag (it sticks to the last non-prerelease).
# - api.github.com is rate-limited (60 req/h per IP unauthenticated)
#   and returns 403 when the admin re-runs the installer a few times.
# - /releases.atom has no rate limit, is served from the GitHub CDN,
#   and lists every release (including pre-releases) sorted by date.
# We grep the first <link href="...releases/tag/vXXX"> entry.
if [ -n "${MUROS_VERSION:-}" ]; then
  TAG="${MUROS_VERSION}"
else
  TAG=$(curl -fsSL "https://github.com/${REPO}/releases.atom" 2>/dev/null \
    | grep -m1 -oE 'releases/tag/v[^/"<]+' \
    | head -1 \
    | sed 's|releases/tag/||')
  # Fallback to /releases/latest in case the atom feed format ever
  # changes (HTML structure under github.com is more stable than
  # parsing XML).
  if [ -z "${TAG}" ]; then
    EFFECTIVE_URL=$(curl -fsSLI -o /dev/null -w '%{url_effective}' \
      "https://github.com/${REPO}/releases/latest" || true)
    TAG="${EFFECTIVE_URL##*/tag/}"
    case "${TAG}" in v*) ;; *) TAG="" ;; esac
  fi
fi
if [ -z "${TAG}" ]; then
  echo "Cannot determine the version to install (network issue or repo unreachable)" >&2
  echo "You can force a specific version: MUROS_VERSION=v0.9.0-rcN curl ... | sudo bash" >&2
  exit 1
fi
VER="${TAG#v}"
DEB="muros_${VER}_all.deb"
URL="https://github.com/${REPO}/releases/download/${TAG}/${DEB}"
echo "    -> ${TAG}"

echo "[3/4] Telechargement"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
curl -fL --progress-bar -o "${TMP}/${DEB}" "${URL}"

# MurOS takes over the entire network/routing control plane (single
# source of truth = SQLite DB applied via iproute2). Competing managers
# must be uninstalled FIRST, otherwise apt refuses to install muros
# because of the Conflicts: line in debian/control.
#
# The kernel keeps the current IP and routes on the interfaces during
# this purge (typical DHCP lease is 24h+, so even if the renewer is
# gone for a few seconds, the IP stays). muros-boot then captures the
# kernel state in the DB at install time and replays it on every reboot.
echo "[4/5] Removing competing network managers"
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

echo "[5/5] Installing MurOS"
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
# package ships every feature daemon (dnsmasq, unbound, snmpd, ...) as
# a hard Depends so the binaries are present from the start, but those
# services must stay dormant until the admin enables the corresponding
# feature from the UI. Debian otherwise starts daemons right after
# package configure, which causes collisions on port 53 between
# dnsmasq's default config and unbound's default config.
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
  dnsmasq|unbound|snmpd|fail2ban|keepalived|conntrackd|\
  strongswan|strongswan-starter|strongswan-swanctl|\
  wg-quick@*|systemd-resolved)
    exit 101
    ;;
esac
exit 0
EOF
chmod +x "$POLICY"
trap 'rm -f "$POLICY"; if [ -e "$POLICY_BAK" ]; then mv "$POLICY_BAK" "$POLICY"; fi' EXIT INT TERM

apt-get install -y "${TMP}/${DEB}"

# Clean up the policy file; trap covers the case where apt-get fails.
rm -f "$POLICY"
if [ -e "$POLICY_BAK" ]; then mv "$POLICY_BAK" "$POLICY"; fi
trap - EXIT INT TERM

IP=$(hostname -I 2>/dev/null | awk '{print $1}')
cat <<EOF

MurOS ${TAG} installe.

  UI     : https://${IP:-<ip-vm>}/  (cert snakeoil auto-signe, accepter le warning navigateur)
  Login  : admin / muros  (changement de mot de passe au premier login)
  Log    : ${LOG}

Verifs :
  systemctl status muros-backend
  journalctl -u muros-backend -n 50 -f

Desinstallation complete (methode officielle, unique) :
  curl -fsSL https://github.com/${REPO}/releases/latest/download/uninstall.sh | sudo bash

EOF
