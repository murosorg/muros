#!/bin/bash

set -euo pipefail

if [ "$(id -u)" != 0 ]; then
	exec sudo -- "$0" "$@"
fi

REPO="${MUROS_REPO:-/opt/muros/download}"
DIST="${MUROS_DIST:-stable}"
ISO_DIR="${REPO}/iso"
ISO_NAME="${MUROS_ISO_NAME:-muros-installer-amd64.iso}"
LOGFILE="${MUROS_LOG:-/var/log/muros-publish.log}"
LOCKFILE="/var/lock/muros-publish.lock"
BASE_URL="${MUROS_URL:-https://download.muros.org}"

FORCE=no
DRYRUN=no
KEEP_PREVIOUS=yes

usage() {
	cat <<'USAGE'
muros-publish - publish a MurOS installer image and update the apt repository

Usage:
  muros-publish deb <file.deb|url> [more.deb ...]
  muros-publish iso <file.iso|url>
  muros-publish release <file.deb> <file.iso>
  muros-publish status
  muros-publish rollback-iso
  muros-publish verify

Options:
  --force          republish a version that is already in the repository
  --no-previous    do not keep the replaced image as a rollback copy
  --dry-run        show what would happen, change nothing
  --name <file>    target name of the image (default muros-installer-amd64.iso)
  -h, --help       this text

What it does:
  deb     reads the package name, version and architecture from the file,
          hands it to reprepro for the stable suite, which rewrites and signs
          the indices, then drops the files no longer referenced. An older
          version of the same package is replaced, apt clients see the update
          on their next refresh.
  iso     stages the image next to the published one, computes the checksum,
          signs it with the repository key, verifies both, and only then moves
          the three files into place. The replaced image is kept as a rollback
          copy unless --no-previous is given.
  release does both, the package first: the image ships that package, so a
          machine installed from it finds its own version in the repository.

The files can be given as local paths or as http(s) urls. Everything is
logged to the log file, the public urls are printed at the end.
USAGE
}

timestamp() { date '+%Y-%m-%dT%H:%M:%S%:z'; }

log() {
	local line
	line="$(timestamp) $*"
	printf '%s\n' "${line}" >&2
	printf '%s\n' "${line}" >> "${LOGFILE}" 2>/dev/null || true
}

die() {
	log "error: $*"
	exit 1
}

run() {
	if [ "${DRYRUN}" = yes ]; then
		log "would run: $*"
		return 0
	fi
	log "run: $*"
	"$@"
}

TMPDIR_PUB=""
cleanup() {
	if [ -n "${TMPDIR_PUB}" ]; then
		rm -rf "${TMPDIR_PUB}"
	fi
	rm -rf "${ISO_DIR}/.stage" 2>/dev/null || true
	return 0
}

fetch() {
	local arg="$1" name
	case "${arg}" in
	http://*|https://*)
		[ -n "${TMPDIR_PUB}" ] || TMPDIR_PUB="$(mktemp -d /var/tmp/muros-publish.XXXXXX)"
		name="$(basename "${arg%%\?*}")"
		log "downloading ${arg}"
		curl -fSL --retry 3 -o "${TMPDIR_PUB}/${name}" "${arg}" >&2
		printf '%s\n' "${TMPDIR_PUB}/${name}"
		;;
	*)
		[ -f "${arg}" ] || die "file not found: ${arg}"
		readlink -f "${arg}"
		;;
	esac
}

signing_key() {
	awk '/^SignWith:/ { print $2; exit }' "${REPO}/conf/distributions"
}

free_bytes() {
	df -Pk "$1" | awk 'NR == 2 { print $4 * 1024 }'
}

fix_permissions() {
	local target
	for target in "${REPO}/dists" "${REPO}/pool" "${REPO}/db" "${ISO_DIR}"; do
		[ -d "${target}" ] || continue
		run chown -R root:root "${target}"
		if [ "${DRYRUN}" = no ]; then
			find "${target}" -type d -exec chmod 755 {} +
			find "${target}" -type f -exec chmod 644 {} +
		fi
	done
}

# apt has no changelog source for a third party repository unless the
# repository publishes one and the client is told where. The client side ships
# with the package, this is the other half: the changelog of the package is
# written where apt expects it, so the firmware page of a box running an older
# version can show what the pending update contains.
publish_changelog() {
	local src pkg version source dir prefix
	src="$1"
	pkg="$2"
	version="$3"

	source="$(dpkg-deb -f "${src}" Source | awk '{ print $1 }')"
	[ -n "${source}" ] || source="${pkg}"

	case "${source}" in
	lib?*) prefix="$(echo "${source}" | cut -c1-4)" ;;
	*) prefix="$(echo "${source}" | cut -c1)" ;;
	esac

	dir="${REPO}/changelogs/main/${prefix}/${source}"

	if [ "${DRYRUN}" = yes ]; then
		log "would publish the changelog to ${dir}/${source}_${version}"
		return 0
	fi

	mkdir -p "${dir}"
	if ! dpkg-deb --fsys-tarfile "${src}" 2> /dev/null |
	    tar -xO ./usr/share/doc/"${pkg}"/changelog.gz 2> /dev/null |
	    zcat > "${dir}/${source}_${version}" 2> /dev/null; then
		rm -f "${dir}/${source}_${version}"
		log "no changelog found in ${src}"
		return 0
	fi

	chmod 644 "${dir}/${source}_${version}"
	log "published the changelog of ${source} ${version}"
}

publish_deb() {
	local src pkg version arch published
	src="$(fetch "$1")"

	dpkg-deb -I "${src}" > /dev/null 2>&1 || die "not a Debian package: ${src}"
	pkg="$(dpkg-deb -f "${src}" Package)"
	version="$(dpkg-deb -f "${src}" Version)"
	arch="$(dpkg-deb -f "${src}" Architecture)"
	[ -n "${pkg}" ] && [ -n "${version}" ] || die "unreadable control fields in ${src}"

	log "package ${pkg} ${version} ${arch}"

	published="$(reprepro -b "${REPO}" list "${DIST}" "${pkg}" 2>/dev/null | awk '{ print $NF }' | tail -1)"
	if [ -n "${published}" ]; then
		log "currently published: ${pkg} ${published}"
		if [ "${published}" = "${version}" ]; then
			if [ "${FORCE}" != yes ]; then
				die "${pkg} ${version} is already published, use --force to replace it"
			fi
			run reprepro -b "${REPO}" remove "${DIST}" "${pkg}"
		fi
	fi

	run reprepro -b "${REPO}" includedeb "${DIST}" "${src}"
	run reprepro -b "${REPO}" deleteunreferenced
	publish_changelog "${src}" "${pkg}" "${version}"
	fix_permissions

	if [ "${DRYRUN}" = no ]; then
		reprepro -b "${REPO}" list "${DIST}" "${pkg}" >&2
	fi
	log "published ${pkg} ${version}"
	PUBLISHED_DEB="${pkg} ${version}"
}

publish_iso() {
	local src size avail stage
	src="$(fetch "$1")"
	stage="${ISO_DIR}/.stage"

	file -b "${src}" | grep -qi 'ISO 9660' || die "not an ISO 9660 image: ${src}"
	size="$(stat -c %s "${src}")"
	[ "${size}" -gt 104857600 ] || die "suspiciously small image (${size} bytes): ${src}"

	mkdir -p "${ISO_DIR}"
	avail="$(free_bytes "${ISO_DIR}")"
	[ "${avail}" -gt $((size + 1073741824)) ] || die "not enough free space in ${ISO_DIR}: ${avail} bytes"

	if [ "${DRYRUN}" = yes ]; then
		log "would publish ${src} as ${ISO_DIR}/${ISO_NAME} (${size} bytes)"
		PUBLISHED_ISO="${ISO_NAME}"
		return 0
	fi

	rm -rf "${stage}"
	mkdir -p "${stage}"

	log "staging ${src} (${size} bytes)"
	cp "${src}" "${stage}/${ISO_NAME}"

	( cd "${stage}" && sha256sum "${ISO_NAME}" > "${ISO_NAME}.sha256" )
	( cd "${stage}" && sha256sum -c "${ISO_NAME}.sha256" >&2 )

	log "signing with $(signing_key)"
	gpg --batch --yes --local-user "$(signing_key)" --armor \
	    --detach-sign -o "${stage}/${ISO_NAME}.asc" "${stage}/${ISO_NAME}"
	gpg --verify "${stage}/${ISO_NAME}.asc" "${stage}/${ISO_NAME}" >&2

	if [ -f "${ISO_DIR}/${ISO_NAME}" ]; then
		if [ "${KEEP_PREVIOUS}" = yes ]; then
			log "keeping the replaced image in ${ISO_DIR}/previous"
			mkdir -p "${ISO_DIR}/previous"
			mv -f "${ISO_DIR}/${ISO_NAME}" "${ISO_DIR}/previous/${ISO_NAME}"
			for suffix in sha256 asc; do
				if [ -f "${ISO_DIR}/${ISO_NAME}.${suffix}" ]; then
					mv -f "${ISO_DIR}/${ISO_NAME}.${suffix}" "${ISO_DIR}/previous/"
				fi
			done
		else
			rm -f "${ISO_DIR}/${ISO_NAME}" "${ISO_DIR}/${ISO_NAME}.sha256" "${ISO_DIR}/${ISO_NAME}.asc"
		fi
	fi

	mv -f "${stage}/${ISO_NAME}" "${ISO_DIR}/${ISO_NAME}"
	mv -f "${stage}/${ISO_NAME}.sha256" "${ISO_DIR}/${ISO_NAME}.sha256"
	mv -f "${stage}/${ISO_NAME}.asc" "${ISO_DIR}/${ISO_NAME}.asc"
	rmdir "${stage}"

	fix_permissions
	log "published ${ISO_NAME}"
	PUBLISHED_ISO="${ISO_NAME}"
}

rollback_iso() {
	local prev="${ISO_DIR}/previous"
	[ -f "${prev}/${ISO_NAME}" ] || die "no rollback copy in ${prev}"
	run mv -f "${prev}/${ISO_NAME}" "${ISO_DIR}/${ISO_NAME}"
	for suffix in sha256 asc; do
		if [ -f "${prev}/${ISO_NAME}.${suffix}" ]; then
			run mv -f "${prev}/${ISO_NAME}.${suffix}" "${ISO_DIR}/${ISO_NAME}.${suffix}"
		fi
	done
	fix_permissions
	log "rolled back to the previous ${ISO_NAME}"
	verify_state
}

verify_state() {
	echo
	echo "apt repository (${REPO}, suite ${DIST})"
	reprepro -b "${REPO}" list "${DIST}" || true
	echo
	echo "release file"
	grep -E '^(Date|Codename|Architectures|Components):' "${REPO}/dists/${DIST}/Release" 2>/dev/null || true
	if [ -f "${REPO}/dists/${DIST}/InRelease" ]; then
		gpg --verify "${REPO}/dists/${DIST}/InRelease" 2>&1 | grep -E 'Signature|Good|Bonne|signature' | head -3 || true
	fi
	echo
	echo "installer image"
	if [ -f "${ISO_DIR}/${ISO_NAME}" ]; then
		ls -l --time-style=long-iso "${ISO_DIR}/${ISO_NAME}" "${ISO_DIR}/${ISO_NAME}.sha256" "${ISO_DIR}/${ISO_NAME}.asc" 2>/dev/null || true
		cat "${ISO_DIR}/${ISO_NAME}.sha256" 2>/dev/null || true
		gpg --verify "${ISO_DIR}/${ISO_NAME}.asc" "${ISO_DIR}/${ISO_NAME}" 2>&1 | grep -E 'Good|Bonne' || true
	else
		echo "none published"
	fi
	if [ -f "${ISO_DIR}/previous/${ISO_NAME}" ]; then
		echo
		echo "rollback copy"
		ls -l --time-style=long-iso "${ISO_DIR}/previous/${ISO_NAME}"
	fi
}

print_urls() {
	echo
	echo "Done."
	if [ -n "${PUBLISHED_DEB}" ]; then
		echo "  package: ${PUBLISHED_DEB}"
		echo "    apt-get update && apt-get install --only-upgrade muros"
	fi
	if [ -n "${PUBLISHED_ISO}" ]; then
		echo "  image:   ${BASE_URL}/iso/${PUBLISHED_ISO}"
		echo "           ${BASE_URL}/iso/${PUBLISHED_ISO}.sha256"
		echo "           ${BASE_URL}/iso/${PUBLISHED_ISO}.asc"
	fi
}

PUBLISHED_DEB=""
PUBLISHED_ISO=""
ARGS=()

while [ $# -gt 0 ]; do
	case "$1" in
	--force) FORCE=yes ;;
	--dry-run) DRYRUN=yes ;;
	--no-previous) KEEP_PREVIOUS=no ;;
	--name) shift; [ $# -gt 0 ] || die "--name needs a value"; ISO_NAME="$1" ;;
	-h|--help) usage; exit 0 ;;
	-*) die "unknown option: $1" ;;
	*) ARGS+=("$1") ;;
	esac
	shift
done

[ ${#ARGS[@]} -gt 0 ] || { usage; exit 1; }

touch "${LOGFILE}" 2>/dev/null || true
[ -d "${REPO}/conf" ] || die "no repository at ${REPO}"
[ -n "$(signing_key)" ] || die "no SignWith key in ${REPO}/conf/distributions"
command -v reprepro > /dev/null || die "reprepro is not installed"

exec 9> "${LOCKFILE}"
flock -n 9 || die "another publication is running"
trap cleanup EXIT

ACTION="${ARGS[0]}"
REST=("${ARGS[@]:1}")
set -- ${REST[@]+"${REST[@]}"}

case "${ACTION}" in
deb)
	[ $# -gt 0 ] || die "usage: muros-publish deb <file.deb> [...]"
	for candidate in "$@"; do
		publish_deb "${candidate}"
	done
	print_urls
	;;
iso)
	[ $# -eq 1 ] || die "usage: muros-publish iso <file.iso>"
	publish_iso "$1"
	print_urls
	;;
release)
	[ $# -eq 2 ] || die "usage: muros-publish release <file.deb> <file.iso>"
	publish_deb "$1"
	publish_iso "$2"
	print_urls
	;;
status|verify)
	verify_state
	;;
rollback-iso)
	rollback_iso
	;;
*)
	die "unknown action: ${ACTION}"
	;;
esac
