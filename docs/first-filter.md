# Use case: setting up your first filter

This tutorial shows how to configure MurOS from scratch for a typical use
case: a firewall protecting an internal LAN with Internet access and
restricted admin SSH.

## Topology

```
   [Internet]
       |
     eth0 (WAN, 203.0.113.10/24)
       |
   +---MurOS firewall---+
       |
     eth1 (LAN, 192.168.1.1/24)
       |
   [LAN hosts]
```

## Step 1: Interfaces

Go to **Network > Interfaces**, add:

* `eth0`: IP `203.0.113.10/24`, MTU 1500
* `eth1`: IP `192.168.1.1/24`, MTU 1500

## Step 2: Zones

Go to **Network > Zones**, add:

* `wan`: interface `eth0`
* `lan`: interface `eth1`

## Step 3: NAT for Internet outbound

Go to **Network > NAT**, add a SNAT (or masquerade) rule:

* Source: zone `lan`
* Destination: zone `wan`
* Action: Masquerade
* Egress interface: `eth0`

Click **Apply**.

## Step 4: Forward rule LAN to WAN

Go to **Filtering > Rules**, add:

* Chain: `forward`
* Source zone: `lan`
* Destination zone: `wan`
* Action: `accept`
* Description: "Allow LAN to reach the Internet"

Click **Apply**.

## Step 5: Block the other direction (WAN to LAN)

The default MurOS rule is already `drop` on the `forward` chain. Traffic
from the Internet to the LAN is therefore blocked by default.

## Step 6: Restricted admin SSH access

In **Filtering > Rules**, add in order:

1. Chain `input`, source IP `203.0.113.99/32`, port 22 tcp, action `accept`
2. Chain `input`, port 22 tcp, action `drop`

The first matching rule wins, so only SSH packets coming from the admin IP
are accepted.

## Step 7: Allow LAN to ping the firewall

* Chain `input`, source zone `lan`, protocol `icmp`, action `accept`

## Step 8: Restrict MurOS UI to LAN

The web UI always listens on every interface; you decide who can reach it
at the firewall, the same way you expose any other service. In
**Firewall > Rules**, make sure the `wan` zone has no rule allowing the UI
ports (`80`/`443`) to the firewall, and keep an explicit rule allowing the
`lan` zone to reach the firewall on those ports. The default ruleset
already permits `lan -> firewall`, so the UI is reachable from the LAN and
denied from the WAN out of the box.

Click **Apply** and confirm within the countdown after checking the UI is
still reachable. If you lose access, automatic rollback restores the
previous ruleset.

## Step 9: Backup before going to production

In **Backups**, click **Create a backup**.

## Next steps

* Add a `dmz` zone for exposed servers
* Configure a WireGuard VPN for roaming users
* Enable email notifications
* Enable SNMP for monitoring
* Configure high availability if you have a 2nd node
