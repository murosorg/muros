# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [v1.0.1-rc21] - 2026-05-28

### Fixed

- HA page header used to display the package name twice
  (`keepalived inactive keepalived 1:2.3.3-1` and the equivalent line
  for conntrackd). The backend `pkg_version("keepalived")` helper
  already prefixes the package name to the version string, while the
  HA page also passes `name="keepalived"` to `ServiceStatusInline`,
  hence the duplication. `ServiceStatusInline` now strips a leading
  `<name> ` prefix from its version slot when a name is also passed,
  so the header now reads `keepalived inactive 1:2.3.3-1`.

### Changed

- System / Maintenance "Mode" wording: `Production (apply on)` /
  `Dry-run (apply off)` are now `Production (apply enabled)` /
  `Dry-run (apply disabled)` to match the rest of the app's tone.

## [v1.0.1-rc20] - 2026-05-28

### Fixed

- `muros-boot.service` now also reconciles `muros-watcher.service`
  against `NotificationConfig.enabled`. The watcher has no on-disk
  config (it reads everything from the DB) so its boot persistence
  used to rely entirely on the systemd enable symlink created by the
  PUT /api/notifications/config route. When that symlink went missing
  (deb-systemd-helper after a package upgrade, a previously masked
  unit, or a non-zero exit code from the toggle's `systemctl enable
  --now`) the watcher silently stayed down after a reboot even though
  the UI showed "Notifications enabled". The new `_restore_watcher`
  step runs `systemctl enable --now` or `disable --now` based on the
  DB flag at every boot, same pattern as `_restore_ha`. Outcome is
  logged in the boot journal so a failed reconcile is no longer
  silent.

## [v1.0.1-rc19] - 2026-05-28

### Added

- `service_dirty.reconcile_all` now also covers `wireguard`, `ipsec`,
  `ssh` and `http`. Each check re-renders the expected on-disk config
  from the DB (`wg0.conf`, `/etc/swanctl/conf.d/muros.conf`,
  `/etc/ssh/sshd_config.d/muros.conf`, the nginx site conf) and clears
  the dirty flag when the SHA-256 already matches. Used to be limited
  to dhcp/dns/snmp/ha; the orange Apply dot will now clear itself for
  the remaining services after an out-of-band reload too.
- SSH page: explicit note shown above the form when the service is
  administratively disabled. Confirms that the configuration is still
  editable and that any Save will be written to the drop-in
  immediately, ready for the next time sshd is re-enabled.

### Changed

- DHCP / DNS page descriptions tightened so the PageHeader subtitle
  reflects every panel on the page, not only the leases / resolver
  block.

## [v1.0.1-rc18] - 2026-05-28

### Changed

- Service pages (HA, IPsec, SSH): the small spinner shown next to the
  service on/off toggle is now driven by a dedicated `toggleBusy` state
  instead of the shared `busy` flag. The spinner only fires while the
  toggle is actually being flipped and no longer flashes during a
  regular Apply or sub-form save.
- Monitoring page: the State pill column now uses `min-w-[96px]` so
  every pill (active, inactive, in error, disabled by admin) aligns
  vertically regardless of label length.
- Sidebar: the `@hostname` suffix next to the admin username has been
  toned down to `text-neutral-500` so the username remains the
  prominent text.
- `ApplyServiceButton`: the clean-state tooltip is now consistent with
  the rest of the app and reads `No pending changes (service is in
  sync with the saved configuration).` instead of a custom wording.

### Fixed

- SSH page: out-of-band reconcile of `ssh_config.admin_disabled`
  is now also triggered on every `GET /api/ssh/status` call. If the
  operator re-enables sshd from the serial console (or the .deb
  postinst does) after the UI toggle has been flipped off, the stale
  flag is cleared on the first page refresh and the running daemon
  stops being labelled `disabled by admin`.

## [v1.0.1-rc6] - 2026-05-28

### Added

- Network page: new `Gateway` column on the physical and VLAN interface
  tables. When the DB has no gateway recorded but the kernel exposes a
  live default route via that interface, the live value is shown with a
  small `(live)` hint so the admin sees what is going to be lost on
  reboot if no IP pinning is done.
- Firewall pages (Rules, NAT, Zones, Services): `View config` button
  next to `Apply`. It opens the compiled nftables ruleset preview at
  any time, even when there is no pending change. Useful for audit and
  for double checking what is currently loaded.
- Ruleset modal: `Copy` button copies the compiled ruleset to the
  clipboard. Falls back to a hidden textarea on browsers without the
  async clipboard API (typical with a snakeoil HTTPS cert).
- Routing page: synthesizes a read-only `default` row for each
  interface that carries a gateway. The admin sees their default
  gateway even when no explicit `StaticRoute` row exists (the
  gateway lives on the interface itself and is materialized by MurOS
  at apply time).

### Fixed

- Kernel adoption now captures the default gateway on the matching
  interface regardless of `ip_mode`. The previous filter
  (`ip_mode == "static"`) silently dropped the default route for
  freshly adopted DHCP boxes, leaving the Routing page empty even
  when a working default gateway was active on the host.
- `GET /api/interfaces/system` now returns the live default gateway
  observed on each interface, so the Network page can flag the live
  value when it is not yet pinned in the MurOS DB.

## [v1.0.1-rc2] - 2026-05-28

### Added

- `CONTRIBUTING.md` with development setup, code style and PR rules.
- `SECURITY.md` with private vulnerability reporting process and scope.
- `CODE_OF_CONDUCT.md` based on Contributor Covenant v2.1.

## [v1.0.0-rc1] - 2026-05-28

First public release candidate. MurOS turns a fresh Debian 13 box into
a web-managed firewall covering the 90% of small and mid-size business
needs: stateful filtering, NAT, VPN (WireGuard + IPsec), high
availability, multi-WAN, DHCP / DNS, monitoring.

See [`README.md`](README.md) for the full feature set and the
[`docs/`](docs/) directory for architecture and operations notes.
