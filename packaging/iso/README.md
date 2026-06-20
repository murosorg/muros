# MurOS installer ISO

`build-iso.sh` turns the Debian 13 (trixie) LIVE standard image (the
text-only, no-desktop variant) into a single, always-offline MurOS
installer. One script does everything: there is no separate preparation
step and no online variant.

## Boot menu

- **Install MurOS** (default, starts after a 30s timeout): partitions the
  first disk (guided LVM), copies the base system from the live squashfs,
  and installs the `muros` package from the on-disc pool. Everything is
  automated EXCEPT the network step: the installer asks you to pick the
  LAN interface and to type its STATIC IP (a firewall LAN is never handed
  out by DHCP). Gateway and DNS are not asked for (a firewall LAN has
  none), so you only enter the interface, its IP and its netmask. This
  address is what MurOS adopts as its LAN, set once here, not in the UI.
- **Install / Graphical install** (stock Debian entries): a plain
  interactive Debian install, untouched.

## Why the live image and why offline

The live standard image ships a complete Debian system inside its
squashfs, so the installer copies that filesystem to disk instead of
debootstrapping from a network mirror: the base OS needs no network.
MurOS and all its dependencies are carried on the ISO in a local package
pool plus a Python wheelhouse, so the MurOS install needs no network
either. Net result: a fully offline install.

## Build

Run on any Linux host with network access (used only at build time). The
host distribution does not matter: the target Debian 13 packages and
Python wheels are collected from the live image's own filesystem. A
single command does everything:

```sh
cd packaging/iso
sudo apt-get install xorriso isolinux wget gzip cpio openssl squashfs-tools dpkg-dev
sudo MUROS_ROOT_PASSWORD='choose-a-password' ./build-iso.sh
```

You can provide the Debian live ISO yourself to skip the download and
build on top of it:

```sh
sudo LIVE_ISO=/path/debian-live-13.5.0-amd64-standard.iso \
     MUROS_ROOT_PASSWORD='choose-a-password' ./build-iso.sh
```

What it does:

1. Obtains the live standard image (downloaded, or your `LIVE_ISO`) and
   extracts it.
2. Prepares the offline bundle by reusing the live image's OWN trixie
   filesystem (its squashfs) as the environment: it registers the MurOS
   apt repo (`stable main`) there,
   downloads `muros` and the dependencies missing from that base into
   `./offline/pool`, and builds the Python wheelhouse with the image's
   Python 3.13 into `./offline/wheelhouse`. No second base system is
   downloaded and the dependency set is the exact delta the target needs.
   (Falls back to a debootstrap trixie chroot if the squashfs cannot be
   read.) By default the bundle is rebuilt from scratch on every run, so
   an ISO never ships a stale cached `muros` .deb (which changes every
   release). Set `MUROS_REUSE_BUNDLE=1` to reuse a cached bundle for
   speed; even then the `muros` .deb is refreshed from the repo unless you
   also set `MUROS_KEEP_CACHED_DEB=1` (fully offline, may be stale).
3. Bakes the MurOS preseed into a dedicated installer initrd, embeds the
   bundle, and repacks a BIOS+UEFI bootable ISO. Output:
   `muros-installer-amd64.iso`.

The on-disc wheelhouse is copied to `/opt/muros/wheelhouse`, which the
package `postinst` uses to install the Python dependencies with
`--no-index` (no PyPI).

### Options (environment variables)

| Variable | Default | Purpose |
| --- | --- | --- |
| `MUROS_ROOT_PASSWORD` | `root` | Root password of the installed system (AZERTY/QWERTY-safe default). Change it after first login. |
| `MUROS_OFFLINE_DIR` | `./offline` | Where the offline bundle lives / is built. |
| `MUROS_REUSE_BUNDLE` | `0` | Set to `1` to reuse a cached offline bundle for speed. Default rebuilds it from scratch every run so an ISO never ships a stale muros .deb. |
| `MUROS_KEEP_CACHED_DEB` | `0` | With `MUROS_REUSE_BUNDLE=1`, set to `1` to also reuse the cached muros .deb instead of refreshing it (fully offline; may ship an old muros package). |
| `MUROS_APT_URL` | `https://download.muros.org` | MurOS apt repo used to fetch the package. |
| `DEBIAN_VERSION` | latest in `current-live/` | Live point release to download. Auto-detected unless pinned (e.g. `13.5.0`). |
| `DEBIAN_ARCH` | `amd64` | `amd64` or `arm64`. |
| `LIVE_ISO` | (unset) | Use a locally downloaded live standard ISO instead of downloading. |
| `OUTPUT` | `./muros-installer-<arch>.iso` | Output path. |

## Publish

`build-iso.sh` produces only the `.iso`. The website links to three files
served from the apt host under `/iso/`: the ISO, its `.sha256`, and a
detached GPG signature `.asc`. `publish-iso.sh` does the rest in one
command:

```sh
cd packaging/iso
./publish-iso.sh
```

It computes and verifies the checksum locally, removes any stale remote
ISO, uploads the ISO and checksum, then signs the ISO ON THE SERVER with
the MurOS repository key (the private key lives there, the same one that
signs the apt `Release`, so users get a single chain of trust). The
upload is a plain replace, never an append, so a previous ISO of a
different release is fully overwritten instead of producing a hybrid file
with a broken checksum. All targets are overridable: `MUROS_ISO`,
`MUROS_APT_HOST` (default `root@download.muros.org`), `MUROS_APT_ISO_DIR`
(default `/opt/muros/download/iso`), `MUROS_GPG_KEY`, `MUROS_APT_URL`.

## Use the image

USB key:

```sh
sudo dd if=muros-installer-amd64.iso of=/dev/sdX bs=4M status=progress oflag=sync
```

or attach the ISO to a VM and boot it.

## First login

With the defaults, the installed system logs in with:

- **Username:** `root`
- **Password:** `root`

The default password is deliberately `root`: it types identically on
AZERTY and QWERTY keyboards, so the first console login is never blocked
by a layout mismatch. The console keymap can be switched with
`loadkeys fr` (the `kbd` package is preinstalled) or set persistently
from the UI (System settings).

Reach the web UI at `https://<the-LAN-IP-you-set-during-install>:8443`.
Change the root password immediately after first login (UI: Access >
Users, or `passwd` at the console). If you built the ISO with a custom
`MUROS_ROOT_PASSWORD`, use that value instead.

## Notes

- The default entry wipes the first disk. Only boot it on the target
  machine, or pick a stock Debian entry to control partitioning.
- The default `root` / `root` credentials are a convenience for lab use
  and a safe first console login. Set `MUROS_ROOT_PASSWORD` for anything
  else and rotate it from the UI (Access > Users) after install.
- MurOS uses the system `root` account for both the web UI and SSH, so
  the preseed creates no separate user.
