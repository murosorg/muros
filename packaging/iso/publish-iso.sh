#!/bin/bash
# Publish a built MurOS installer ISO to the download.muros.org host.
#
# The website (install.html) links to three files served from the apt
# host under /iso/:
#   muros-installer-<arch>.iso
#   muros-installer-<arch>.iso.sha256
#   muros-installer-<arch>.iso.asc   (detached GPG signature)
#
# build-iso.sh produces only the .iso. This script generates the
# checksum, uploads the ISO and checksum, then signs the ISO ON THE
# SERVER with the MurOS repository key (the private key lives there, the
# same one that signs the apt Release), and verifies the result.
#
# Why sign on the server: the release key is not on developer
# workstations, and reusing the same key as the apt repo gives users a
# single chain of trust (the keyring they already imported for apt also
# verifies the ISO).
#
# A plain replace, never an append: rsync runs without --append/
# --append-verify so an existing remote ISO of a different release is
# fully overwritten instead of producing a hybrid file with a broken
# checksum.
#
# Usage:
#   ./publish-iso.sh
#   MUROS_ISO=/path/muros-installer-amd64.iso ./publish-iso.sh
#
# Environment variables (all optional):
#   MUROS_ISO          path to the ISO to publish
#                      (default: ./muros-installer-<arch>.iso)
#   DEBIAN_ARCH        amd64 (default) or arm64, used for the default name
#   MUROS_APT_HOST     ssh target of the apt host
#                      (default: root@download.muros.org)
#   MUROS_APT_ISO_DIR  remote directory served at /iso/
#                      (default: /opt/muros/download/iso)
#   MUROS_GPG_KEY      signing key id/fingerprint on the server
#                      (default: 17DE4892C0D370BB4FC4AA9A8699CDDBEA8D4B22)
#   MUROS_APT_URL      public base url for the post-publish check
#                      (default: https://download.muros.org)

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ARCH="${DEBIAN_ARCH:-amd64}"
ISO="${MUROS_ISO:-${HERE}/muros-installer-${ARCH}.iso}"
APT_HOST="${MUROS_APT_HOST:-debian@10.10.10.10}"
SSH_OPTS="${MUROS_SSH_OPTS:--o StrictHostKeyChecking=no}"
APT_ISO_DIR="${MUROS_APT_ISO_DIR:-/opt/muros/download/iso}"
GPG_KEY="${MUROS_GPG_KEY:-17DE4892C0D370BB4FC4AA9A8699CDDBEA8D4B22}"
APT_URL="${MUROS_APT_URL:-https://download.muros.org}"

need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing dependency: $1" >&2; exit 1; }; }
for bin in sha256sum rsync ssh; do need "$bin"; done

[ -f "${ISO}" ] || { echo "ISO not found: ${ISO}" >&2; exit 1; }

ISO_NAME="$(basename "${ISO}")"
ISO_DIR="$(cd "$(dirname "${ISO}")" && pwd)"
SHA_FILE="${ISO_DIR}/${ISO_NAME}.sha256"

# The .sha256 must reference the ISO by base name only (no path), because
# it is verified with `sha256sum -c` from inside the remote directory.
echo "[1/5] Computing local checksum (${ISO_NAME})"
( cd "${ISO_DIR}" && sha256sum "${ISO_NAME}" > "${ISO_NAME}.sha256" )

echo "[2/5] Verifying local checksum"
( cd "${ISO_DIR}" && sha256sum -c "${ISO_NAME}.sha256" )

# Remove any previous ISO of a different release first, then a clean
# transfer (no append). --partial keeps a resumable temp on interruption
# while still reconstructing the full, verified file.
echo "[3/5] Removing any stale remote ISO"
ssh ${SSH_OPTS} "${APT_HOST}" "sudo mkdir -p ${APT_ISO_DIR}; sudo rm -f ${APT_ISO_DIR}/${ISO_NAME}"

echo "[4/5] Uploading ISO and checksum to ${APT_HOST}:${APT_ISO_DIR}"
rsync --partial --info=progress2 -e "ssh ${SSH_OPTS}" --rsync-path="sudo rsync" \
  "${ISO}" "${SHA_FILE}" \
  "${APT_HOST}:${APT_ISO_DIR}/"

# Verify on the server, then sign with the repository key. --yes lets the
# signature overwrite a previous .asc for the same name.
echo "[5/5] Verifying and signing on ${APT_HOST}"
ssh ${SSH_OPTS} "${APT_HOST}" "
  set -e
  cd '${APT_ISO_DIR}'
  sudo sha256sum -c '${ISO_NAME}.sha256'
  sudo gpg --batch --yes --local-user '${GPG_KEY}' --armor --detach-sign '${ISO_NAME}'
  sudo ls -la '${ISO_NAME}'*
"

echo
echo "Published. Public URLs:"
echo "  ${APT_URL}/iso/${ISO_NAME}"
echo "  ${APT_URL}/iso/${ISO_NAME}.sha256"
echo "  ${APT_URL}/iso/${ISO_NAME}.asc"
