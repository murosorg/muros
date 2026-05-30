#!/bin/bash
# Build an unattended MurOS installer ISO from a Debian 13 (trixie)
# netinst image.
#
# The resulting ISO boots straight into a fully automated install: it
# partitions the first disk (guided LVM), installs a minimal Debian
# system, then registers apt.muros.org and installs the muros package.
# No installer question is asked. Burn it to a USB key (dd / Rufus /
# balenaEtcher) or attach it to a VM and boot.
#
# The preseed answers live in preseed.cfg (same directory). This script
# injects them into the installer initrd so they are read before any
# prompt, then repacks a BIOS+UEFI bootable ISO.
#
# Usage:
#   sudo ./build-iso.sh
#
# Environment variables (all optional):
#   MUROS_ROOT_PASSWORD   root password for the installed system
#                         (default: muros). Change it after first login.
#   DEBIAN_VERSION        netinst point release to fetch (default: the
#                         latest published under debian-cd/current)
#   DEBIAN_ARCH           amd64 (default) or arm64
#   NETINST_ISO           path to an already-downloaded netinst ISO
#                         (skips the download)
#   OUTPUT                output ISO path (default: ./muros-installer-<arch>.iso)
#
# Dependencies: xorriso, wget, gzip, cpio, openssl, isolinux
# (apt-get install xorriso isolinux wget gzip cpio openssl).

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT_PASSWORD="${MUROS_ROOT_PASSWORD:-muros}"
DEBIAN_VERSION="${DEBIAN_VERSION:-}"
ARCH="${DEBIAN_ARCH:-amd64}"
OUTPUT="${OUTPUT:-${HERE}/muros-installer-${ARCH}.iso}"
WORK="$(mktemp -d /tmp/muros-iso.XXXXXX)"
EXTRACT="${WORK}/iso"

cleanup() { rm -rf "${WORK}"; }
trap cleanup EXIT

need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing dependency: $1" >&2; exit 1; }; }
for bin in xorriso wget gzip cpio openssl; do need "$bin"; done

# ---------------------------------------------------------------------
# 1. Obtain the Debian netinst ISO.
# ---------------------------------------------------------------------
if [ -n "${NETINST_ISO:-}" ]; then
  SRC_ISO="${NETINST_ISO}"
  echo "[1/5] Using provided netinst ISO: ${SRC_ISO}"
else
  SRC_ISO="${WORK}/netinst.iso"
  BASE="https://cdimage.debian.org/debian-cd/current/${ARCH}/iso-cd"
  # The point release in current/ moves over time (13.0.0, 13.5.0, ...).
  # Discover the published netinst filename instead of hardcoding it,
  # unless the caller pinned DEBIAN_VERSION explicitly.
  if [ -n "${DEBIAN_VERSION}" ]; then
    ISO_NAME="debian-${DEBIAN_VERSION}-${ARCH}-netinst.iso"
  else
    echo "[1/5] Resolving latest netinst under ${BASE}"
    ISO_NAME="$(wget -qO- "${BASE}/" \
      | grep -oE "debian-[0-9.]+-${ARCH}-netinst\.iso" \
      | sort -V | tail -1)"
    if [ -z "${ISO_NAME}" ]; then
      echo "Could not determine the current Debian netinst filename from ${BASE}/." >&2
      echo "Pin one with DEBIAN_VERSION=X.Y.Z or pass NETINST_ISO=/path/to.iso." >&2
      exit 1
    fi
  fi
  URL="${BASE}/${ISO_NAME}"
  echo "[1/5] Downloading ${URL}"
  if ! wget -q --show-progress -O "${SRC_ISO}" "${URL}"; then
    echo "Download failed: ${URL}" >&2
    echo "Check connectivity, or pin DEBIAN_VERSION / pass NETINST_ISO." >&2
    exit 1
  fi
fi

# ---------------------------------------------------------------------
# 2. Extract the ISO contents (read-write copy).
# ---------------------------------------------------------------------
echo "[2/5] Extracting ISO"
mkdir -p "${EXTRACT}"
xorriso -osirrox on -indev "${SRC_ISO}" -extract / "${EXTRACT}" 2>/dev/null
chmod -R u+w "${EXTRACT}"

# ---------------------------------------------------------------------
# 3. Render the preseed (substitute the crypted root password) and
#    inject it into the installer initrd so it is read very early.
# ---------------------------------------------------------------------
echo "[3/5] Injecting preseed into the installer initrd"
PW_HASH="$(openssl passwd -6 "${ROOT_PASSWORD}")"
PRESEED_RENDERED="${WORK}/preseed.cfg"
sed "s|@ROOT_PW_HASH@|${PW_HASH}|g" "${HERE}/preseed.cfg" > "${PRESEED_RENDERED}"

# The text-mode installer initrd. (install.amd for amd64, install.a64 for arm64.)
case "${ARCH}" in
  amd64) INSTALL_DIR="install.amd" ;;
  arm64) INSTALL_DIR="install.a64" ;;
  *) echo "Unsupported arch: ${ARCH}" >&2; exit 1 ;;
esac

for IRD in "${EXTRACT}/${INSTALL_DIR}/initrd.gz" "${EXTRACT}/${INSTALL_DIR}/gtk/initrd.gz"; do
  [ -f "${IRD}" ] || continue
  TMP="${WORK}/ird"; rm -rf "${TMP}"; mkdir -p "${TMP}"
  cp "${PRESEED_RENDERED}" "${TMP}/preseed.cfg"
  ( cd "${TMP}" && echo preseed.cfg | cpio -H newc -o --quiet ) | gzip -9 >> "${IRD}"
  echo "      + ${IRD#${EXTRACT}/}"
done

# ---------------------------------------------------------------------
# 4. Make the install fully automatic: default to the auto-install entry
#    with a short timeout and append the auto cmdline. We patch both the
#    BIOS (isolinux) and UEFI (grub) boot configs.
# ---------------------------------------------------------------------
echo "[4/5] Patching boot menus for unattended boot"
KARGS="auto=true priority=critical"

# BIOS / isolinux
if [ -d "${EXTRACT}/isolinux" ]; then
  for cfg in "${EXTRACT}"/isolinux/*.cfg; do
    [ -f "${cfg}" ] || continue
    sed -i "s|timeout .*|timeout 30|I" "${cfg}" 2>/dev/null || true
  done
  # Append our kernel args to every 'append' line that loads the installer.
  for cfg in "${EXTRACT}"/isolinux/*.cfg; do
    [ -f "${cfg}" ] || continue
    sed -i "/append/ s|\(vga=[^ ]*\)|\1 ${KARGS}|I" "${cfg}" 2>/dev/null || true
  done
fi

# UEFI / grub
if [ -f "${EXTRACT}/boot/grub/grub.cfg" ]; then
  sed -i "s|set timeout=.*|set timeout=3|" "${EXTRACT}/boot/grub/grub.cfg" || true
  sed -i "/vmlinuz/ s|\(vga=[^ ]*\)|\1 ${KARGS}|" "${EXTRACT}/boot/grub/grub.cfg" || true
fi

# Refresh the md5 manifest so the installer integrity check passes.
if [ -f "${EXTRACT}/md5sum.txt" ]; then
  ( cd "${EXTRACT}" && find . -type f ! -name md5sum.txt -print0 \
    | xargs -0 md5sum > md5sum.txt ) 2>/dev/null || true
fi

# ---------------------------------------------------------------------
# 5. Repack a hybrid (BIOS + UEFI) bootable ISO, reusing the original
#    El Torito boot layout captured from the source image.
# ---------------------------------------------------------------------
echo "[5/5] Repacking ISO -> ${OUTPUT}"
# Reuse the El Torito / isohybrid boot layout captured from the source
# image. The reported arguments are single-quoted (paths, modification
# date); parse them through 'eval set --' so the quotes are honoured
# instead of reaching xorriso as literal characters.
MKISOFS_ARGS="$(xorriso -indev "${SRC_ISO}" -report_el_torito as_mkisofs 2>/dev/null \
  | grep -v '^-V' | tr '\n' ' ')"
eval "set -- ${MKISOFS_ARGS}"
xorriso -as mkisofs \
  -V 'MUROS_INSTALL' \
  "$@" \
  -o "${OUTPUT}" \
  "${EXTRACT}"

echo
echo "Done. Unattended MurOS installer: ${OUTPUT}"
echo "Root password for the installed system: ${ROOT_PASSWORD}"
echo "Write it to a USB key:  sudo dd if=${OUTPUT} of=/dev/sdX bs=4M status=progress oflag=sync"
