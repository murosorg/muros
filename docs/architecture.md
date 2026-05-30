# Architecture

This document maps the moving parts of a MurOS install: which files it
owns on disk, which daemons it manages, and how an operator action in
the UI propagates down to the kernel.

## Layout on disk

```
/opt/muros/                                  code (FastAPI backend + built Vite frontend)
/opt/muros/backend/venv/                     Python venv built at postinst
/var/lib/muros/muros.db                      SQLite, the only source of truth
/var/lib/muros/muros-secret.key              JWT signing secret (mode 0600)
/var/lib/muros/backups/                      DB + config snapshots
/etc/muros/                                  out-of-band configuration owned by muros user
/etc/muros/jwt.key                           JWT HMAC key
/etc/muros/unattended.json                   unattended-upgrades schedule (UI-managed)
```

Generated configuration files (rewritten on every Apply):

```
/etc/muros/nftables.conf                     full ruleset (filter + NAT)
/etc/keepalived/keepalived.conf              VRRP
/etc/conntrackd/conntrackd.conf              state replication
/etc/wireguard/wg0.conf                      WireGuard
/etc/swanctl/conf.d/muros.conf               StrongSwan
/etc/kea/kea-dhcp4.conf                      DHCP server (Kea)
/etc/unbound/unbound.conf.d/muros.conf       recursive DNS
/etc/ssh/sshd_config.d/muros.conf            SSH drop-in
/etc/fail2ban/{filter.d,jail.d}/muros*       fail2ban
/etc/snmp/snmpd.conf.d/muros.conf            SNMP
/etc/sysctl.d/99-muros-hardening.conf        sysctl
/etc/nginx/sites-available/muros             nginx site
```

MurOS never writes to `/etc/network/interfaces`, `/etc/systemd/network/`
or `/etc/netplan/`. Interfaces, VLANs and routes are replayed from the
DB by `muros-boot.service` before `network-online.target`.

## Daemons managed

| Service | Role | When MurOS touches it |
| --- | --- | --- |
| muros-backend.service | FastAPI + uvicorn behind nginx | always running |
| nginx | HTTPS termination, /api proxy | reload on HTTP access change |
| muros-boot.service | replays interfaces/routes at boot | oneshot, before network-online |
| muros-watcher.service | alert loop (SMTP / webhook) | enabled when notification rules exist |
| muros-wan-monitor.service | multi-WAN ICMP probes | enabled when >1 WAN gateway |
| nftables | filter + NAT | `nft -f` on Apply |
| keepalived | VRRP active/passive | restart on HA Apply |
| conntrackd | conntrack state sync | restart with keepalived |
| kea-dhcp4-server | DHCP only (never binds port 53) | restart on DHCP Apply |
| unbound | recursive DNS | restart on DNS Apply |
| strongswan | IPsec | swanctl --load-all on IPsec Apply |
| wg-quick@wg0 | WireGuard tunnel | wg syncconf on peer Apply (no tunnel drop) |
| fail2ban | bruteforce protection | reload on rule change |
| snmpd | SNMP agent | restart on SNMP Apply |
| chrony | NTP (enabled by default) | restart on Time Apply |

## Apply pipeline

Every write action follows the same shape:

1. **Stage in DB.** The HTTP route validates and persists to `muros.db`.
2. **Render.** A `services/<module>_apply.py` reads the DB and writes the
   target configuration file under `/etc/...`.
3. **Reload.** `systemctl reload` or `restart` for the relevant daemon.
   For nftables we use `nft -f` so the kernel swap is atomic.
4. **Auto-rollback.** Filter / NAT applies open a control window
   (60 seconds by default, configurable: 10/30/60/120/300). If the
   operator does not confirm in the modal, MurOS restores the previous
   ruleset from `/var/lib/muros/nftables.last`.

For read paths, no Apply is needed: the routes hit `/proc`, `nft`,
`conntrack`, `journalctl`, `ip -j ...` directly and stream results.

## Database schema

SQLite with SQLAlchemy ORM. Schemas live under `backend/app/models/`.
The canonical tables: `interface`, `vlan`, `route`, `wan_gateway`,
`zone`, `firewall_rule`, `nat_rule`, `dhcp_pool`, `dhcp_static_lease`,
`dns_record`, `wg_peer`, `ipsec_connection`, `ipsec_certificate`,
`ha_config`, `user`, `audit_log`, `notification_rule`.

## Boot sequence

```
systemd-boot
  -> systemd
     -> muros-boot.service (oneshot)
        - reads DB, brings interfaces / VLANs / routes up
        - generates /etc/muros/nftables.conf, loads it
     -> network-online.target
     -> muros-backend.service, nginx, keepalived, conntrackd,
        kea-dhcp4-server, unbound, strongswan, wg-quick@wg0 (if enabled)
```

A failure in muros-boot is non-fatal for the OS: SSH and the
emergency console still come up so the operator can recover.
