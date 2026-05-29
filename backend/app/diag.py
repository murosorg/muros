# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Outils de diagnostic reseau exposes via l'UI.

Wrappers safe autour de ping / traceroute / dig / tcpdump avec validation
stricte des entrees (anti-injection) et timeout court.
"""
from __future__ import annotations

import ipaddress
import re
import subprocess

# Hostnames RFC 952/1123 + IP v4/v6 + caracteres pour FQDN.
_HOSTNAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
# Interfaces : alphanum + . - @ : (vlan, bridges...)
_INTERFACE_RE = re.compile(r"^[A-Za-z0-9.\-_@:]+$")

MAX_OUTPUT_BYTES = 50_000


def _validate_target(target: str) -> str:
    """Verifie que target est une IP ou un hostname valide. Anti-injection."""
    if not target or len(target) > 253:
        raise ValueError("Empty target or too long.")
    # Test IP d'abord
    try:
        ipaddress.ip_address(target)
        return target
    except ValueError:
        pass
    # Sinon hostname
    if not _HOSTNAME_RE.match(target):
        raise ValueError("Target must be an IP or domain name (letters, digits, dots, dashes).")
    return target


def _validate_interface(iface: str) -> str:
    if not iface or len(iface) > 32 or not _INTERFACE_RE.match(iface):
        raise ValueError(f"Invalid interface name : {iface!r}")
    return iface


from app.service_state import which as _which  # noqa: E402


def _run(cmd: list[str], timeout: int = 15) -> dict:
    """Lance une commande shell, retourne stdout + stderr + returncode + duration."""
    import time
    started = time.time()
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
        )
        stdout = proc.stdout[-MAX_OUTPUT_BYTES:]
        stderr = proc.stderr[-MAX_OUTPUT_BYTES:]
        return {
            "returncode": proc.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "command": " ".join(cmd),
            "duration_ms": int((time.time() - started) * 1000),
            "timed_out": False,
        }
    except subprocess.TimeoutExpired:
        return {
            "returncode": -1,
            "stdout": "",
            "stderr": f"Timeout apres {timeout}s.",
            "command": " ".join(cmd),
            "duration_ms": timeout * 1000,
            "timed_out": True,
        }
    except FileNotFoundError:
        return {
            "returncode": -1,
            "stdout": "",
            "stderr": f"Command not found : {cmd[0]}",
            "command": " ".join(cmd),
            "duration_ms": 0,
            "timed_out": False,
        }


# --- Ping ---

def ping(target: str, count: int = 4) -> dict:
    target = _validate_target(target)
    count = max(1, min(count, 20))
    return _run(["ping", "-c", str(count), "-W", "3", target], timeout=30)


# --- Traceroute ---

def traceroute(target: str, max_hops: int = 20) -> dict:
    target = _validate_target(target)
    max_hops = max(1, min(max_hops, 30))
    if _which("traceroute"):
        return _run(["traceroute", "-n", "-q", "1", "-w", "2", "-m", str(max_hops), target], timeout=60)
    if _which("tracepath"):
        return _run(["tracepath", "-n", "-m", str(max_hops), target], timeout=60)
    return {
        "returncode": -1, "stdout": "", "command": "",
        "stderr": "traceroute et tracepath sont absents (apt install traceroute).",
        "duration_ms": 0, "timed_out": False,
    }


# --- DNS lookup ---

DNS_TYPES = {"A", "AAAA", "CNAME", "MX", "NS", "PTR", "SOA", "SRV", "TXT", "CAA", "ANY"}


def dns_lookup(target: str, record_type: str = "A", resolver: str | None = None) -> dict:
    target = _validate_target(target)
    rt = record_type.upper().strip()
    if rt not in DNS_TYPES:
        raise ValueError(f"Invalid DNS type : {record_type}. Allowed : {sorted(DNS_TYPES)}")
    cmd = ["dig", "+short", "+timeout=3", "+tries=1"]
    if resolver:
        resolver = _validate_target(resolver)
        cmd.append(f"@{resolver}")
    cmd.extend([rt, target])
    if not _which("dig"):
        # Fallback host
        if _which("host"):
            return _run(["host", "-t", rt, target], timeout=15)
        return {
            "returncode": -1, "stdout": "", "command": "",
            "stderr": "dig et host sont absents (apt install dnsutils).",
            "duration_ms": 0, "timed_out": False,
        }
    return _run(cmd, timeout=15)


# --- Port TCP/UDP test (nc) ---

def port_test(target: str, port: int, protocol: str = "tcp", timeout: int = 5) -> dict:
    """Teste si un port distant est ouvert, via nc (netcat-openbsd).

    protocol : 'tcp' (defaut, nc -z) ou 'udp' (nc -zu, moins fiable car
    UDP n'a pas de handshake : on ne sait que si le port est ferme par ICMP
    Port Unreachable; sinon ca dit 'open' meme si rien n'ecoute).
    """
    target = _validate_target(target)
    if not 1 <= port <= 65535:
        raise ValueError("Port must be between 1 and 65535.")
    if protocol not in ("tcp", "udp"):
        raise ValueError("Protocol must be tcp or udp.")
    timeout = max(1, min(timeout, 30))

    if not _which("nc"):
        return {
            "returncode": -1, "stdout": "", "command": "",
            "stderr": "nc (netcat) absent (apt install netcat-openbsd).",
            "duration_ms": 0, "timed_out": False,
        }

    cmd = ["nc", "-z", "-v", "-w", str(timeout)]
    if protocol == "udp":
        cmd.append("-u")
    cmd.extend([target, str(port)])
    # nc -v ecrit sur stderr, on merge en stdout cote presentation
    res = _run(cmd, timeout=timeout + 5)
    # Concat stderr -> stdout si stdout vide (nc ecrit son resultat en
    # stderr "Connection to ... succeeded!").
    if not res["stdout"] and res["stderr"]:
        res["stdout"] = res["stderr"]
        res["stderr"] = ""
    return res


# --- tcpdump (lecture brieve) ---

def conntrack_show(zone: str | None = None, limit: int = 200) -> dict:
    """Liste les connexions conntrack actives (kernel netfilter).

    `zone` filtre par direction (`reply`, `original`, None=tout) ou par
    IP/host quand il contient un point. C'est un wrapper safe autour de
    `conntrack -L`. Resultat tronque a `limit` lignes pour eviter de
    saturer l'UI.
    """
    if not _which("conntrack"):
        return {
            "returncode": -1, "stdout": "", "command": "conntrack -L",
            "stderr": "conntrack is missing (apt install conntrack).",
            "duration_ms": 0, "timed_out": False,
        }
    cmd = ["conntrack", "-L", "-n", "-o", "extended,timestamp"]
    if zone:
        # On accepte un filtre sous forme d'IP ou de proto. Aucun shell
        # n'est implique, conntrack rejettera les filtres invalides.
        z = zone.strip()
        if len(z) > 64:
            raise ValueError("Filter too long (max 64 chars).")
        if "." in z or ":" in z:
            cmd += ["-s", z]
        elif z.lower() in ("tcp", "udp", "icmp"):
            cmd += ["-p", z.lower()]
    res = _run(cmd, timeout=10)
    limit = max(10, min(limit, 1000))
    lines = (res.get("stdout") or "").splitlines()
    if len(lines) > limit:
        res["stdout"] = "\n".join(lines[:limit]) + f"\n... truncated, {len(lines) - limit} more entries"
    return res


def list_interfaces() -> list[str]:
    """Liste les interfaces reseau du systeme pour le select de l'UI."""
    try:
        proc = subprocess.run(
            ["ip", "-o", "link", "show"],
            capture_output=True, text=True, timeout=3,
        )
        if proc.returncode != 0:
            return []
        # Format : "1: lo: <LOOPBACK,UP,LOWER_UP> ..."
        names = []
        for line in proc.stdout.splitlines():
            m = re.match(r"^\d+:\s+([^:@]+)[:@]", line)
            if m:
                names.append(m.group(1))
        return names
    except (subprocess.SubprocessError, FileNotFoundError):
        return []


# --- Snapshots etat systeme (read-only, sans argument) ---
#
# Trois dumps utiles a tout admin firewall qui debugge a distance :
# `ip route show` pour la table de routage, `ip addr show` pour les
# adresses par interface, et `nft list ruleset` pour voir la ruleset
# active (celle que MurOS a effectivement chargee, pas celle en DB).
# Chacun s'execute via subprocess, timeout court (5s), aucun argument
# utilisateur => zero risque d'injection.


def show_routes() -> dict:
    return _run(["ip", "-4", "route", "show"], timeout=5)


def show_addresses() -> dict:
    return _run(["ip", "-o", "addr", "show"], timeout=5)


def show_nft_ruleset() -> dict:
    if not _which("nft"):
        return {
            "returncode": -1, "stdout": "", "command": "nft list ruleset",
            "stderr": "nft est absent (apt install nftables).",
            "duration_ms": 0, "timed_out": False,
        }
    return _run(["nft", "list", "ruleset"], timeout=5)


_PUBLIC_IP_PROVIDERS = [
    "https://ifconfig.me/ip",
    "https://api.ipify.org",
    "https://icanhazip.com",
    "https://ipinfo.io/ip",
]


def _is_valid_ip(s: str) -> bool:
    try:
        ipaddress.ip_address(s.strip())
        return True
    except ValueError:
        return False


def public_ip(family: str = "auto") -> dict:
    """Query a few well-known providers to discover the WAN egress IP.

    family: "v4" forces IPv4 (curl -4), "v6" forces IPv6 (curl -6),
    "auto" lets curl pick whichever stack reaches the provider first.

    Calls each provider sequentially with a short timeout. Returning
    several results in the same output is intentional: cross-checking
    providers exposes captive portals or MITM (when answers diverge).
    """
    import time

    if not _which("curl"):
        return {
            "returncode": -1, "stdout": "",
            "command": "public-ip",
            "stderr": "curl est absent (apt install curl).",
            "duration_ms": 0, "timed_out": False,
        }

    if family not in {"auto", "v4", "v6"}:
        raise ValueError("family must be auto, v4 or v6.")

    family_flag = {"v4": "-4", "v6": "-6"}.get(family)

    started = time.time()
    lines: list[str] = []
    results: list[str] = []
    timed_out_any = False
    success_count = 0

    for url in _PUBLIC_IP_PROVIDERS:
        cmd = ["curl", "--silent", "--show-error", "--max-time", "3"]
        if family_flag:
            cmd.append(family_flag)
        cmd.append(url)
        provider = url.replace("https://", "").split("/")[0]
        t0 = time.time()
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            dt = int((time.time() - t0) * 1000)
            raw = (proc.stdout or "").strip().splitlines()
            ip = raw[0].strip() if raw else ""
            if proc.returncode == 0 and _is_valid_ip(ip):
                results.append(ip)
                success_count += 1
                lines.append(f"{provider:<24}  {ip:<40}  {dt:>4} ms")
            else:
                err = (proc.stderr or "").strip().splitlines()
                msg = err[0] if err else f"unexpected response: {ip[:80]!r}"
                lines.append(f"{provider:<24}  ERROR  {msg}")
        except subprocess.TimeoutExpired:
            timed_out_any = True
            lines.append(f"{provider:<24}  TIMEOUT after 5s")
        except FileNotFoundError:
            return {
                "returncode": -1, "stdout": "",
                "command": " ".join(cmd),
                "stderr": "curl est absent (apt install curl).",
                "duration_ms": 0, "timed_out": False,
            }

    # Summary block: agreement check. If all providers returned the same
    # IP, surface it on the first line. Divergence is a strong hint that
    # something is intercepting the egress (captive portal, transparent
    # proxy) and deserves attention.
    header = []
    unique = sorted(set(results))
    if success_count == 0:
        header.append("Public IP could not be determined. See provider errors below.")
    elif len(unique) == 1:
        header.append(f"Public IP : {unique[0]}")
        header.append(f"({success_count} provider(s) agreed)")
    else:
        header.append("WARNING: providers disagree on the public IP.")
        header.append("Possible causes: captive portal, transparent proxy, dual-WAN.")
        header.append("Observed: " + ", ".join(unique))
    header.append("")
    header.append(f"{'Provider':<24}  {'Result':<40}  Time")
    header.append("-" * 78)

    stdout = "\n".join(header + lines) + "\n"
    duration_ms = int((time.time() - started) * 1000)
    return {
        "returncode": 0 if success_count else -1,
        "stdout": stdout,
        "stderr": "",
        "command": f"curl -s --max-time 3{(' ' + family_flag) if family_flag else ''} {{ifconfig.me,api.ipify.org,icanhazip.com,ipinfo.io}}/ip",
        "duration_ms": duration_ms,
        "timed_out": timed_out_any and success_count == 0,
    }


def tcpdump_capture(interface: str, count: int = 50, filter_expr: str | None = None) -> dict:
    """Capture quelques paquets via tcpdump puis stop.

    Le filter_expr est passe tel quel a tcpdump (syntaxe BPF). C'est un
    point d'injection volontaire mais limite : tcpdump tourne sans shell,
    donc on ne peut pas injecter de commande, juste un filtre BPF
    eventuellement invalide.
    """
    interface = _validate_interface(interface)
    count = max(1, min(count, 500))
    if not _which("tcpdump"):
        return {
            "returncode": -1, "stdout": "", "command": "",
            "stderr": "tcpdump est absent (apt install tcpdump).",
            "duration_ms": 0, "timed_out": False,
        }
    cmd = ["tcpdump", "-i", interface, "-c", str(count), "-nn", "-t", "--immediate-mode"]
    if filter_expr:
        # Limite raisonnable de longueur pour eviter les abus.
        if len(filter_expr) > 256:
            raise ValueError("BPF filter too long (max 256 chars).")
        cmd.append(filter_expr)
    return _run(cmd, timeout=20)
