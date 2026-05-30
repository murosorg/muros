# Hardware

MurOS runs on anything that runs Debian 13. Below are realistic sizing
brackets, both for bare-metal and for VMs.

## Minimum

- **CPU**: 1 vCPU, x86_64 (amd64). No special instructions required.
- **RAM**: 1 GB. The full MurOS stack (backend + nginx + nftables + monitor) uses around 250 MB idle.
- **Disk**: 4 GB. Mostly Debian, MurOS itself is a 25 MB .deb.
- **NIC**: 1 interface for management. 2 or more for WAN / LAN segmentation.

A Raspberry Pi 4 or a small thin client (Wyse 3040, Fujitsu Futro S720) is
enough for a home lab or a small office under 50 Mbps.

## Sizing by throughput

These brackets assume stateful filtering + NAT + a couple of WireGuard or
IPsec tunnels. IDS / IPS once shipped will bump CPU requirements.

| Target | CPU | RAM | Notes |
| --- | --- | --- | --- |
| Home / 100 Mbps | 1 vCPU | 1 GB | RPi 4, thin client, small VM. |
| SMB / 500 Mbps | 2 vCPU | 2 GB | Intel N5105 / N100 mini-PC. |
| Office / 1 Gbps | 4 vCPU | 4 GB | i3 / Ryzen 3 with Intel i225 / i226 NICs. |
| Datacenter / 10 Gbps | 8+ vCPU | 8 GB+ | Xeon / EPYC, Intel X710 / Mellanox ConnectX-4, AES-NI for VPN. |

## Recommended boxes

Hardware that has been tested or that follows known-good designs:

- **Mini-PC fanless Intel N100 / N305 with 4x Intel i226-V**: the sweet spot for home and small office, ~150 euros, gigabit-class.
- **Protectli FW4B / VP2410 / VP4670**: turnkey Intel-NIC firewall boxes, well documented under Debian.
- **Generic 1U rack server with 2-4x Intel X710-DA**: when you need 10 Gbps and PCIe SR-IOV for HA pairs.
- **VMware / Proxmox / KVM VM**: virtio-net works out of the box, virtio-vsock is not required.

## Network cards

- **Intel i210 / i225 / i226 / X710**: first choice. Mature kernel driver, robust under load.
- **Mellanox ConnectX-4 / 5**: 10 / 25 / 40 Gbps, great offloads.
- **Realtek r8168 / r8125**: works, but proprietary driver quirks under heavy load. Avoid in production.

## High availability

Active / passive HA needs **two MurOS nodes** running the same package
version, with matching interface names and a dedicated link (or VLAN)
between them for VRRP advertisements and conntrack sync. Identical
hardware is recommended but not required: keepalived handles VIP failover
and conntrackd replays sessions on the slave so existing TCP connections
survive a takeover. Asymmetric pairs (different vendors, different NIC
counts) work as long as enabled interfaces line up on both sides. See the
[High availability](/docs/ha.html) page for the full setup.

## Power and form factor

A typical N100 mini-PC pulls 7-12 W under load, fits a DIN rail or a shelf,
runs silent. A 1U appliance with two Xeon and 25 GbE pulls 80-150 W. Pick
the form factor that matches your physical environment first, then size up
if needed.
