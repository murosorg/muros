# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""WireGuard : tunnels VPN site-a-site et road-warrior.

MurOS s'appuie sur deux paquets Debian : wireguard (module noyau + wg-quick)
et wireguard-tools (commande `wg`).

Approche : une seule interface WireGuard `wg0` par defaut, geree via un
fichier `/etc/wireguard/wg0.conf` rendu depuis la DB SQLite. Activation
au boot via `wg-quick@wg0.service`.
"""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

from app.apply import APPLY_ENABLED

log = logging.getLogger("muros.wireguard")

WG_PACKAGES = ["wireguard", "wireguard-tools"]
WG_DIR = Path("/etc/wireguard")


from app.service_state import is_active as _systemd_active, which as _which  # noqa: E402


def _list_wg_interfaces() -> list[dict]:
    """Retourne les interfaces WireGuard actives via `wg show interfaces`."""
    if not _which("wg"):
        return []
    try:
        out = subprocess.check_output(["wg", "show", "interfaces"], text=True, timeout=3)
    except (subprocess.SubprocessError, FileNotFoundError):
        return []
    ifaces = []
    for name in out.split():
        info = {"name": name, "peers": 0, "listen_port": None}
        try:
            details = subprocess.check_output(
                ["wg", "show", name, "dump"], text=True, timeout=3,
            )
            lines = details.strip().splitlines()
            # Premiere ligne = interface, suivantes = peers.
            if lines:
                first = lines[0].split("\t")
                if len(first) >= 3:
                    info["listen_port"] = _to_int(first[2])
                info["peers"] = max(0, len(lines) - 1)
        except (subprocess.SubprocessError, FileNotFoundError):
            pass
        ifaces.append(info)
    return ifaces


def _to_int(s: str) -> int | None:
    try:
        return int(s.strip())
    except (ValueError, TypeError):
        return None


def _wg_version() -> str | None:
    """WireGuard version via dpkg (wireguard-tools package)."""
    from app.service_state import pkg_version
    return pkg_version("wireguard-tools", "WireGuard")


def get_status() -> dict:
    """Etat live WireGuard : paquets, version, service, interfaces actives."""
    from app.service_state import service_state as _state
    installed = _which("wg") and _which("wg-quick")
    interfaces = _list_wg_interfaces()
    service_active = _systemd_active("wg-quick@wg0.service")
    return {
        "installed": installed,
        "version": _wg_version(),
        "interfaces": interfaces,
        "service_active": service_active,
        "service_state": _state("wg-quick@wg0.service"),
    }


def install_packages() -> dict:
    """Installe wireguard + wireguard-tools via apt.

    Idempotente : verifie d'abord la presence des binaires.
    """
    already = _which("wg") and _which("wg-quick")
    if already:
        return {
            "installed": True,
            "already_present": WG_PACKAGES,
            "newly_installed": [],
            "output_tail": "",
        }

    if not APPLY_ENABLED:
        return {
            "installed": False,
            "already_present": [],
            "newly_installed": [],
            "output_tail": (
                f"dry-run : aurait execute 'apt-get install -y {' '.join(WG_PACKAGES)}' "
                "(MUROS_APPLY off)."
            ),
        }

    if os.geteuid() != 0:
        raise RuntimeError(
            "Installation impossible: MurOS must run as root. "
            f"Installer manuellement : apt install -y {' '.join(WG_PACKAGES)}"
        )

    try:
        subprocess.check_call(["which", "apt-get"], stdout=subprocess.DEVNULL, timeout=2)
    except (subprocess.SubprocessError, FileNotFoundError) as exc:
        raise RuntimeError(
            "apt-get not found, only supported on Debian/Ubuntu."
        ) from exc

    env = {**os.environ, "DEBIAN_FRONTEND": "noninteractive", "LC_ALL": "C"}
    proc_update = subprocess.run(
        ["apt-get", "update", "-q"],
        env=env, capture_output=True, text=True, timeout=120,
    )
    if proc_update.returncode != 0:
        raise RuntimeError(
            f"apt-get update failed: {(proc_update.stderr or '').strip()[:400]}"
        )

    proc = subprocess.run(
        ["apt-get", "install", "-y", "--no-install-recommends", *WG_PACKAGES],
        env=env, capture_output=True, text=True, timeout=600,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"apt-get install failed (code {proc.returncode}): "
            f"{(proc.stderr or '').strip()[:400]}"
        )

    if not (_which("wg") and _which("wg-quick")):
        raise RuntimeError(
            f"Binaries missing after install: wg/wg-quick. Output: {proc.stdout[-400:]}"
        )

    return {
        "installed": True,
        "already_present": [],
        "newly_installed": WG_PACKAGES,
        "output_tail": proc.stdout[-800:],
    }


# --- Generation de cles ---

def _curve25519_keys() -> tuple[str, str]:
    """Genere une paire de cles X25519 en Python pur (lib cryptography).

    Retourne (private_key_b64, public_key_b64) au format WireGuard.
    Cle privee doit etre fixee a 32 octets, avec les 3 bits forces selon
    la spec X25519 (Curve25519 cofactor clamping).
    """
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    import base64
    sk = X25519PrivateKey.generate()
    sk_bytes = sk.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pk_bytes = sk.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return (
        base64.standard_b64encode(sk_bytes).decode("ascii"),
        base64.standard_b64encode(pk_bytes).decode("ascii"),
    )


def _pubkey_from_priv(priv_b64: str) -> str:
    """Recompute the public key from an X25519 private key."""
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    import base64
    sk_bytes = base64.standard_b64decode(priv_b64)
    if len(sk_bytes) != 32:
        raise ValueError("invalid private key: 32 bytes expected")
    sk = X25519PrivateKey.from_private_bytes(sk_bytes)
    pk_bytes = sk.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return base64.standard_b64encode(pk_bytes).decode("ascii")


def generate_keypair() -> dict:
    """API: generate a new WG key pair."""
    priv, pub = _curve25519_keys()
    return {"private_key": priv, "public_key": pub}


def generate_psk() -> str:
    """Genere une PSK WireGuard (32 octets base64)."""
    import secrets
    import base64
    return base64.standard_b64encode(secrets.token_bytes(32)).decode("ascii")


# --- Config file rendering ---

def render_config(cfg, peers: list) -> str:
    """Rend le contenu de /etc/wireguard/<iface>.conf.

    cfg : WireGuardConfig (singleton)
    peers : liste de WireGuardPeer (seuls les enabled sont inclus)
    """
    if not cfg.private_key or not cfg.address_cidr:
        raise ValueError(
            "Incomplete config: private key and CIDR address required."
        )

    lines: list[str] = [
        "# Genere par MurOS - ne pas editer a la main.",
        "",
        "[Interface]",
        f"PrivateKey = {cfg.private_key}",
        f"Address = {cfg.address_cidr}",
        f"ListenPort = {cfg.listen_port}",
    ]
    if cfg.mtu:
        lines.append(f"MTU = {cfg.mtu}")

    for peer in peers:
        if not peer.enabled:
            continue
        lines.append("")
        lines.append(f"# {peer.name}" + (f" : {peer.description}" if peer.description else ""))
        lines.append("[Peer]")
        lines.append(f"PublicKey = {peer.public_key}")
        if peer.preshared_key:
            lines.append(f"PresharedKey = {peer.preshared_key}")
        lines.append(f"AllowedIPs = {peer.allowed_ips}")
        if peer.endpoint:
            lines.append(f"Endpoint = {peer.endpoint}")
        if peer.persistent_keepalive and peer.persistent_keepalive > 0:
            lines.append(f"PersistentKeepalive = {peer.persistent_keepalive}")

    return "\n".join(lines) + "\n"


# --- Apply sur le systeme ---

class WireGuardApplyError(Exception):
    """Raised when wg-quick / wg syncconf refuses the rendered configuration.

    Caught by the Apply route and surfaced as a 409, so a broken peer
    definition (overlapping AllowedIPs, malformed key, invalid
    endpoint) does not leave the kernel partially applied with a
    phantom-clear dirty flag.
    """


def write_conf(cfg, peers: list) -> dict:
    """Render and persist /etc/wireguard/<iface>.conf only. No netlink.

    Used by the Save path : the new config is materialised so it
    survives a reboot via muros-boot, but the live wg interface keeps
    its previous config until the operator clicks Apply.
    """
    iface = cfg.interface_name or "wg0"
    conf_path = WG_DIR / f"{iface}.conf"
    if not cfg.enabled:
        if APPLY_ENABLED and conf_path.exists():
            conf_path.unlink()
        return {"message": f"WireGuard {iface} configuration saved (disabled).", "interface": iface}

    text = render_config(cfg, peers)
    if not APPLY_ENABLED:
        return {
            "message": f"dry-run : aurait ecrit {conf_path} ({len(text)} octets).",
            "interface": iface,
            "config_preview": text,
        }
    WG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(WG_DIR, 0o700)
    except OSError:
        pass
    conf_path.write_text(text, encoding="utf-8")
    os.chmod(conf_path, 0o600)
    return {"message": f"WireGuard {iface} configuration saved.", "interface": iface}


def reload(cfg, peers: list) -> dict:
    """Reload the wg-quick interface to pick up the on-disk config.

    Called only by the explicit Apply action ; assumes write_conf has
    already been run. Thin wrapper around apply_config (which is
    idempotent on the file write) until we extract the systemctl /
    wg syncconf path into its own helper.
    """
    return apply_config(cfg, peers)


def apply_config(cfg, peers: list, *, defer_start: bool = False) -> dict:
    """Ecrit /etc/wireguard/<iface>.conf et reconfigure l'interface.

    Si l'interface n'existe pas et que cfg.enabled : `wg-quick up <iface>`.
    Si elle existe et que cfg.enabled : reload a chaud via `wg syncconf`.
    Si !cfg.enabled : `wg-quick down <iface>` + on retire le fichier conf.

    En dry-run (MUROS_APPLY=false) : log seulement.

    defer_start: utilise `wg-quick up <iface>` directement plutot que
    `systemctl enable --now`, ce qui evite un deadlock quand on est
    appele depuis muros-boot.service (Before=network-online.target),
    car wg-quick@.service a After=network-online.target. La persistance
    au reboot est assuree par `systemctl enable` (sans --now).
    """
    iface = cfg.interface_name or "wg0"
    conf_path = WG_DIR / f"{iface}.conf"

    if not cfg.enabled:
        # Stop + remove conf.
        if APPLY_ENABLED:
            subprocess.run(
                ["systemctl", "disable", "--now", f"wg-quick@{iface}.service"],
                capture_output=True, text=True, timeout=15,
            )
            if conf_path.exists():
                conf_path.unlink()
        return {"message": f"WireGuard {iface} desactive.", "interface": iface}

    # Generation conf.
    text = render_config(cfg, peers)

    if not APPLY_ENABLED:
        return {
            "message": f"dry-run : aurait ecrit {conf_path} ({len(text)} octets) et active wg-quick@{iface}.",
            "interface": iface,
            "config_preview": text,
        }

    WG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(WG_DIR, 0o700)
    except OSError:
        pass
    conf_path.write_text(text, encoding="utf-8")
    os.chmod(conf_path, 0o600)

    # Activation: if the interface already exists, do a hot reload,
    # otherwise bring it up with wg-quick up.
    iface_exists = subprocess.run(
        ["ip", "link", "show", iface], capture_output=True, timeout=5,
    ).returncode == 0

    if iface_exists:
        # Hot reload without dropping active sessions. `wg syncconf`
        # only reads a subset of the config (not Address/MTU) but that is
        # what we need to avoid killing existing tunnels.
        strip = subprocess.run(
            ["wg-quick", "strip", iface], capture_output=True, text=True, timeout=5,
        )
        if strip.returncode != 0:
            raise RuntimeError(f"wg-quick strip failed: {strip.stderr.strip()}")
        sync = subprocess.run(
            ["wg", "syncconf", iface, "/dev/stdin"],
            input=strip.stdout, capture_output=True, text=True, timeout=10,
        )
        if sync.returncode != 0:
            raise RuntimeError(f"wg syncconf failed: {sync.stderr.strip()}")
        # Persist across reboot even in the reload branch: the iface may have
        # been brought up manually (wg-quick up) without being enabled.
        subprocess.run(
            ["systemctl", "enable", f"wg-quick@{iface}.service"],
            capture_output=True, text=True, timeout=5,
        )
        msg = f"WireGuard {iface}: configuration reloaded (hot reload)."
    else:
        # Persist across reboot: enable (without --now) does not touch the
        # running service and depends on no target, so it is safe in boot
        # context.
        subprocess.run(
            ["systemctl", "enable", f"wg-quick@{iface}.service"],
            capture_output=True, text=True, timeout=5,
        )
        if defer_start:
            # Boot context: enqueue the start with --no-block (so systemctl
            # returns immediately, no deadlock with network-online.target
            # waiting for muros-boot to finish) and let systemd run
            # wg-quick up after muros-boot.
            # We do NOT run `wg-quick up` manually here: it would create the
            # iface right away, then systemd would retry the same command
            # when starting the unit and fail with
            # "wg-quick: '<iface>' already exists".
            up = subprocess.run(
                ["systemctl", "--no-block", "start",
                 f"wg-quick@{iface}.service"],
                capture_output=True, text=True, timeout=5,
            )
            if up.returncode != 0:
                raise RuntimeError(
                    f"systemctl --no-block start wg-quick@{iface} "
                    f"failed: {(up.stderr or up.stdout).strip()[:400]}"
                )
        else:
            up = subprocess.run(
                ["systemctl", "start", f"wg-quick@{iface}.service"],
                capture_output=True, text=True, timeout=15,
            )
            if up.returncode != 0:
                raise RuntimeError(
                    f"systemctl start wg-quick@{iface} failed: "
                    f"{(up.stderr or up.stdout).strip()[:400]}"
                )
        msg = (
            f"WireGuard {iface}: startup delegated to systemd (boot)."
            if defer_start
            else f"WireGuard {iface}: interface brought up."
        )

    return {"message": msg, "interface": iface}


# --- Export config for a peer (client side) ---

def render_peer_client_config(cfg, peer, peer_private_key: str | None = None) -> str:
    """Render the CLIENT-side config file for this peer.

    To hand to the road-warrior client. If peer_private_key is provided
    (case of a key generated on the fly from the UI), it is included in the
    [Interface] section. Otherwise a placeholder to fill in is used.
    """
    pk = peer_private_key or "<PASTE THE PEER PRIVATE KEY HERE>"
    lines = [
        f"# WireGuard config for {peer.name}",
        "# Generated by MurOS",
        "",
        "[Interface]",
        f"PrivateKey = {pk}",
        # On the server side AllowedIPs lists the networks reachable BY the
        # peer, but on the client side it is Address (the IP assigned to the
        # peer in the tunnel). We take the first /32 or /128 AllowedIP as the
        # client address.
        f"Address = {_extract_client_address(peer.allowed_ips)}",
        # MTU 1280 is the IPv6 minimum, safe over cellular networks and
        # any underlying path. Without it browsers stall on large
        # responses (image, JS bundle, etc.) because the default 1420
        # gets dropped on some carriers.
        "MTU = 1280",
        "",
        "[Peer]",
        f"PublicKey = {cfg.public_key}",
    ]
    if peer.preshared_key:
        lines.append(f"PresharedKey = {peer.preshared_key}")
    # Client-side AllowedIPs: networks the client will route into the tunnel.
    # Empty peer.client_allowed_ips field -> default full tunnel 0.0.0.0/0,::/0.
    # The admin can customize it for a split tunnel (e.g. 10.10.0.0/24,
    # 192.168.1.0/24) from the peer UI.
    client_routes = (getattr(peer, "client_allowed_ips", "") or "").strip() \
        or "0.0.0.0/0, ::/0"
    lines.append(f"AllowedIPs = {client_routes}")
    # The client-side endpoint must point at the server.
    # We do not know the server's public IP here, so we use a placeholder.
    endpoint_host = (getattr(cfg, "public_endpoint", "") or "").strip() or "<FIREWALL-PUBLIC-IP>"
    lines.append(f"Endpoint = {endpoint_host}:{cfg.listen_port}")
    if peer.persistent_keepalive and peer.persistent_keepalive > 0:
        lines.append(f"PersistentKeepalive = {peer.persistent_keepalive}")
    return "\n".join(lines) + "\n"


def _extract_client_address(allowed_ips: str) -> str:
    """Extract the first /32 (or /128) IP from allowed_ips as the client address."""
    for part in allowed_ips.split(","):
        part = part.strip()
        if part.endswith("/32") or part.endswith("/128"):
            return part
        if "/" in part:
            return part
    return "10.10.0.2/32"


def render_peer_qr_svg(config_text: str) -> str:
    """Generate an SVG QR code (no PIL dependency) of the config file."""
    try:
        import qrcode
        import qrcode.image.svg
        factory = qrcode.image.svg.SvgPathImage
        img = qrcode.make(config_text, image_factory=factory, box_size=10, border=2)
        from io import BytesIO
        buf = BytesIO()
        img.save(buf)
        return buf.getvalue().decode("utf-8")
    except ImportError:
        raise RuntimeError(
            "Le module Python 'qrcode' n'est pas installe. Ajoutez 'qrcode' a"
            " requirements.txt et relancez l'install pour les QR codes."
        )


# --- Sensible defaults / quick provisioning ---
#
# Goal: the operator only types a peer name. Everything else (server
# keys, tunnel subnet, peer keys, peer IP, PSK, full-tunnel routing,
# NAT/forward rules) is set up automatically and works on first apply.

DEFAULT_TUNNEL_CIDR = "10.10.0.1/24"
DEFAULT_LISTEN_PORT = 51820
DEFAULT_KEEPALIVE = 25  # seconds, recommended when peer is behind NAT


def ensure_initialized(cfg) -> bool:
    """Populate empty WireGuardConfig fields with sane defaults.

    Returns True if any field was changed (caller must commit).
    """
    changed = False
    if not cfg.private_key:
        priv, pub = _curve25519_keys()
        cfg.private_key = priv
        cfg.public_key = pub
        changed = True
    elif not cfg.public_key:
        try:
            cfg.public_key = _pubkey_from_priv(cfg.private_key)
            changed = True
        except (ValueError, Exception):  # noqa: BLE001
            pass
    if not cfg.address_cidr:
        cfg.address_cidr = DEFAULT_TUNNEL_CIDR
        changed = True
    if not cfg.listen_port:
        cfg.listen_port = DEFAULT_LISTEN_PORT
        changed = True
    if not cfg.interface_name:
        cfg.interface_name = "wg0"
        changed = True
    return changed


def _tunnel_network(cfg):
    """Return the ipaddress.IPv4Network describing the WG tunnel subnet.

    Falls back to 10.10.0.0/24 if address_cidr is unset or invalid.
    """
    import ipaddress
    raw = (cfg.address_cidr or DEFAULT_TUNNEL_CIDR).strip()
    try:
        return ipaddress.ip_interface(raw).network
    except (ValueError, TypeError):
        return ipaddress.ip_network("10.10.0.0/24")


def _used_peer_ips(peers) -> set[str]:
    """Collect every IPv4 address (as string) already claimed by peers."""
    import ipaddress
    used: set[str] = set()
    for p in peers:
        for part in (p.allowed_ips or "").split(","):
            part = part.strip()
            if not part:
                continue
            try:
                ip = ipaddress.ip_interface(part).ip
                used.add(str(ip))
            except (ValueError, TypeError):
                continue
    return used


def next_free_peer_ip(cfg, peers) -> str:
    """Pick the next free /32 in the tunnel subnet, skipping the server IP.

    Returns the address in CIDR /32 form ready to drop into AllowedIPs.
    """
    import ipaddress
    network = _tunnel_network(cfg)
    server_ip = None
    try:
        server_ip = str(ipaddress.ip_interface(cfg.address_cidr).ip)
    except (ValueError, TypeError):
        pass
    used = _used_peer_ips(peers)
    if server_ip:
        used.add(server_ip)
    for host in network.hosts():
        candidate = str(host)
        if candidate not in used:
            return f"{candidate}/32"
    raise RuntimeError(
        f"No free address left in tunnel subnet {network}. "
        "Enlarge the tunnel CIDR or remove unused peers."
    )

