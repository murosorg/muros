# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [v0.9.0-rc5] - 2026-05-29

### Changed
- The in-product MurOS updater now goes through apt (apt.muros.org)
  instead of GitHub. The upgrade candidate is read from `apt-cache
  policy muros`, and the upgrade runs `apt-get install --only-upgrade
  muros` (integrity guaranteed by the repository GPG signature), still
  detached via systemd-run so the backend can restart safely. No more
  .deb download or SHA-256 check in the backend.
- All install/uninstall instructions (docs, packaging README, uninstall
  message) now point to apt.muros.org. GitHub Releases remain available
  as an artifact mirror but are no longer the official procedure.
- System > Updates UI: source shown as apt.muros.org.

### Removed
- Dead GitHub release-fetching code in backend/app/updates.py
  (_fetch_latest_release, HTML 302 fallback, .deb download/sha256).

## [v0.9.0-rc4] - 2026-05-29

### Added
- CI auto-publishes every release to apt.muros.org (publish-apt job in
  build-deb.yml) through a deploy key locked server-side to a single
  forced command. A git tag now publishes to GitHub and apt at once.

## [v0.9.0-rc3] - 2026-05-29

### Changed
- install.sh is now apt-native: it registers the signed apt.muros.org
  repository and runs apt install muros, instead of downloading the .deb
  from GitHub and resolving the version through the releases feed. This
  removes the GitHub rate-limit and pre-release edge cases entirely.
- Official install and uninstall one-liners now point at the project
  domain: https://apt.muros.org/install.sh and
  https://apt.muros.org/uninstall.sh.
- MUROS_VERSION now takes an apt version (0.9.0-rcN), a leading v is
  tolerated.

## [v0.9.0-rc2] - 2026-05-29

### Added
- Signed apt repository at https://apt.muros.org. The installer now
  registers the repository and its signing key, so upgrades flow through
  apt and unattended-upgrades. A manual setup snippet is documented in
  the README.

### Changed
- README positioning made explicit: open source alternative to pfSense
  and OPNsense.

### Removed
- uninstall.sh now removes the apt source and keyring for symmetry.

## [v0.9.0-rc1] - 2026-05-29

First public release candidate. MurOS turns a fresh Debian 13 box into
a web-managed firewall covering the 90% of small and mid-size business
needs: stateful filtering, NAT, VPN (WireGuard + IPsec), high
availability, multi-WAN, DHCP / DNS, monitoring.

See [`README.md`](README.md) for the full feature set and the
[`docs/`](docs/) directory for architecture and operations notes.
