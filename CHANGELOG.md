# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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
