#!/bin/bash
# MurOS uninstaller - safe defaults.
#
# Default flow :
#   1. Stop + disable MurOS services and optional services (keepalived,
#      strongswan, wireguard, snmpd) only used via the MurOS UI
#   2. Snapshot /var/lib/muros to /var/backups/muros/data-<timestamp>
#      so the DB and backups can be restored by the next install.sh
#      (unless MUROS_PURGE_DATA=1)
#   3. apt-get purge muros (postrm wipes /opt/muros, DB, configs)
#   4. Cleanup config drop-ins written by MurOS outside the package
#   5. Restore /etc/network/interfaces from the .muros-bak backup
#   6. Unmask native network services (networking, systemd-networkd, NM)
#
# Dependency packages (keepalived, strongswan, wireguard, nginx,
# fail2ban, snmpd...) stay installed but inactive. Purge them with
# MUROS_PURGE_DEPS=1, which is opt-in (may pull shared libs used
# elsewhere).
#
# Everything is logged to /var/log/muros-install.log.
#
# Usage (official, single method) :
#   curl -fsSL https://apt.muros.org/uninstall.sh | sudo bash
#
# Optional vars :
#   MUROS_PURGE_DEPS=1   also purge keepalived/strongswan/wireguard/etc.
#                        (default : off, safer)
#   MUROS_PURGE_DATA=1   wipe /var/lib/muros AND /var/backups/muros
#                        so the next install starts from a clean slate
#                        (default : off, data is preserved for reinstall)

set -eu

LOG=/var/log/muros-install.log
PURGE_DEPS="${MUROS_PURGE_DEPS:-0}"

if [ "$(id -u)" -ne 0 ]; then
  echo "Doit etre lance en root" >&2
  exit 1
fi

exec > >(tee -a "$LOG") 2>&1
echo
echo "=============================================================="
echo "MurOS uninstall - $(date -Is)"
echo "=============================================================="

echo "[1/4] Arret des services MurOS et des services optionnels"
# Services MurOS
for s in muros-backend muros-watcher muros-boot; do
  systemctl stop "$s.service" 2>/dev/null || true
  systemctl disable "$s.service" 2>/dev/null || true
done
# Services optionnels qui n'ont de sens qu'avec MurOS (gere via l'UI).
# On les arrete et disable, mais on laisse le paquet en place.
for s in keepalived conntrackd strongswan strongswan-starter \
         snmpd muros-watcher; do
  systemctl stop "$s.service" 2>/dev/null || true
  systemctl disable "$s.service" 2>/dev/null || true
done
systemctl stop "wg-quick@wg0.service" 2>/dev/null || true
systemctl disable "wg-quick@wg0.service" 2>/dev/null || true

echo "[2/4] Purge du paquet muros (dpkg postrm s'occupe de /opt, /etc, DB)"
export DEBIAN_FRONTEND=noninteractive
# Optional snapshot of /var/lib/muros before any purge action. Lets us
# restore the DB if the user reinstalls and wants the previous config
# back. Off if MUROS_PURGE_DATA=1 (user explicitly asks for wipe).
if [ "${MUROS_PURGE_DATA:-0}" != "1" ] && [ -d /var/lib/muros ]; then
  BACKUP_DIR="/var/backups/muros"
  STAMP=$(date -u +%Y%m%dT%H%M%SZ)
  mkdir -p "$BACKUP_DIR"
  if cp -a /var/lib/muros "$BACKUP_DIR/data-$STAMP" 2>/dev/null; then
    echo "    Data snapshot kept at $BACKUP_DIR/data-$STAMP"
    echo "    (it will be auto-restored by install.sh on the next reinstall)"
  fi
fi

if dpkg -l muros >/dev/null 2>&1; then
  # Degraded case: if an upgrade was killed mid-way, dpkg keeps muros in
  # "reinstall required" state and apt refuses to move forward until we
  # reinstall it, but the archive is gone. Try apt-get purge first, then
  # dpkg --purge, then dpkg --remove --force-remove-reinstreq which
  # nukes a "ReinstReq" package without requiring the archive.
  if ! apt-get purge -y muros 2>/dev/null; then
    if ! dpkg --purge muros 2>/dev/null; then
      echo "    package in inconsistent state, force-remove..."
      dpkg --remove --force-remove-reinstreq muros 2>/dev/null || true
      dpkg --purge --force-all muros 2>/dev/null || true
    fi
  fi
else
  echo "    muros package not installed"
fi

# If user explicitly asked to wipe, also remove our stashed snapshots
# (otherwise the next install would restore them).
if [ "${MUROS_PURGE_DATA:-0}" = "1" ]; then
  rm -rf /var/backups/muros 2>/dev/null || true
fi

# Nettoyage des entrees dpkg/apt qui pourraient resister (paquet
# fantome en /var/lib/dpkg/info/muros.* apres force-remove)
rm -f /var/lib/dpkg/info/muros.* 2>/dev/null || true
# Force apt a re-evaluer l'etat
dpkg --configure -a 2>/dev/null || true

if [ "$PURGE_DEPS" = "1" ]; then
  echo "[3/4] MUROS_PURGE_DEPS=1 : purge des dependances optionnelles"
  # Liste strictement MurOS-specifique. Pas de purge sur nginx, ssl-cert,
  # rsync, nftables, iproute2 ou les paquets de build : trop de risque
  # d'effets de bord sur le reste du systeme.
  DEPS="
    keepalived conntrackd
    strongswan strongswan-starter strongswan-swanctl
    libcharon-extra-plugins libstrongswan-extra-plugins
    wireguard-tools wireguard
    fail2ban snmpd snmp
  "
  for p in $DEPS; do
    if dpkg -l "$p" >/dev/null 2>&1; then
      echo "    -> purge $p"
      apt-get purge -y "$p" 2>/dev/null || true
    fi
  done
else
  echo "[3/4] Dependances conservees (utiliser MUROS_PURGE_DEPS=1 pour les virer)"
fi

echo "[4/4] Nettoyage des drop-ins ecrits par MurOS hors paquet"

# Repertoires applicatifs MurOS (la DB, les backups, les cache)
rm -rf /opt/muros /var/lib/muros /etc/muros /var/cache/muros

# Depot apt signe enregistre par install.sh (apt.muros.org)
rm -f /etc/apt/sources.list.d/muros.list
rm -f /usr/share/keyrings/muros-archive-keyring.gpg

# IPsec : drop-ins swanctl ecrits par MurOS (les confs du paquet
# strongswan restent intactes)
rm -f /etc/swanctl/conf.d/muros.conf /etc/swanctl/conf.d/muros.secrets

# WireGuard : confs ecrites par MurOS
rm -f /etc/wireguard/wg*.conf

# HA / SNMP : MurOS ecrase /etc/keepalived/keepalived.conf,
# /etc/conntrackd/conntrackd.conf et /etc/snmp/snmpd.conf avec sa
# propre config. On les supprime ici. Si l'admin veut reutiliser
# keepalived/snmpd standalone apres uninstall, il faut reinstaller
# le paquet (apt-get install --reinstall keepalived) pour retrouver
# la conf d'origine.
rm -f /etc/keepalived/keepalived.conf 2>/dev/null || true
rm -f /etc/conntrackd/conntrackd.conf 2>/dev/null || true

# SNMP : drop-in MurOS uniquement (laisse /etc/snmp/snmpd.conf du paquet)
rm -f /etc/snmp/snmpd.conf.d/muros.conf

# DNS : MurOS ecrit /etc/resolv.conf directement et garde un backup
# .muros-bak. Si l'admin avait une conf DNS pre-MurOS on la restaure.
if [ -f /etc/resolv.conf.muros-bak ]; then
  mv /etc/resolv.conf.muros-bak /etc/resolv.conf
fi
# Drop-in resolved si on l'a pose (cas avance ou l'admin avait
# installe systemd-resolved a la main)
rm -f /etc/systemd/resolved.conf.d/muros.conf 2>/dev/null || true

# Reseau : drop-in iface + sauvegarde de l'interfaces original
rm -f /etc/network/interfaces.d/muros
# /etc/network/interfaces.muros-bak est restaure par le postrm purge,
# mais si ce script tourne sans qu'il y ait eu apt purge avant (cas
# rare), on le restaure ici en dernier recours.
if [ -f /etc/network/interfaces.muros-bak ] && \
   [ ! -f /etc/network/interfaces.restored.muros ]; then
  mv /etc/network/interfaces.muros-bak /etc/network/interfaces
  touch /etc/network/interfaces.restored.muros
  rm -f /etc/network/interfaces.restored.muros
fi

# nginx : site + bak + symlinks SSL
rm -f /etc/nginx/sites-enabled/muros
rm -f /etc/nginx/sites-available/muros.muros-bak
rm -f /etc/nginx/ssl/muros.crt /etc/nginx/ssl/muros.key
rmdir /etc/nginx/ssl 2>/dev/null || true

# fail2ban / nftables
rm -f /etc/fail2ban/jail.d/muros.conf
rm -f /etc/nftables.conf.muros-bak 2>/dev/null || true

# SSH drop-in : livre par le .deb donc supprime par apt purge muros.
# Au cas ou MurOS l'aurait re-ecrit apres purge (race avec l'apply UI) :
rm -f /etc/ssh/sshd_config.d/muros.conf
# Le log /var/log/muros-install.log est conserve volontairement pour
# pouvoir relire le deroulement de l'uninstall apres coup.
# User muros (si cree)
if getent passwd muros >/dev/null 2>&1; then
  deluser --quiet --remove-home muros 2>/dev/null || true
fi

if dpkg -l nginx-common >/dev/null 2>&1; then
  if [ -f /etc/nginx/sites-available/default ] && [ ! -L /etc/nginx/sites-enabled/default ]; then
    ln -sf /etc/nginx/sites-available/default /etc/nginx/sites-enabled/default
  fi
  systemctl reload nginx 2>/dev/null || systemctl restart nginx 2>/dev/null || true
fi
systemctl daemon-reload || true

# Restore Debian-default network manager so the box still has working
# network management after MurOS is gone. ifupdown is the historical
# Debian default and works with the /etc/network/interfaces backup we
# restored above. We don't reinstall NetworkManager (desktop-only by
# default on Debian server installs).
if ! dpkg-query -W -f='${Status}' ifupdown 2>/dev/null | grep -q "install ok installed"; then
  echo "    -> reinstalling ifupdown (Debian default network manager)"
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ifupdown isc-dhcp-client 2>/dev/null || \
    echo "       (failed, install ifupdown manually if you need it)"
fi
# Unmask systemd-networkd (the only unit MurOS masked - the others
# were uninstalled outright). Stays disabled until admin enables it.
for svc in systemd-networkd.service systemd-networkd-wait-online.service; do
  systemctl unmask "$svc" 2>/dev/null || true
done

echo
echo "=============================================================="
echo "MurOS purge OK. Log : ${LOG}"
echo "=============================================================="
echo "Reinstall :"
echo "  curl -fsSL https://apt.muros.org/install.sh | sudo bash"
