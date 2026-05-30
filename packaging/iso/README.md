# MurOS unattended installer ISO

`build-iso.sh` turns a stock Debian 13 (trixie) netinst image into a
fully unattended MurOS installer. Boot it and walk away: it partitions
the first disk (guided LVM), installs a minimal Debian base, then
registers `apt.muros.org` and installs the `muros` package. No installer
question is asked.

## Why

So you never have to click through the Debian installer and then run
`install.sh` by hand on every appliance. One image, repeatable installs.

## How it works

1. The answers live in `preseed.cfg` (locale, network via DHCP, root
   account, guided LVM partitioning, minimal package set).
2. `build-iso.sh` renders the preseed (crypting the root password),
   injects it into the installer `initrd.gz` so it is read before any
   prompt, patches the BIOS (isolinux) and UEFI (grub) boot menus to
   boot automatically, then repacks a hybrid bootable ISO with xorriso.
3. On first boot of the installed system, the preseed `late_command`
   runs the official `install.sh`, which registers the signed apt
   repository and pulls `muros` and its dependencies.

## Build

```sh
sudo apt-get install xorriso isolinux wget gzip cpio openssl
cd packaging/iso
sudo MUROS_ROOT_PASSWORD='choose-a-password' ./build-iso.sh
```

Output: `muros-installer-amd64.iso`.

### Options (environment variables)

| Variable | Default | Purpose |
| --- | --- | --- |
| `MUROS_ROOT_PASSWORD` | `root` | Root password of the installed system (AZERTY/QWERTY-safe default). Change it after first login. |
| `DEBIAN_VERSION` | latest in `current/` | netinst point release to download. Auto-detected unless pinned (e.g. `13.5.0`). |
| `DEBIAN_ARCH` | `amd64` | `amd64` or `arm64`. |
| `NETINST_ISO` | (unset) | Use a locally downloaded netinst ISO instead of downloading. |
| `OUTPUT` | `./muros-installer-<arch>.iso` | Output path. |

## Use the image

USB key:

```sh
sudo dd if=muros-installer-amd64.iso of=/dev/sdX bs=4M status=progress oflag=sync
```

or attach the ISO to a VM and boot it. The install needs network access
(DHCP + outbound HTTPS) to reach the Debian mirror and `apt.muros.org`.

## First login

When built with the defaults, the installed system logs in with:

- **Username:** `root`
- **Password:** `root`

The default password is deliberately `root`: it types identically on
AZERTY and QWERTY keyboards, so the first console login is never blocked
by a layout mismatch (unlike, say, `muros`). The console keymap can then
be switched with `loadkeys fr` (the `kbd` package is preinstalled) or set
persistently from the UI (System settings).

Change this password immediately after first login (UI: Access > Users,
or `passwd` at the console). If you built the ISO with a custom
`MUROS_ROOT_PASSWORD`, use that value instead.

## Notes

- The installer wipes the first disk (`auto=true priority=critical`).
  Only boot it on the target machine.
- The default `root` / `root` credentials are a convenience for lab use
  and a safe first console login. Set `MUROS_ROOT_PASSWORD` for anything
  else and rotate it from the UI (Access > Users) after install.
- MurOS uses the system `root` account for both the web UI and SSH, so
  the preseed creates no separate user.
