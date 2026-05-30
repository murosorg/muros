# MurOS

[![Release](https://img.shields.io/github/v/release/murosorg/muros?include_prereleases&label=release)](https://github.com/murosorg/muros/releases)
[![CI](https://img.shields.io/github/actions/workflow/status/murosorg/muros/ci.yml?branch=main&label=CI)](https://github.com/murosorg/muros/actions)
[![License: AGPL v3](https://img.shields.io/badge/license-AGPL%20v3-blue.svg)](LICENSE)
[![Debian 13](https://img.shields.io/badge/Debian-13%20Trixie-A81D33?logo=debian&logoColor=white)](https://www.debian.org/)
[![Website](https://img.shields.io/badge/website-muros.org-f59e0b)](https://muros.org)

A firewall appliance built on Debian 13, with every network service built
natively on top and managed from a single web UI. An open source
alternative to pfSense and OPNsense: web-managed, Debian-native, zero
subscription. Covers the 90% of small and mid-size business needs:
stateful filtering, NAT, routing, multi-WAN, VPN (WireGuard + IPsec), high
availability, DHCP, recursive DNS, SNMP and monitoring.

Website: [muros.org](https://muros.org)

![MurOS dashboard](docs/screenshots/dashboard.png)

## Why MurOS

- **Pure Debian, no fork.** Boots and debugs like a regular Debian 13 box.
  `journalctl`, `nft`, `ip`, `systemctl` work as you expect, no custom CLI
  on top of FreeBSD.
- **Single source of truth in SQLite.** The UI, the API and the boot-time
  applier all read the same DB. No drift between running config and files.
- **Dry-run by default.** Every change is staged in DB first. The kernel
  push only happens when you click Apply, and bad rulesets auto-rollback.
- **Drop-ins over file rewrites.** When a daemon supports drop-ins MurOS
  uses them, so your native Debian config stays untouched and visible.
- **One install command.** No appliance image, no custom kernel. Just
  `apt install muros` on a fresh Debian.

## Quick start

Prerequisites: a freshly installed Debian 13 machine with root access and
one reachable interface.

```bash
curl -fsSL https://apt.muros.org/install.sh | sudo bash
```

The installer registers the signed apt repository and installs the
package, so upgrades are just `apt update && apt install --only-upgrade
muros`. Then open `https://<firewall-ip>` in a browser:

- Login: `root`
- Password: the existing system root password (MurOS does not change it)

To remove cleanly: `curl -fsSL https://apt.muros.org/uninstall.sh | sudo bash`.

## Modules

| Domain | Features |
| --- | --- |
| Filtering | Zones, interfaces (IP, VLAN, MTU), nft rules, rate-limit, log, live per-rule counters |
| NAT | SNAT, DNAT, masquerade, redirects, drag-and-drop reorder |
| Routing | Static routes, multi-WAN failover with ICMP probes |
| DHCP | Kea backend, per-interface pools, static leases, live lease view |
| DNS | Unbound recursive resolver, DNSSEC, forwarders, local records |
| NTP | chrony, custom server list, live sync status |
| VPN | WireGuard (config + peers) and IPsec (PSK/cert, integrated PKI) |
| HA | VRRP, conntrackd, VIPs, inter-node DB sync, automatic takeover |
| Monitoring | CPU/RAM/conntrack/traffic, SNMP, firewall logs, UI audit log |
| Notifications | SMTP mail, event watcher, configurable postfix relay |
| Backups | Local DB snapshot/restore, remote (rclone, ftp, ssh) |
| Diagnostic | ping, traceroute, dig, tcpdump, conntrack from the UI |
| System | Hostname, timezone/locale, DNS, apt updates, reboot/shutdown |
| Access | TLS UI cert, SSH, nginx HTTP access, PAM accounts (UI + SSH share Linux users) |
| Hardening | sysctl, sshd, fail2ban, journald (clean drop-ins) |

Everything that ships is built natively into the core, with no plugins to
add. On the roadmap: OSPF/BGP, IDS/IPS (Suricata), external auth (LDAP/AD).

## Source of truth in SQLite

The DB is the source of truth and the only thing you need to back up. MurOS
uses drop-ins when a service supports them, and regenerates the full file
otherwise. It **never writes** to `/etc/network/interfaces`,
`/etc/systemd/network/` nor `/etc/netplan/`: interfaces, VLANs and routes
are replayed from the DB at boot by `muros-boot.service`.

## API

The UI consumes a complete REST API under `/api/*` with JWT Bearer auth.
Auto-generated OpenAPI doc at `https://<firewall>/docs`.

```bash
TOKEN=$(curl -sk -X POST https://firewall/api/auth/login \\
  -H 'Content-Type: application/json' \\
  -d '{"username":"root","password":"mypass"}' | jq -r .access_token)

curl -sk https://firewall/api/firewall/rules -H "Authorization: Bearer $TOKEN"
```

## Local development

```bash
make install   # create the Python venv and install npm packages once
make backend   # terminal 1: FastAPI on :8000
make frontend  # terminal 2: Vite dev server on :5173
```

Frontend dev at <http://localhost:5173> (proxies `/api` to `:8000`),
Swagger at <http://localhost:8000/docs>.

## Documentation

See the [`docs/`](docs/) folder: [concepts](docs/concepts.md),
[first filter](docs/first-filter.md), [FAQ](docs/faq.md). Delivered
features are tracked in [`CHANGELOG.md`](CHANGELOG.md).

## License

MurOS is distributed under the **GNU AGPL v3.0 or later**. See
[`LICENSE`](LICENSE) for the full text.

The canonical spelling is **MurOS**. It is unrelated to *Murus*, the
commercial macOS PF front-end at <https://www.murusfirewall.com/>; both
names derive from Latin *murus* (wall) and the proximity is coincidental.

Issues: <https://github.com/murosorg/muros/issues>
