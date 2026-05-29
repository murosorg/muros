# FAQ and troubleshooting

## The UI keeps showing "Service temporarily unavailable"

The MurOS backend is down or restarting. The page auto-refreshes every 10s.
Otherwise, SSH on the firewall:

```bash
sudo systemctl status muros-backend
sudo journalctl -u muros-backend -n 100 --no-pager
```

If the backend has a traceback, the journal shows the stack. Common cause
during dev: missing DB migration after a schema update. See also the
Migration section below.

## How to send notification emails?

MurOS sends mail directly via an **external SMTP smarthost** (typically
your operator's SMTP relay or an internal enterprise relay). No local
postfix is installed: the SMTP relay handles delivery.

In **Notifications > SMTP Configuration**:

* **SMTP server**: e.g. `smtp.company.com`
* **Port**: 587 (STARTTLS, default) or 465 (SMTPS) or 25 (clear)
* **Username / Password**: smarthost credentials (often required on 587/465)
* **TLS**: checked (recommended)
* **Sender**: e.g. `firewall@company.com`
* **Recipients**: comma-separated addresses

Test with the "Send a test email" button. History shows up at the bottom of
the page.

## I changed the SSH port but sshd is still on 22

Check the effective config:
```bash
sshd -T 2>&1 | grep -E "^port|^listenaddress"
ss -tlnp | grep ssh
```

If `sshd -T` returns the new port but `ss` shows nothing, do a full restart:
```bash
systemctl restart ssh
```

On Debian 12+, ssh may be started through socket activation (`ssh.socket`)
which listens on 22 by default. If you want sshd to listen exclusively on
your custom port, disable the socket:
```bash
systemctl disable --now ssh.socket
systemctl restart ssh
```


## I lost SSH or HTTPS access after a change

MurOS implements **automatic 60s rollback** on changes that may lock you
out of admin access:

| Action | Auto-rollback 60s |
|---|---|
| Apply firewall (nft) | YES |
| Apply HTTP nginx (listen + ports) | YES |
| Apply SSH (port + listen) | YES |
| Upload TLS cert or regen self-signed | YES |
| Interface change (IP, MTU) | NO (confirm() only) |
| Static route change | NO (confirm() only) |
| Apply WireGuard, IPsec, SNMP, HA | NO (low risk on local admin access) |

If you do not confirm in the modal that follows the apply, the previous
config is restored automatically by a backend thread that scans for
expired pending_apply records every 5 seconds.

If you were blocked in the meantime:
* Wait 60-90s, the previous config is restored
* Reconnect with the old parameters

**Special case interfaces/routes**: if you change the admin interface IP
or the default gateway and lose access, you need serial console / IPMI /
hypervisor access to revert manually. V1 plans auto-rollback on these too.

## I forgot the UI admin password

SSH on the firewall (as root), reset from Python:

```bash
sudo -i
cd /opt/muros/backend
source .venv/bin/activate
python -c "
from app.db import SessionLocal
from app import models
from app.auth import hash_password
with SessionLocal() as db:
    user = db.query(models.User).filter_by(username='admin').first()
    user.password_hash = hash_password('new_temporary_password_AAbb12!!')
    user.must_change_password = True
    db.commit()
    print('Password reset.')
"
```

The new password must satisfy the policy (12+ chars, complexity), otherwise
the mandatory change on the next login will refuse to accept it.

## The firewall does not forward LAN -> WAN traffic

Check in order:

1. **IP forwarding enabled**: `sysctl net.ipv4.ip_forward` must return `1`.
   Otherwise the MurOS drop-in `99-muros-hardening.conf` is not loaded.
2. **NAT rule present**: in Network > NAT, masquerade or SNAT rule
   `lan` -> `wan` egressing on the WAN interface.
3. **Forward rule**: in Filtering > Rules, `forward` `lan` -> `wan` rule
   with action accept.
4. **Apply done**: a pending "Apply" button means changes haven't been
   pushed yet.

From the firewall, test `ping -I eth0 8.8.8.8`. From a LAN host, test
`traceroute 8.8.8.8` to see where it stops.

## The WireGuard VPN does not come up

Check in Services (Dashboard) that `wg-quick@wg0` is active. Otherwise:

```bash
sudo systemctl status wg-quick@wg0
sudo wg show
sudo journalctl -u wg-quick@wg0 -n 50
```

Most common causes:
* UDP port 51820 (or other) blocked on the firewall or upstream
* Public/private key mismatch
* PSK mismatch
* AllowedIPs too restrictive on the peer

## Upgrading MurOS

While MurOS is in **beta** (`v0.9.0-rcXX` release candidate cycle), the DB
schema is not frozen. Upgrades between beta releases are done via clean
reinstall:

```bash
curl -fsSL https://github.com/murosorg/muros/releases/latest/download/uninstall.sh | sudo bash
curl -fsSL https://github.com/murosorg/muros/releases/latest/download/install.sh | sudo bash
```

If you want to keep your config across reinstalls, export a backup first
(Backups menu) and restore after reinstall.

Starting from the first stable release, in-place upgrades will be supported and the
schema will evolve via versioned migrations.

## How to change the Linux root account password?

Two ways:
* **Via the UI**: SSH Access > "Linux root account password" section
* **Via SSH**: `sudo passwd root`

The Linux root account password is **different** from the MurOS UI
password. The first is for password-based SSH login, the second is for
logging into the web interface.

## How to add an SSH key for root?

Via the UI: **SSH Access** > "SSH keys allowed for root" section > paste
the public key (`ssh-ed25519 AAAA... comment`) > Add.

The key is written to `/root/.ssh/authorized_keys` with correct perms.

## How to watch live backend requests?

From the firewall:

```bash
sudo journalctl -u muros-backend -f
```

Or see the UI action audit log in **Logs > UI actions**.

## How to export / import MurOS config from one firewall to another?

Use **backups**:

1. On source firewall: Backups > Create a backup > Download
2. On destination firewall: Backups > Restore > Upload the .tar.gz

The full DB config is transferred. Note: UI TLS certs and WireGuard keys
are **not** in the DB (they live on disk), you need to regenerate or copy
them separately.

## How to uninstall MurOS?

The `uninstall.sh` script removes:
* The `muros-*` systemd services
* The `/opt/muros` directory
* The `/var/lib/muros/` DB
* MurOS drop-ins (sysctl, sshd, journald, fail2ban, logrotate, snmpd)
* The `muros` nginx site

It does not touch installed packages (postfix, wireguard, strongswan,
keepalived, conntrackd, snmpd). Uninstall those manually if desired.

The nftables configuration is reloaded to its pre-MurOS state (but for
safety, reboot the machine after uninstall).
