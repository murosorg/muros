# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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
