# High availability

MurOS HA is a classic active/passive pair built on keepalived (VRRP
for the VIPs) and conntrackd (state replication). On top of the
Linux pieces, MurOS adds a DB replication so the standby always
knows the full configuration of the master.

## Topology

```
            VIP 203.0.113.1                   VIP 10.10.0.1
                |                                  |
         (WAN) eth0 -------- internet -------- eth0 (WAN)
                                                   |
  master ---- eth1 (LAN) ---- switch ---- eth1 (LAN) ---- backup
                |              VRRP +              |
                |              conntrack           |
                |              sync                |
         (sync) eth2 ------ direct cable ------ eth2 (sync)
```

The sync link can be either a dedicated NIC or a VLAN on the LAN
switch. MurOS does not impose a topology, but a direct cable keeps
VRRP advertisements out of the broadcast domain and survives a LAN
switch reboot.

## What you configure in the UI

The `/ha` page exposes one form:

- **Role** : master or backup. The priority is computed from the
  role (150 / 100) and is the same on every VRRP instance.
- **Authentication password** : VRRP IPSEC-AH shared secret.
- **Sync interface** : link used by conntrackd for the multicast
  state replication (default group `225.0.0.50`).
- **Peer management IP** : used for the inter-node DB sync over
  HTTPS (mutual JWT, separate from the operator login).
- **VIPs** : one virtual IP per interface that should fail over.
  Each VIP carries its own VRID (10, 11, 12 ...).

Applying this page regenerates `/etc/keepalived/keepalived.conf`,
`/etc/conntrackd/conntrackd.conf` and restarts both services. The
DB sync agent (`muros-watcher` on the master) pushes updates over
HTTPS as they happen.

## What survives a failover

- All TCP and UDP connections tracked in conntrack (web sessions,
  SSH from the LAN to a DMZ host, IPsec SAs replicated by charon).
- The active WireGuard interface : peers reconnect within their
  persistent keepalive interval (default 25 s).
- DHCP leases : dnsmasq writes them to disk in a path that is part
  of the replicated MurOS DB snapshot.

## What does NOT survive a failover

- Live diagnostic streams (tcpdump, traceroute) in the UI. The
  operator restarts them after takeover.
- The current SSH session to the master IP : reconnect to the VIP
  to land on the new master.

## Asymmetric pairs

Identical hardware is recommended but not required. As long as the
logical interfaces (zone names + roles) match on both sides,
keepalived owns the VIPs and conntrackd replays sessions. Two valid
patterns:

- **Same vendor, same NIC count**, identical cabling. Simplest.
- **Different boxes** (e.g. master = 1U Xeon, backup = mini-PC).
  The backup needs enough CPU and RAM to terminate the same
  encrypted tunnels at the actual traffic rate or it will become
  the bottleneck during a takeover.

## Verifying the pair is healthy

From either node:

```
ip -br addr | grep -E 'vrrp|UP'
journalctl -u keepalived -n 50 --no-pager
conntrackd -i | head -20
curl -sk https://<peer-ip>/api/health -H "Authorization: Bearer $TOK"
```

The dashboard tile **Cluster status** consolidates the above into
one page: local role, peer role, last failover, sync state.

## Common pitfalls

- **VRID collision** with another VRRP cluster on the same L2.
  Each MurOS VIP picks 10..19 by default; change them if you
  already run keepalived elsewhere.
- **Asymmetric ruleset** : the DB sync prevents this in normal use,
  but if you ever edit `/etc/nftables.conf` by hand on the backup,
  the next failover will surprise you. Trust the UI.
- **Anti-spoof filters upstream** : some carriers drop a packet
  sourced from the VIP if it arrives from the backup right after a
  takeover. Set sticky source NAT on the upstream if observed.
