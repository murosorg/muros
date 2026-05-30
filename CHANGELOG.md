# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [v0.9.0-rc86] - 2026-05-30

### Added
- Regression tests for the compiler chain/zone match clause
  (`test_compiler_zones.py`): forward matches both interfaces, input keeps
  only the source interface, output keeps only the destination interface,
  a stale zone on the firewall side is ignored, and a multi-interface zone
  expands to an nft set. Locks in the invariant the redesigned rule form
  relies on.

## [v0.9.0-rc84] - 2026-05-30

### Changed
- New filter rules are now inserted at the top of their chain (lowest
  position, evaluated first) instead of being appended at the bottom.
  Filter rules are first-match wins, so appending a freshly created rule
  below a broader rule already in place silently shadowed it (e.g. a
  "block SSH" landing under "allow LAN" never matched). Inserting first
  matches the intuition that a rule you just created takes effect; order
  can still be refined with drag-and-drop.

## [v0.9.0-rc83] - 2026-05-30

### Added
- QoS / traffic shaping. A new "QoS / Shaping" page (Network section)
  caps and prioritises egress bandwidth per interface using the kernel's
  HTB qdisc with an fq_codel leaf on each class, the same combination
  OpenWrt SQM and VyOS rely on. Closes a real gap versus OPNsense for
  SMBs running VoIP or a saturated uplink. Model: a shaper per interface
  (total egress bandwidth), priority classes (guaranteed rate, ceil,
  priority 0-7), and classifier rules matching protocol / destination
  port / source / destination / DSCP into a class. Unmatched traffic
  falls into a catch-all default class. Configuration is compiled to
  `tc` commands and applied from the page header Apply button; tc state
  is volatile so muros-boot replays the qdisc tree from the DB at every
  boot. New `qos` managed service (dirty tracking + sidebar indicator),
  REST API under `/api/qos`, and a pure compiler test suite.

## [v0.9.0-rc82] - 2026-05-30

### Fixed
- Rollback confirmation countdown stuck at 0s for any client not on UTC.
  The backend stores naive UTC datetimes and serialized `expires_at`
  without a timezone offset, so the browser parsed it as local time and
  the timer expired in the past on the first render (e.g. a GMT+2 client
  saw "Automatic rollback in 0s" immediately, with no real auto-rollback
  window). All three rollback sources (nftables apply, safe_apply pending
  changes, DB-backed pending applies for http/ssh/tls) now emit
  offset-aware UTC timestamps via a shared `iso_utc` helper, so the
  countdown shows the full configured timeout regardless of client
  timezone.

## [v0.9.0-rc80] - 2026-05-30

### Removed
- Dead `service_id` field on the synthetic "Default policy" row in the
  rules list. `FirewallRule` has no such field (only `service_group_id`);
  it was a leftover from a removed concept, silently allowed by an
  `as unknown as FirewallRule` cast.

### Changed
- The synthetic "Default policy" row is now a fully type-checked
  `FirewallRule` (the unsafe double cast is gone), so any future field
  drift is caught at compile time instead of slipping through.

## [v0.9.0-rc78] - 2026-05-30

### Changed
- Filter rule form redesigned around a plain "From -> To" traffic flow.
  Each endpoint is now a single dropdown that can be a zone, "any zone" or
  "This firewall", and the netfilter chain (input/forward/output) is
  derived automatically from that choice. The "Chain" jargon and the
  separate chain selector in Advanced options are gone.
- Creating a rule no longer forces it into the active tab's chain. The
  chain follows the From -> To selection, so a rule "To the firewall" can
  be created from any tab; the list then jumps to the tab the new rule
  landed in. Previously, standing on the default Forward tab made it
  impossible to add an input or output rule without first switching tabs.
- Rules list tabs relabeled from the raw chain names to "Through
  firewall" / "To firewall" / "From firewall", each with an explanatory
  tooltip. The empty-state hint now reflects the actual default policy
  (accept on the output chain, drop elsewhere).

## [v0.9.0-rc77] - 2026-05-30

### Changed
- Dashboard "Manage updates" now deep-links to the System Updates tab
  (`/system/updates`) instead of the default General tab.
- The dashboard version card refreshes its status every 60s so the
  up-to-date / update-available badge stays current while the dashboard is
  left open (reads cached apt metadata, no `apt-get update`).

## [v0.9.0-rc76] - 2026-05-30

### Fixed
- Firewall rule form: creating a rule from the Input or Output tab no
  longer falls back to the Forward layout. The form opened with two zone
  selectors and could save a nonsensical "any zone -> any zone" rule on a
  single-ended chain, because the reset effect ignored the chain the page
  opened it with. It now honors the active chain and shows "This firewall"
  for the fixed endpoint (destination on input, source on output).
- Chain/zone invariant is now enforced end to end: the API clears the
  destination zone on input rules and the source zone on output rules
  (rule create and update), and the nftables compiler ignores the zone
  that does not apply to the chain, so a stale selection can never compile
  into a rule that never matches.
- Rules list filter: the always-on "Default policy" row is now hidden
  while a text filter is active, so the list visibly narrows to the
  matching rules (and shows "No rule matches the filter" when empty). The
  filter also matches the source port and the chain name.

## [v0.9.0-rc74] - 2026-05-30

### Added
- Tests for the changelog parser (parsing, version matching, fallback to
  the latest released section, and missing-file handling).

### Changed
- Release notes are now rendered with a small built-in formatter (headings
  and bullet lists) instead of raw text, shared between the dashboard
  version card and the System updates page.

## [v0.9.0-rc70] - 2026-05-30

### Added
- Management-lockout guard on firewall applies. Before applying, MurOS
  statically evaluates the input chain against a new connection from the
  operator's source to the web UI and SSH ports. If the ruleset would
  block new management connections (the commit-confirm modal cannot catch
  this because conntrack keeps the current session alive), the Apply
  modal shows a blocking warning that must be acknowledged. New endpoint
  `GET /api/firewall/apply/lockout-check`; `POST /api/firewall/apply`
  refuses with 409 unless `acknowledge_lockout` is set.
- Early "Management lockout risk" indicator next to the Apply button on
  the firewall pages, shown as soon as there are pending changes that
  would block new management connections (before opening the modal).
- MurOS version card on the dashboard: shows the installed version, an
  up-to-date / update-available badge, the candidate version on the apt
  channel, and the changelog of the latest version, with a link to manage
  updates. The changelog is read from the `CHANGELOG.md` shipped with the
  package and exposed by `GET /api/updates/muros` (`release_notes`).

### Changed
- Firewall apply now honors the configurable `apply_confirm_timeout`
  setting (60s by default) instead of a hardcoded 10s countdown.
- API client surfaces structured error details (`detail.message`)
  instead of stringifying them to `[object Object]`.
- Dashboard reorganized: the service inventory, storage and the version
  card sit at the top; the resource KPIs (CPU, memory, conntrack, load)
  move to a row below. The service list groups essential (always-on)
  services and only shows optional ones when active or in error, with a
  summary line for the rest.

### Internationalization
- Translated the remaining French comments and user-facing error strings
  to English across the backend (core modules, `wireguard.py`,
  `ipsec.py`, audit labels and API messages).

## [v0.9.0-rc53] - 2026-05-30

### Added
- System settings page (hostname, timezone, locale, console keymap),
  applied live and persisted through systemd (hostnamectl / timedatectl /
  localectl). Changing the hostname also updates `/etc/hosts` and
  refreshes the self-signed UI certificate.
- Management fallback: if a box boots with no usable IP at all, MurOS
  assigns a deterministic management address (default `192.168.1.1/24`)
  on a physical interface and serves DHCP on it via Kea, so the UI stays
  reachable from a directly attached laptop without a shell.

### Changed
- Installer ISO default credentials are now **`root` / `root`**. The
  password is deliberately AZERTY/QWERTY-safe so the first console login
  is never blocked by a keyboard layout mismatch; override with
  `MUROS_ROOT_PASSWORD` and rotate it after first login. The ISO also
  preinstalls `kbd` and `console-setup` so `loadkeys` works at the
  console.

### Fixed
- Installer ISO: the unattended install no longer fails with apt exit
  code 100. The preseed now disables the CD-ROM apt source (a lingering
  `deb cdrom:` line broke `apt-get update` since the disc is not mounted
  in the installed system), and `install.sh` defensively comments out any
  such line. The netinst point release is auto-detected instead of being
  pinned to an outdated version, and the hybrid ISO repack was fixed.

## [v0.9.0-rc30] - 2026-05-30

### Added
- IPv6 Router Advertisements / SLAAC (lot 2 of IPv6 support). MurOS can now
  act as the IPv6 router for a LAN interface via radvd: it advertises a /64
  prefix derived from the interface's own IPv6 address so clients
  autoconfigure an address and default route (SLAAC). Optional RDNSS
  (advertise the firewall as IPv6 resolver) and the M/O flags to hand off
  to DHCPv6. Managed from a new card on the DHCP services page (it is the
  IPv6 counterpart of the IPv4 DHCP server). radvd ships as a held daemon,
  started only when Router Advertisements are enabled, and the config is
  reconciled at boot once the LAN interface is up. IPv6 forwarding is
  enabled automatically when RA is on. New /api/ipv6/ra endpoints.

## [v0.9.0-rc28] - 2026-05-30

### Added
- CI regression coverage for the per-service config generators. The
  backend pytest job now exercises the rendering path of WireGuard (VPN),
  SNMP, NTP (chrony), DHCP (Kea) and DNS (Unbound) on every push and pull
  request, so a change that breaks the VPN or SNMP config is caught in CI
  instead of on a customer box. Tests are pure / DB-only (no systemd, no
  netlink) and assert the key invariants: WireGuard interface and peer
  sections, disabled peers excluded, X25519 keypair round-trip, SNMP
  one-rocommunity-per-CIDR plus the empty-network loopback guard-rail, the
  chrony "allow all" gating on server mode, valid idle Kea JSON, and the
  Unbound access-control plus defensive allowlist.

## [v0.9.0-rc29] - 2026-05-30

### Added
- Unattended installer ISO builder (`packaging/iso/`). `build-iso.sh`
  turns a stock Debian 13 (trixie) netinst image into a fully automated
  MurOS installer: it injects the `preseed.cfg` answers into the
  installer initrd, patches the BIOS (isolinux) and UEFI (grub) boot
  menus for hands-off boot, and repacks a hybrid bootable ISO with
  xorriso. The install partitions the first disk (guided LVM), lays down
  a minimal Debian base, then runs the official `install.sh` to register
  apt.muros.org and pull the muros package. No installer question is
  asked, so building appliances no longer requires clicking through the
  Debian installer by hand. Root password is configurable via
  `MUROS_ROOT_PASSWORD`; see `packaging/iso/README.md`.

## [v0.9.0-rc27] - 2026-05-30

### Added
- IPv6 firewall filtering (lot 1 of IPv6 support). Address-based rules are
  now family-aware: the compiler emits `ip saddr/daddr` for IPv4 values
  and `ip6 saddr/daddr` for IPv6 values inside the `inet` table, instead
  of only ever matching IPv4. A rule (or address group) mixing both
  families expands to one variant per family. ICMP rules now match both
  ICMP and ICMPv6 (`meta l4proto { icmp, ipv6-icmp }`), and the forward
  chain gained the ICMPv6 baseline (NDP / Packet-Too-Big) required for
  routed IPv6 to work. Previously any address or ICMP rule silently
  skipped IPv6 traffic, which the drop policy then blackholed.

## [v0.9.0-rc25] - 2026-05-30

### Added
- First-boot setup wizard. Until the operator assigns the network
  interfaces, the UI redirects to a one-step wizard that asks which NIC is
  the WAN (Internet, DHCP client) and which is the trusted LAN (with its
  CIDR). Applying it wires the zones and drops the permissive "any to
  firewall" bootstrap rules, so the box reaches its final posture (LAN
  reaches the firewall and its services, WAN default-deny) instead of
  staying in the open bootstrap state. Services keep listening on every
  interface; access is enforced by the firewall zones, not per-service
  binds. Boxes already configured (zones assigned, or upgraded) skip the
  wizard. New /api/setup/state and /api/setup/apply endpoints.

## [v0.9.0-rc24] - 2026-05-30

### Added
- NTP (chrony) now appears on the dashboard service list, and can be
  turned on or off from a master enable toggle on the NTP page (like the
  other service pages). Disabling stops chrony and keeps it down across
  reboots, after a confirmation prompt.

### Changed
- Dashboard service list is now split by install state: services enabled
  out of the box (backend, nginx, fail2ban, SNMP, NTP, DHCP, DNS) sit in
  the left column, on-demand ones (SSH, HA, VPN, MurOS feature daemons)
  on the right, instead of an arbitrary half/half split.

## [v0.9.0-rc22] - 2026-05-30

### Added
- Two-factor authentication (TOTP, RFC 6238) for the web UI login. When
  enabled on an account, the password step returns a short-lived token
  and the login asks for a 6-digit code from an authenticator app
  (POST /api/auth/login/verify). Enrolment is self-service from the HTTP
  Access page (QR code + manual secret), confirmed with one code; the
  intermediate token is scoped so it can never be used as an access
  token. Disabling requires a current valid code. (pyotp dependency.)
- DHCP <-> DNS integration. Unbound now publishes DHCP hostnames as local
  DNS records under a configurable lease domain (default "lan"), so LAN
  clients resolve each other by name (e.g. nas.lan). Static reservations
  are DB-driven and deterministic; active dynamic leases are read from
  the Kea lease file on apply. Manual local records take precedence, the
  WAN is unaffected. Toggle and lease domain on the DNS server page;
  a DHCP apply now also refreshes the DNS records.

## [v0.9.0-rc21] - 2026-05-30

### Fixed
- Logs: removed the phantom "muros-nft.service" entry from the System
  journal viewer dropdown. There is no such systemd unit (nftables is
  loaded by muros-boot.service at boot and applied directly by the
  backend), so selecting it returned nothing. The journal unit list is
  now realigned with the service catalog.

## [v0.9.0-rc20] - 2026-05-30

### Changed
- Frontend: page components are now code-split with React.lazy and loaded
  on demand. The initial JavaScript bundle dropped from ~577 kB to
  ~216 kB (gzip ~149 kB to ~68 kB), so the UI paints faster on the modest
  hardware MurOS targets. A lightweight "Loading..." placeholder is shown
  inside the layout shell while a page chunk is fetched, keeping the
  sidebar visible during navigation. No functional change.

## [v0.9.0-rc19] - 2026-05-30

### Changed
- Dashboard: removed the redundant "Per-interface traffic" table, which
  duplicated the per-interface "Traffic <iface>" charts already shown in
  the History section. The History section (CPU/memory, connection
  tracking sessions, system load, per-interface traffic) now sits right
  below the live metric cards for quicker access, with the Storage table
  moved to the bottom.

## [v0.9.0-rc18] - 2026-05-30

### Changed
- Internal: migrated the Pydantic schemas from the deprecated class-based
  `Config` to `model_config = ConfigDict(...)` (removes the Pydantic V2
  deprecation warnings and is ready for Pydantic V3) and cleaned up the
  remaining lint findings. No functional change.

## [v0.9.0-rc17] - 2026-05-30

### Changed
- Default firewall rules now follow the OPNsense model: the LAN is the
  trusted zone and gets an "allow LAN to firewall" (input) plus an "allow
  LAN to any" (forward) rule out of the box. Without this the input
  policy drop blocked LAN clients from reaching box services such as DNS
  (53) and NTP (123) even though those services were enabled by default.
  The WAN stays default-deny inbound. These bootstrap rules carry a
  "restrict once configured" comment and can be tightened from the UI.

## [v0.9.0-rc16] - 2026-05-30

### Fixed
- The dashboard no longer shows SSH as "disabled by admin". SSH is now
  treated like every other service and simply shows as inactive when it
  is stopped. SSH is still disabled on a fresh install and can be turned
  back on manually from the SSH page toggle.

## [v0.9.0-rc15] - 2026-05-30

### Added
- NTP server mode (chrony) for the LAN, enabled by default. chrony now
  emits an `allow <subnet>` directive for every LAN-side network (every
  static interface whose zone is not a WAN zone), so the firewall serves
  time to LAN clients out of the box, like an OPNsense appliance. The WAN
  is never served (no `allow all`) to avoid NTP reflection/amplification.
  A "Serve time to LAN clients" toggle on the Services > NTP server page
  controls it (NtpConfig.serve_lan, default on); the served subnets are
  shown there. Server mode is reconciled at boot by muros-boot once the
  LAN interfaces are up.

### Note
- Reaching the NTP server from the LAN still requires the firewall to
  accept udp/123 from the LAN zone (add an allow rule if your ruleset is
  restrictive).

## [v0.9.0-rc13] - 2026-05-30

### Fixed
- Login still broken even with python-pam present: python-pam 2.0.2
  imports `six` at import time but does not declare it as a dependency,
  so `import pam` failed with "No module named 'six'" and PAM auth was
  unusable on a deployed box. `six` is now pinned in requirements.txt.
- The PAM loader no longer hides the underlying import error behind a
  generic "python-pam is not available" message; it logs and reports the
  real exception so a missing library or dependency is diagnosable.

## [v0.9.0-rc12] - 2026-05-30

### Fixed
- Login broken after a reinstall/upgrade ("python-pam is not available").
  The postinst only installed the Python requirements when the venv had
  no uvicorn, so a reused venv from an older release never received newly
  added dependencies (here python-pam, required for PAM auth). The
  postinst now always runs `pip install -r requirements.txt`, keeping the
  venv in sync with the shipped requirements on every install and upgrade.
- Uninstall now leaves a clean box: it flushes the live nftables ruleset
  (the kernel keeps the rules loaded by muros-boot otherwise, leaving the
  box firewalled by an unmanaged ruleset) and removes the kernel hardening
  sysctl drop-in then reloads sysctl, so forwarding / rp_filter revert to
  the Debian defaults.

## [v0.9.0-rc11] - 2026-05-30

### Fixed
- Uninstall no longer leaves the box without working DNS. When "Unbound
  as system resolver" was enabled, `/etc/resolv.conf` pointed at
  127.0.0.1; after removing MurOS (and Unbound) every DNS lookup stalled
  on the dead local resolver, so `apt update` hung at 0% and reinstalling
  was impossible. uninstall.sh now restores the pre-Unbound resolver
  backup and, as a safety net, replaces a loopback-only resolv.conf with
  public resolvers. install.sh applies the same DNS preflight so a box
  stuck in that state can still be reinstalled.

## [v0.9.0-rc10] - 2026-05-30

### Changed
- The root administrator now keeps its existing system password. MurOS
  no longer resets it to `muros` at install and no longer forces a change
  on first login: you log into the web UI with the password root already
  has for the shell / console. (The dev-only fallback stays root/muros
  when MUROS_APPLY is off.)
- NTP moved to its own Services page (`/services/ntp`, "NTP server" in the
  sidebar) instead of a tab under System. `/system/time` redirects there.

### Fixed
- Documentation (README, FAQ, quickstart, packaging, site) updated for the
  root account, Kea, chrony and the per-account web UI access model.

## [v0.9.0-rc9] - 2026-05-30

### Changed
- Dashboard is now a live view. The summary endpoint is sampled twice
  per second (was every 3s) and the per-interface traffic and history
  charts are fed from an in-memory ring buffer instead of the backend
  60s collector, so they update in real time.
- History window selector now offers 1 / 5 / 15 minutes and defaults to
  5 minutes (was 1 / 6 / 12 / 24 hours, default 24h). Time charts pin
  the x axis to the selected window and show minute:second labels for
  sub-hour spans.
- Authentication now goes through PAM: the web UI and SSH share the same
  Linux accounts. The UI login is validated against the system password
  (pam_unix on /etc/shadow), and changing the password from the UI also
  changes it for SSH. The default administrator is the system `root`
  account (password `muros`, forced change on first UI login). No
  separate `admin` account is created at install.
- DHCP server now uses ISC Kea (`kea-dhcp4-server`) instead of dnsmasq.
  Kea is DHCP-only and never binds port 53, so it coexists with Unbound
  with no possible collision. The DHCP configuration is rendered to
  `/etc/kea/kea-dhcp4.conf`; leases are read from the Kea memfile CSV.
- NTP now uses chrony instead of systemd-timesyncd. chrony is enabled by
  default and managed from System > Time (`/etc/chrony/conf.d/muros.conf`).
- No-configuration services start by default at install: DHCP (Kea), DNS
  (Unbound), NTP (chrony), SNMP, plus the management plane (nginx, backend,
  fail2ban). Services that need a per-site configuration (HA, VPN) stay
  disabled until enabled from the UI.

### Added
- Access > Users page (administrators only). The web UI and SSH share the
  PAM stack, so any Linux account could authenticate; this page controls
  which accounts are actually allowed into the web UI. Only `root` is
  granted by default, every other account stays locked out until root
  grants it access. Granted accounts can optionally be promoted to
  administrator.

### Removed
- The five summary cards at the top of the dashboard (Interfaces up,
  Total throughput, Conntrack, Pending changes, Last apply). The same
  information is available from the metric cards and the relevant pages.

### Security
- SSH is closed by default. `openssh-server` ships but ssh.service /
  ssh.socket are disabled on a fresh install; the operator opens SSH
  from the SSH access page once keys and restrictions are in place.
- Root login over SSH defaults to `prohibit-password` (key only, never
  password), so root can open an SSH session with a key once SSH is
  enabled while password login for root stays refused.
- Web UI access is gated per account: passing PAM is not enough, an
  account must be explicitly granted access by root to sign in.

## [v0.9.0-rc6] - 2026-05-29

### Changed
- CI runs JavaScript actions on Node 24 (FORCE_JAVASCRIPT_ACTIONS_TO_NODE24)
  ahead of the Node 20 removal on GitHub runners.

### Security
- The apt deploy key wrapper now requires an explicit release tag. A bare
  SSH connection with no command is refused instead of defaulting to
  publishing "latest".

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
