# MurOS concepts

This document explains the key concepts you will encounter in the UI.

## Interfaces

An **interface** represents a network card on the firewall: `eth0`, `eth1`, a
VLAN on `eth0` (e.g. `eth0.10`), a bridge, a WireGuard tunnel `wg0`, etc.
In MurOS, each interface has:

* A name (the Linux name: `eth0`, `eth0.10`)
* One or more IP addresses (with CIDR mask: `192.168.1.1/24`)
* Optionally a custom MTU
* An assigned zone (see below)

Interfaces are configured in **Network > Interfaces**.

## Zones

A **zone** is a logical grouping of interfaces that share the same trust
level. It is the core filtering concept of MurOS, inspired by pfSense /
OPNsense / Shorewall.

Typical examples:

* `wan`: internet-facing interface(s), untrusted
* `lan`: internal network, trusted
* `dmz`: exposed servers
* `mgmt`: admin network
* `vpn`: VPN tunnel interfaces

Filter rules reference zones (not interfaces directly). That way, if you add
a 2nd interface to the LAN tomorrow, the rules keep working.

Zones are defined in **Network > Zones**.

## Filter rules

A **rule** decides the fate of packets matching its criteria:
* `accept`: let through
* `drop`: silently discard (sender does not know)
* `reject`: send back an ICMP "unreachable" (sender is informed)

Possible criteria:
* **Chain**: `input` (packets to the firewall itself), `forward` (packets
  traversing two interfaces), `output` (packets emitted by the firewall)
* **Source zone** and **destination zone**
* **Protocol** (tcp, udp, icmp, sctp)
* **Destination port** (or range)
* **Source** and/or **destination address** (IP or CIDR)
* **Rate-limit** (e.g. `100/s burst 20`)
* **Log**: if enabled, matched packets are written to journalctl

Rules are ordered: the first one that matches takes the decision. A default
`drop` rule is appended at the end by MurOS.

Rules are managed in **Filtering > Rules**. Click **Apply** after editing to
push into nftables.

## NAT (Network Address Translation)

**NAT** lets you modify packet addresses:

* **SNAT / Masquerade**: replaces the source IP. Typical use: LAN hosts go
  out to the Internet with the firewall's public IP.
* **DNAT**: replaces the destination IP. Use case: expose an internal web
  server by redirecting traffic arriving on port 443 of the public IP.

Managed in **Network > NAT**.

## VPN

Two technologies available:

### WireGuard

Modern, simple, fast VPN protocol. Configured in **VPN > WireGuard**:
* An "interface" wg0 with IP, listen port, private key
* "Peers" (clients or other firewalls) with their public key, allowed IPs,
  optionally an endpoint and a keepalive

Best for: roaming users (laptop, phone), simple site-to-site, performance.

### IPsec (StrongSwan)

Standard protocol interoperable with everything (Cisco, Fortinet, etc.).
Configured in **VPN > IPsec**:
* Connections with local/remote address, IKE and ESP proposals
* Auth via PSK (pre-shared key) or X.509 certificate
* Integrated PKI: MurOS can generate the CA, peer certificates and the CRL

Best for: interoperability, client contracts that mandate IPsec.

## High Availability (HA)

MurOS supports an **active-passive 2-node** configuration:
* **keepalived** handles VIP failover via VRRP
* **conntrackd** synchronizes connection state between the 2 nodes
  (established connections survive the failover)
* MurOS synchronizes its own configuration DB between the 2 nodes (the
  BACKUP receives all changes made on the MASTER)

A node is either **MASTER** or **BACKUP** at a given moment. The MASTER
holds the VIPs, the BACKUP waits silently. If the MASTER goes down, the
BACKUP takes over the VIP and keeps serving traffic.

Managed in **HA**.

## Audit log

All UI-driven changes (rule creation, peer deletion, config apply...) are
logged in **Logs > UI actions**. This lets you trace who did what and when.

Read operations (GET) are not logged to avoid noise (otherwise UI polling
would saturate the table).

## Backups

MurOS backs up its entire **SQLite DB** (which contains the whole config:
rules, zones, peers, etc.). Backups can be:
* Local in `/var/lib/muros/backups/`
* Remote via rclone, FTP, or SSH (depending on what is configured)

A backup is created automatically before every HA sync (so you can roll
back if the sync breaks something).

Managed in **Backups**.
