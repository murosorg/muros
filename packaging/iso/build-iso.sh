#!/bin/bash
# Build a MurOS installer ISO from a Debian 13 (trixie) LIVE standard
# image (the text-only, no-desktop variant).
#
# Why the live image and not netinst: the live standard image ships a
# complete Debian system inside its squashfs, so the installer copies
# that filesystem to disk (live-installer) instead of debootstrapping
# from a network mirror. The base OS install therefore needs no network.
# Combined with an offline bundle (pool + wheelhouse, see
# prepare-offline.sh) the whole install runs fully offline.
#
# The boot menu offers:
#   - "Install MurOS" (default, 30s timeout): partitions the first disk
#     (guided LVM), copies the base system, installs muros. Fully
#     unattended; networking is not configured by the installer. On first
#     boot MurOS assigns the interfaces (WAN on DHCP, LAN static) and loads
#     the firewall, exactly like an OPNsense appliance.
#   - the stock live "Install" / "Graphical install" entries: a plain
#     interactive Debian install.
#
# Single, self-contained, always-offline build. One command does
# everything: on the first run it prepares the offline bundle (pool/ +
# wheelhouse/), the only step that needs network, then it builds an ISO
# that installs with no network at all. Burn the result to a USB key
# (dd / Rufus / balenaEtcher) or attach it to a VM.
#
# Usage:
#   sudo ./build-iso.sh                          # downloads the live image
#   sudo LIVE_ISO=/path/debian-live-...-standard.iso ./build-iso.sh
#
# You can provide the Debian live ISO yourself with LIVE_ISO to avoid the
# download and build on top of it. Run on any Linux host with network
# access: the target Debian 13 packages and Python wheels are collected
# from the live image's own filesystem (its squashfs), so the host
# distribution does not matter. Dependencies:
#   apt-get install xorriso isolinux wget gzip cpio openssl squashfs-tools dpkg-dev
# (debootstrap is only needed as a fallback when the squashfs cannot be
# read.) The privileged steps run via sudo, which is why the script is
# invoked with sudo.
#
# Environment variables (all optional):
#   MUROS_ROOT_PASSWORD   root password for the installed system
#                         (default: root). Kept deliberately simple and
#                         AZERTY/QWERTY-safe so first console login is not
#                         blocked by a keymap mismatch. Change it after
#                         first login.
#   MUROS_OFFLINE_DIR     where the offline bundle lives / is built
#                         (default: ./offline next to this script).
#   MUROS_REUSE_BUNDLE    set to 1 to reuse a cached offline bundle (the
#                         default rebuilds it from scratch every run so an
#                         ISO never ships a stale muros .deb).
#   MUROS_KEEP_CACHED_DEB with MUROS_REUSE_BUNDLE=1, set to 1 to also reuse
#                         the cached muros .deb (fully offline; may be stale).
#   MUROS_APT_URL         MurOS apt repo used to fetch the package
#                         (default: https://download.muros.org).
#   DEBIAN_VERSION        live point release to fetch (default: the
#                         latest published under debian-cd/current-live)
#   DEBIAN_ARCH           amd64 (default) or arm64
#   LIVE_ISO              path to an already-downloaded live standard ISO
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
# Offline bundle directory (optional). When set, it must contain a
# prepared local package pool (pool/) and, optionally, a Python wheelhouse
# (wheelhouse/) produced by prepare-offline.sh. The resulting ISO then
# installs MurOS and all its dependencies with no network access at all.
# Defaults to ./offline next to this script. Populate it once with
# prepare-offline.sh (needs network and a Debian 13 host); the build
# itself is always offline, there is no online variant.
OFFLINE_DIR="${MUROS_OFFLINE_DIR:-${HERE}/offline}"
# Scratch work tree. A full build extracts the ~1.9 GB live ISO, builds an
# offline package pool and a Python wheelhouse, then masters the output
# ISO, so it needs several GB of free space. The default /tmp is often a
# RAM-backed tmpfs sized to a fraction of RAM (a few hundred MB), which
# fills up while extracting the 800+ MB squashfs and aborts the build with
# a misleading "No such file or directory" write error. Default the work
# tree to a directory on real disk next to this script instead, and let
# MUROS_WORK_DIR override it.
WORK_BASE="${MUROS_WORK_DIR:-${HERE}/.work}"
mkdir -p "${WORK_BASE}"
WORK="$(mktemp -d "${WORK_BASE}/muros-iso.XXXXXX")"
EXTRACT="${WORK}/iso"
CHROOT=""   # set by prepare_offline_bundle; unmounted on exit
# Run privileged steps via sudo only when we are not already root.
SUDO=""; [ "$(id -u)" -eq 0 ] || SUDO="sudo"

cleanup() {
  # Unmount any kernel filesystems bound into the bundle chroot before
  # removing the work tree, so rm never descends into a live mount.
  if [ -n "${CHROOT}" ]; then
    for m in dev/pts dev sys proc; do
      mountpoint -q "${CHROOT}/${m}" 2>/dev/null && ${SUDO} umount -lf "${CHROOT}/${m}" 2>/dev/null || true
    done
  fi
  ${SUDO} rm -rf "${WORK}"
}
trap cleanup EXIT

need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing dependency: $1" >&2; exit 1; }; }
for bin in xorriso wget gzip cpio openssl; do need "$bin"; done

REPO="$(cd "${HERE}/../.." && pwd)"
MUROS_APT_URL="${MUROS_APT_URL:-https://download.muros.org}"

# Report the muros .deb version present in the offline pool. With
# MUROS_REUSE_BUNDLE=1 the pool is cached between builds, so the muros
# package, which changes every release, can silently go stale and ship
# old code on the ISO. Surfacing the version here makes that obvious.
bundled_muros_version() {
  ls "${OFFLINE_DIR}"/pool/muros_*_all.deb 2>/dev/null \
    | sed -n 's#.*/muros_\(.*\)_all\.deb#\1#p' | sort -V | tail -1
}

# Refresh ONLY the muros .deb inside an existing cached pool. This runs on
# the MUROS_REUSE_BUNDLE=1 path: the heavy parts of the bundle (base
# packages + Python wheelhouse) are reused for speed, but the muros
# package changes every release, so reusing a cached one ships old code on
# the ISO (e.g. the first-boot static LAN import is missing, so the box
# comes up on the 192.168.1.1 fallback whatever address was typed at
# install). So even when reusing the cache we pull the current muros .deb
# from ${MUROS_APT_URL}. Set MUROS_KEEP_CACHED_DEB=1 to skip this and reuse
# the cached .deb too. On any failure we fall back to a full bundle rebuild
# rather than proceed with a stale .deb, so a build never silently ships
# old code. Note: if a new release adds a dependency absent from the cached
# pool, the offline install can fail on that dep; drop MUROS_REUSE_BUNDLE
# for a full rebuild that recollects the whole dependency set.
refresh_muros_deb() {
  need wget
  need dpkg-scanpackages
  local tmp idx pair version filename cached
  tmp="${WORK}/muros-refresh"
  mkdir -p "${tmp}"
  # Flat repo published as "deb ${MUROS_APT_URL} stable main"; arch:all
  # packages are listed in the amd64 binary index.
  idx="${MUROS_APT_URL}/dists/stable/main/binary-amd64/Packages.gz"
  if ! wget -q -O "${tmp}/Packages.gz" "${idx}"; then
    echo "      could not fetch ${idx}; falling back to a full bundle rebuild" >&2
    prepare_offline_bundle
    return
  fi
  gzip -dc "${tmp}/Packages.gz" > "${tmp}/Packages" 2>/dev/null || true
  # Pick the highest muros version published, and its Filename.
  pair="$(awk 'BEGIN{RS="";FS="\n"} {p="";v="";f="";
      for(i=1;i<=NF;i++){
        if($i ~ /^Package: /){p=$i};
        if($i ~ /^Version: /){v=$i};
        if($i ~ /^Filename: /){f=$i}}
      if(p=="Package: muros"){sub(/^Version: /,"",v); sub(/^Filename: /,"",f); print v" "f}}' \
      "${tmp}/Packages" | sort -V | tail -1)"
  version="${pair%% *}"
  filename="${pair#* }"
  if [ -z "${version}" ] || [ -z "${filename}" ] || [ "${version}" = "${filename}" ]; then
    echo "      muros not found in ${idx}; falling back to a full bundle rebuild" >&2
    prepare_offline_bundle
    return
  fi
  cached="$(bundled_muros_version)"
  if [ -n "${cached}" ] && [ "${cached}" = "${version}" ]; then
    echo "[0b/5] Cached offline bundle is current (muros ${version}); reusing it"
    return
  fi
  echo "[0b/5] Refreshing muros .deb in cached pool: ${cached:-none} -> ${version}"
  if ! wget -q -O "${tmp}/muros.deb" "${MUROS_APT_URL}/${filename}"; then
    echo "      could not download ${MUROS_APT_URL}/${filename}; falling back to a full rebuild" >&2
    prepare_offline_bundle
    return
  fi
  rm -f "${OFFLINE_DIR}"/pool/muros_*_all.deb
  cp "${tmp}/muros.deb" "${OFFLINE_DIR}/pool/$(basename "${filename}")"
  ( cd "${OFFLINE_DIR}/pool" && dpkg-scanpackages -m . /dev/null > Packages )
  gzip -9c "${OFFLINE_DIR}/pool/Packages" > "${OFFLINE_DIR}/pool/Packages.gz"
}

# Prepare the offline bundle (pool/ + wheelhouse/). This is the only step
# that needs network access; the resulting ISO installs with no network.
# The target is Debian 13 (trixie) and the build host may be anything
# (Ubuntu, an older Debian, ...), so the packages and Python wheels are
# collected inside a trixie environment. By preference we reuse the live
# image's OWN filesystem (its squashfs), which is exactly the target base,
# so we do not download a second base system and the dependency set is the
# precise delta the target needs. If the squashfs is unavailable we fall
# back to a minimal trixie chroot built with debootstrap. Requires the ISO
# to be extracted first (so it runs after step 2).
prepare_offline_bundle() {
  echo "[0b/5] Preparing offline bundle at ${OFFLINE_DIR} (needs network)"
  need dpkg-scanpackages
  mkdir -p "${OFFLINE_DIR}/pool" "${OFFLINE_DIR}/wheelhouse"
  CHROOT="${WORK}/trixie"

  local squashfs
  squashfs="$(ls "${EXTRACT}"/live/*.squashfs 2>/dev/null | head -1)"
  if [ -n "${squashfs}" ] && command -v unsquashfs >/dev/null 2>&1; then
    echo "      reusing the live image's own trixie filesystem (${squashfs#${EXTRACT}/})"
    ${SUDO} unsquashfs -f -d "${CHROOT}" "${squashfs}" >/dev/null
  else
    echo "      live squashfs not usable here, bootstrapping a minimal trixie chroot"
    need debootstrap
    ${SUDO} debootstrap --arch="${ARCH}" --variant=minbase \
      --include=ca-certificates,apt-utils,python3,python3-pip,python3-venv \
      trixie "${CHROOT}" "${DEBIAN_MIRROR:-http://deb.debian.org/debian}"
  fi

  # Bind the kernel filesystems so apt/pip behave, register the signed
  # MurOS repo (same suite as install.sh: stable main; trusted=yes avoids
  # importing the key in this throwaway environment) and a resolver.
  ${SUDO} mount -t proc proc "${CHROOT}/proc" 2>/dev/null || true
  ${SUDO} mount --bind /sys "${CHROOT}/sys" 2>/dev/null || true
  ${SUDO} mount --bind /dev "${CHROOT}/dev" 2>/dev/null || true
  # The live filesystem points apt at its own install medium
  # (file:/run/live/medium), which is not present here and makes
  # apt-get update fail. Replace the chroot's apt sources with a clean
  # trixie mirror, drop any leftover source files, then add the MurOS
  # repo (trusted=yes: the collected .debs are what matter, and the
  # on-target install uses a trusted file: repo anyway).
  ${SUDO} sh -c "printf 'deb http://deb.debian.org/debian trixie main non-free-firmware\n' > '${CHROOT}/etc/apt/sources.list'"
  ${SUDO} sh -c "rm -f '${CHROOT}'/etc/apt/sources.list.d/* 2>/dev/null" || true
  echo "deb [trusted=yes] ${MUROS_APT_URL} stable main" \
    | ${SUDO} tee "${CHROOT}/etc/apt/sources.list.d/muros.list" >/dev/null
  ${SUDO} cp /etc/resolv.conf "${CHROOT}/etc/resolv.conf" 2>/dev/null || true
  ${SUDO} chroot "${CHROOT}" apt-get update
  # The standard squashfs may not ship pip; make sure it is there.
  ${SUDO} chroot "${CHROOT}" sh -c 'command -v pip3 >/dev/null 2>&1 || DEBIAN_FRONTEND=noninteractive apt-get install -y python3-pip python3-venv' || true

  # Download muros and the dependencies missing from the base system, then
  # copy them into the pool. Packages already present on the target are not
  # reinstalled at install time, so a superset in the pool is harmless.
  echo "      downloading muros + dependencies into pool/"
  ${SUDO} chroot "${CHROOT}" apt-get install -y --download-only muros
  ${SUDO} sh -c "cp -a '${CHROOT}'/var/cache/apt/archives/*.deb '${OFFLINE_DIR}/pool/'"

  # Build the Python wheelhouse with the chroot's Python (trixie = 3.13)
  # so the wheel ABI matches the target.
  echo "      downloading Python wheels into wheelhouse/"
  if [ -f "${REPO}/backend/requirements.txt" ]; then
    ${SUDO} cp "${REPO}/backend/requirements.txt" "${CHROOT}/root/requirements.txt"
    ${SUDO} chroot "${CHROOT}" python3 -m pip download -r /root/requirements.txt -d /root/wheelhouse
    ${SUDO} chroot "${CHROOT}" python3 -m pip download pip wheel -d /root/wheelhouse || true
    ${SUDO} sh -c "cp -a '${CHROOT}'/root/wheelhouse/. '${OFFLINE_DIR}/wheelhouse/'"
  else
    echo "      warning: ${REPO}/backend/requirements.txt not found, skipping wheelhouse" >&2
  fi

  # Hand the collected files back to the invoking user, then build the
  # flat apt index for the pool.
  ${SUDO} chown -R "$(id -u):$(id -g)" "${OFFLINE_DIR}/pool" "${OFFLINE_DIR}/wheelhouse"
  ( cd "${OFFLINE_DIR}/pool" && dpkg-scanpackages -m . /dev/null > Packages )
  gzip -9c "${OFFLINE_DIR}/pool/Packages" > "${OFFLINE_DIR}/pool/Packages.gz"
  echo "      bundle ready: $(ls -1 "${OFFLINE_DIR}"/pool/*.deb 2>/dev/null | wc -l) packages, $(ls -1 "${OFFLINE_DIR}"/wheelhouse/*.whl 2>/dev/null | wc -l) wheels"
}

# ---------------------------------------------------------------------
# 1. Obtain the Debian LIVE standard ISO (self-contained base system).
# ---------------------------------------------------------------------
# LIVE_ISO is the supported override; accept the legacy NETINST_ISO name
# too so existing callers keep working.
PROVIDED_ISO="${LIVE_ISO:-${NETINST_ISO:-}}"
if [ -n "${PROVIDED_ISO}" ]; then
  SRC_ISO="${PROVIDED_ISO}"
  echo "[1/5] Using provided live ISO: ${SRC_ISO}"
else
  SRC_ISO="${WORK}/live.iso"
  BASE="https://cdimage.debian.org/debian-cd/current-live/${ARCH}/iso-hybrid"
  # The point release in current-live/ moves over time (13.0.0, 13.5.0,
  # ...). Discover the published standard live filename instead of
  # hardcoding it, unless the caller pinned DEBIAN_VERSION explicitly.
  if [ -n "${DEBIAN_VERSION}" ]; then
    ISO_NAME="debian-live-${DEBIAN_VERSION}-${ARCH}-standard.iso"
  else
    echo "[1/5] Resolving latest live standard image under ${BASE}"
    ISO_NAME="$(wget -qO- "${BASE}/" \
      | grep -oE "debian-live-[0-9.]+-${ARCH}-standard\.iso" \
      | sort -V | tail -1)"
    if [ -z "${ISO_NAME}" ]; then
      echo "Could not determine the current Debian live standard filename from ${BASE}/." >&2
      echo "Pin one with DEBIAN_VERSION=X.Y.Z or pass LIVE_ISO=/path/to.iso." >&2
      exit 1
    fi
  fi
  URL="${BASE}/${ISO_NAME}"
  echo "[1/5] Downloading ${URL}"
  if ! wget -q --show-progress -O "${SRC_ISO}" "${URL}"; then
    echo "Download failed: ${URL}" >&2
    echo "Check connectivity, or pin DEBIAN_VERSION / pass LIVE_ISO." >&2
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
# 2b. Prepare the offline bundle from the extracted live filesystem
#     By default the bundle is rebuilt from scratch on every run, so an
#     ISO never ships a stale cached muros .deb. MUROS_REUSE_BUNDLE=1 opts
#     into the cache (and still refreshes the muros .deb unless
#     MUROS_KEEP_CACHED_DEB=1 is also set).
# ---------------------------------------------------------------------
if [ "${MUROS_REUSE_BUNDLE:-0}" != "1" ] || [ ! -d "${OFFLINE_DIR}/pool" ] \
   || [ -z "$(ls -A "${OFFLINE_DIR}/pool" 2>/dev/null)" ]; then
  # Default: full rebuild, no cache reuse.
  prepare_offline_bundle
elif [ "${MUROS_KEEP_CACHED_DEB:-0}" = "1" ]; then
  echo "[0b/5] Reusing existing offline bundle at ${OFFLINE_DIR}"
  echo "      MUROS_KEEP_CACHED_DEB=1: NOT refreshing the muros .deb (fully"
  echo "      offline build). The cached muros package may be stale and ship"
  echo "      old code on the ISO. Drop this flag to pull the current"
  echo "      release, or drop MUROS_REUSE_BUNDLE for a full rebuild." >&2
else
  # Explicit cache reuse: keep the heavy bundle but always pull the
  # current muros .deb so the ISO never ships an old muros package.
  echo "[0b/5] MUROS_REUSE_BUNDLE=1: reusing cached bundle at ${OFFLINE_DIR}"
  refresh_muros_deb
fi
echo "      bundled muros version: $(bundled_muros_version | sed 's/^$/unknown/')"

# ---------------------------------------------------------------------
# 3. Render the preseed and bake it into a DEDICATED copy of the
#    installer initrd (initrd.muros.gz). The stock initrd.gz is left
#    pristine on purpose: that way the standard Debian "Install" and
#    "Graphical install" menu entries stay fully interactive (a plain
#    Debian install). Only the dedicated "Install MurOS" entry, which
#    boots initrd.muros.gz, runs the MurOS preseed (everything automated
#    except the LAN addressing, which the operator enters by hand).
# ---------------------------------------------------------------------
echo "[3/5] Baking preseed into a dedicated installer initrd (initrd.muros.gz)"
PW_HASH="$(openssl passwd -6 "${ROOT_PASSWORD}")"
PRESEED_RENDERED="${WORK}/preseed.cfg"
PRESEED_SRC="${HERE}/preseed.cfg"
[ -f "${PRESEED_SRC}" ] || { echo "Missing ${PRESEED_SRC}" >&2; exit 1; }
sed "s|@ROOT_PW_HASH@|${PW_HASH}|g" "${PRESEED_SRC}" > "${PRESEED_RENDERED}"

# Locate the Debian installer directory. Live images put it under
# /install, while netinst/DVD images use /install.amd (amd64) or
# /install.a64 (arm64). Detect whichever is actually present.
INSTALL_DIR=""
for cand in install install.amd install.a64; do
  if [ -f "${EXTRACT}/${cand}/vmlinuz" ] || [ -f "${EXTRACT}/${cand}/initrd.gz" ]; then
    INSTALL_DIR="${cand}"; break
  fi
done
if [ -z "${INSTALL_DIR}" ]; then
  echo "Could not locate the Debian installer directory (install/ or install.amd/) in the image." >&2
  exit 1
fi
echo "      installer directory: /${INSTALL_DIR}"

for SRC in "${EXTRACT}/${INSTALL_DIR}/initrd.gz" "${EXTRACT}/${INSTALL_DIR}/gtk/initrd.gz"; do
  [ -f "${SRC}" ] || continue
  DST="${SRC%/initrd.gz}/initrd.muros.gz"
  cp "${SRC}" "${DST}"
  TMP="${WORK}/ird"; rm -rf "${TMP}"; mkdir -p "${TMP}"
  cp "${PRESEED_RENDERED}" "${TMP}/preseed.cfg"
  ( cd "${TMP}" && echo preseed.cfg | cpio -H newc -o --quiet ) | gzip -9 >> "${DST}"
  echo "      + ${DST#${EXTRACT}/} (stock ${SRC#${EXTRACT}/} kept interactive)"
done

# ---------------------------------------------------------------------
# 4. Make "Install MurOS" the ONLY boot entry. A MurOS installer has no
#    use for the stock Debian live entries (Live system, Start installer,
#    speech, Advanced, Utilities), which only confuse operators, so we
#    them. priority=high surfaces any question the preseed does not answer
#    (a safety net rather than failing on a bad default); a fully preseeded
#    run is unattended. We rewrite both the BIOS (isolinux) and UEFI (grub)
#    menus.
# ---------------------------------------------------------------------
echo "[4/5] Building a MurOS-only boot menu (single 'Install MurOS' entry)"
KARGS="priority=high"

# BIOS / isolinux: our single entry, then a menu.cfg that pulls only the
# shared layout (stdmenu.cfg, which carries the splash and colors) plus
# our entry. Dropping the stock includes (live.cfg, install.cfg, the
# utilities submenu) and the "Boot menu" title (which overlapped the
# splash) leaves a clean one-line menu.
if [ -d "${EXTRACT}/isolinux" ]; then
  cat > "${EXTRACT}/isolinux/muros.cfg" <<EOF
label murosauto
	menu label ^Install MurOS
	menu default
	kernel /${INSTALL_DIR}/vmlinuz
	append vga=788 ${KARGS} initrd=/${INSTALL_DIR}/initrd.muros.gz --- quiet
EOF
  cat > "${EXTRACT}/isolinux/menu.cfg" <<'EOF'
menu hshift 0
menu width 82
include stdmenu.cfg
include muros.cfg
EOF
  for cfg in "${EXTRACT}"/isolinux/*.cfg; do
    [ -f "${cfg}" ] || continue
    sed -i "s|^timeout .*|timeout 300|I" "${cfg}" 2>/dev/null || true
  done
fi

# UEFI / grub: keep the shared config (config.cfg sets the gfx mode,
# theme and splash), then expose only our entry.
if [ -f "${EXTRACT}/boot/grub/grub.cfg" ]; then
  GRUB="${EXTRACT}/boot/grub/grub.cfg"
  {
    echo "source /boot/grub/config.cfg"
    echo "set default=0"
    echo "set timeout=30"
    echo "menuentry 'Install MurOS' {"
    echo "    linux    /${INSTALL_DIR}/vmlinuz vga=788 ${KARGS} --- quiet"
    echo "    initrd   /${INSTALL_DIR}/initrd.muros.gz"
    echo "}"
  } > "${GRUB}.new" && mv "${GRUB}.new" "${GRUB}"
fi

# ---------------------------------------------------------------------
# 4c. Replace the Debian boot splash with the MurOS-branded one, so the
#     installer menu shows MurOS instead of Debian. isolinux uses a
#     640x480 image, grub an 800x600 one (regenerate them with
#     branding/make-splash.py). Both menus reference splash.png.
# ---------------------------------------------------------------------
BRAND="${HERE}/branding"
if [ -f "${BRAND}/splash-640x480.png" ] && [ -f "${EXTRACT}/isolinux/splash.png" ]; then
  cp -f "${BRAND}/splash-640x480.png" "${EXTRACT}/isolinux/splash.png"
  echo "      branded isolinux splash"
fi
if [ -f "${BRAND}/splash-800x600.png" ] && [ -f "${EXTRACT}/boot/grub/splash.png" ]; then
  cp -f "${BRAND}/splash-800x600.png" "${EXTRACT}/boot/grub/splash.png"
  echo "      branded grub splash"
fi

# ---------------------------------------------------------------------
# 4b. Stage the local package pool and the Python wheelhouse onto the ISO
#     so the install needs no network at all. The bundle was prepared in
#     step 0 (or pre-existing).
# ---------------------------------------------------------------------
echo "[4b/5] Staging offline bundle from ${OFFLINE_DIR}"
if [ ! -d "${OFFLINE_DIR}/pool" ] || [ -z "$(ls -A "${OFFLINE_DIR}/pool" 2>/dev/null)" ]; then
  echo "Offline bundle at ${OFFLINE_DIR}/pool is missing or empty (step 0 failed?)." >&2
  exit 1
fi
echo "      staging muros version: $(bundled_muros_version | sed 's/^$/unknown/')"
mkdir -p "${EXTRACT}/muros/pool" "${EXTRACT}/muros/wheelhouse"
cp -a "${OFFLINE_DIR}/pool/." "${EXTRACT}/muros/pool/"
[ -d "${OFFLINE_DIR}/wheelhouse" ] && cp -a "${OFFLINE_DIR}/wheelhouse/." "${EXTRACT}/muros/wheelhouse/"
# The offline post-install script invoked by preseed/late_command.
cp "${HERE}/late_command.sh" "${EXTRACT}/muros/late_command.sh"
chmod +x "${EXTRACT}/muros/late_command.sh"
# Stage the signed-repo keyring so the INSTALLED system can register
# download.muros.org and receive MurOS updates online (the install itself
# stays fully offline, late_command just copies this file into the
# target). The pre-dearmored keyring is published next to the repo; it
# is fetched here at build time (the build host has network for the
# bundle step anyway). Non-fatal: if it cannot be fetched, ISO-installed
# systems simply will not auto-register the repo.
if wget -qO "${EXTRACT}/muros/muros-archive-keyring.gpg" "${MUROS_APT_URL}/muros-archive-keyring.gpg" \
     && [ -s "${EXTRACT}/muros/muros-archive-keyring.gpg" ]; then
  echo "      staged repo keyring (${MUROS_APT_URL}/muros-archive-keyring.gpg)"
else
  rm -f "${EXTRACT}/muros/muros-archive-keyring.gpg"
  echo "      warning: could not fetch the repo keyring from ${MUROS_APT_URL}; ISO-installed systems will not auto-register download.muros.org" >&2
fi
# (Re)generate flat apt metadata for the local pool.
if command -v dpkg-scanpackages >/dev/null 2>&1; then
  ( cd "${EXTRACT}/muros/pool" && dpkg-scanpackages -m . /dev/null > Packages 2>/dev/null )
  gzip -9c "${EXTRACT}/muros/pool/Packages" > "${EXTRACT}/muros/pool/Packages.gz"
elif [ ! -f "${EXTRACT}/muros/pool/Packages" ]; then
  echo "Warning: dpkg-scanpackages not found and no prepared Packages index in pool/." >&2
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
echo "Done. Offline MurOS installer (no network needed): ${OUTPUT}"
echo "Boot menu: 'Install MurOS' (default, 30s). The install is fully"
echo "unattended; networking is not configured by the installer. On first"
echo "boot MurOS assigns the interfaces (WAN on DHCP, LAN static) and loads"
echo "the firewall; reach the web UI on the LAN at https://192.168.1.1"
echo "(login root / muros). The stock Debian entries remain for a plain"
echo "manual install. Console root password for the installed system: ${ROOT_PASSWORD}"
echo "Write it to a USB key:  sudo dd if=${OUTPUT} of=/dev/sdX bs=4M status=progress oflag=sync"
