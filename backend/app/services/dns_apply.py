# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Generation de /etc/unbound/unbound.conf.d/muros.conf + reload Unbound.

Mode : recursive validating resolver. Si forwarders non vides -> mode
forwarder vers ces IPs (utile derriere ISP qui filtre le 53 outbound).
otherwise, recursive pur depuis les root servers.
"""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import DnsConfig, DnsLocalRecord

log = logging.getLogger("muros.dns")

CONF_PATH = Path("/etc/unbound/unbound.conf.d/muros.conf")
RESOLV_CONF = Path("/etc/resolv.conf")
RESOLV_BACKUP = Path("/etc/resolv.conf.muros-pre-unbound")
_APPLY = os.environ.get("MUROS_APPLY", "false").lower() in ("1", "true", "yes")

# Defensive allowlist : domains we never want any future blocklist to
# blackhole. Critical for MurOS to keep working (apt update, GitHub
# release polling, public IP detection). Rendered as `local-zone NAME
# transparent` which makes Unbound resolve them normally even if a
# blocklist defines a NXDOMAIN/A 0.0.0.0 entry for the same name.
SYSTEM_ALLOWLIST = (
    "debian.org",
    "muros.org",
    "github.com",
    "githubusercontent.com",
    "github.io",
    "letsencrypt.org",
    "ifconfig.me",
    "ifconfig.co",
    "ipify.org",
)

# Fallback resolver used when use_as_system_resolver is True : even if
# Unbound is stopped or crashes, the box keeps resolving (apt, curl,
# certbot). Chosen because :
#   - reachable globally with no auth,
#   - DNSSEC capable,
#   - low latency from anywhere.
_SYSTEM_RESOLVER_FALLBACK = "1.1.1.1"


def _get_singleton(db: Session) -> DnsConfig:
    cfg = db.get(DnsConfig, 1)
    if cfg is None:
        cfg = DnsConfig(id=1)
        db.add(cfg)
        db.commit()
    return cfg


def render(db: Session) -> str:
    cfg = _get_singleton(db)
    if not cfg.enabled:
        return ""

    lines: list[str] = [
        "# /etc/unbound/unbound.conf.d/muros.conf -- managed by MurOS.",
        "server:",
        "  interface: 0.0.0.0@53",
        "  interface: ::0@53",
        "  do-ip4: yes",
        "  do-ip6: yes",
        "  do-udp: yes",
        "  do-tcp: yes",
        "  hide-identity: yes",
        "  hide-version: yes",
        "  qname-minimisation: yes",
        "  harden-glue: yes",
        "  harden-dnssec-stripped: yes",
        "  use-caps-for-id: yes",
        f"  prefetch: {'yes' if cfg.prefetch else 'no'}",
        # OPNsense model: Unbound listens on every interface and accepts
        # queries at the daemon level; who can actually reach the resolver
        # is decided at the firewall (input chain udp/tcp 53). The
        # default-drop input policy plus the seeded "allow LAN to firewall"
        # rule keep the WAN closed, so the box is not an open resolver.
        "  access-control: 0.0.0.0/0 allow",
        "  access-control: ::0/0 allow",
    ]

    # Note: do NOT emit `auto-trust-anchor-file` here. The Debian
    # `unbound` package already ships
    # `/etc/unbound/unbound.conf.d/root-auto-trust-anchor-file.conf`
    # which declares it. Re-declaring it makes unbound-checkconf fail
    # with "trust anchor for '.' presented twice". DNSSEC validation
    # stays enabled by default through that shipped file; toggling
    # `cfg.dnssec` off here would require removing the shipped file
    # which we deliberately avoid (the OS package owns it).

    # Defensive allowlist : ensure critical infrastructure domains stay
    # resolvable even if a future blocklist tries to sinkhole them. The
    # `transparent` type tells Unbound : resolve normally unless a
    # local-data entry exists for that exact name.
    for domain in SYSTEM_ALLOWLIST:
        lines.append(f'  local-zone: "{domain}." transparent')

    # Records locaux : zone authoritative `local-zone` + `local-data`.
    # Un local-zone par hostname to avoid d'inonder le cache avec
    # NXDOMAIN sur les sous-noms.
    records = db.query(DnsLocalRecord).all()
    for r in records:
        zone = r.name if r.name.endswith(".") else r.name + "."
        rtype = r.record_type
        # Avoid emitting `local-zone static` for relative record types
        # (CNAME, MX, SRV, TXT). A static local-zone makes Unbound
        # return NXDOMAIN for any subname that has no entry, which is
        # fine for A/AAAA but breaks layered services (e.g. an MX
        # alongside an A on the same name needs both records visible).
        # We use `transparent` for the indirect types so other records
        # at the same name still resolve from upstream.
        lines.append(
            f'  local-zone: "{zone}" '
            + ("static" if rtype in ("A", "AAAA", "PTR") else "transparent")
        )
        lines.append(f'  local-data: "{zone} IN {rtype} {_format_rdata(rtype, r.value)}"')

    forwarders = (cfg.forwarders or "").strip()
    if forwarders:
        lines.append("")
        lines.append('forward-zone:')
        lines.append('  name: "."')
        for ip in [s.strip() for s in forwarders.split(",") if s.strip()]:
            lines.append(f"  forward-addr: {ip}")

    return "\n".join(lines) + "\n"


def _format_rdata(rtype: str, value: str) -> str:
    """Normalize a record value into the rdata syntax Unbound expects.

    The operator types the value the natural way (e.g.
    ``mail.example.com`` for an MX target, ``v=spf1 ...`` for a TXT,
    ``10 mail.example.com`` already prefixed by priority). We turn that
    into the wire form Unbound parses without surprise.
    """
    value = (value or "").strip()
    if not value:
        return ""

    def _fqdn(host: str) -> str:
        return host if host.endswith(".") else host + "."

    if rtype in ("A", "AAAA"):
        return value

    if rtype == "PTR":
        return _fqdn(value)

    if rtype == "CNAME":
        return _fqdn(value)

    if rtype == "TXT":
        # Already quoted ? Keep as-is so the operator can pre-split
        # strings longer than 255 chars (`"part1" "part2"`).
        if value.startswith('"') and value.endswith('"'):
            return value
        # Escape any embedded double-quote then wrap in quotes.
        escaped = value.replace('\\', '\\\\').replace('"', '\\"')
        return f'"{escaped}"'

    if rtype == "MX":
        # Accept either ``mail.example.com`` (priority defaults to 10)
        # or ``20 mail.example.com``.
        parts = value.split(None, 1)
        if len(parts) == 2 and parts[0].isdigit():
            return f"{parts[0]} {_fqdn(parts[1])}"
        return f"10 {_fqdn(value)}"

    if rtype == "SRV":
        # ``priority weight port target`` is mandatory. We just FQDN
        # the target if needed.
        parts = value.split()
        if len(parts) == 4:
            prio, weight, port, target = parts
            return f"{prio} {weight} {port} {_fqdn(target)}"
        return value  # leave as-is so the operator sees the parse error

    return value


def _first_fallback(cfg) -> str:
    """Pick a non-loopback resolver to drop in /etc/resolv.conf as
    fallback when Unbound is the system resolver. Use the first
    upstream forwarder if defined, else the global fallback."""
    forwarders = (cfg.forwarders or "").strip()
    if forwarders:
        for ip in forwarders.split(","):
            ip = ip.strip()
            if ip and not ip.startswith("127."):
                return ip
    return _SYSTEM_RESOLVER_FALLBACK


def _write_system_resolv(cfg) -> None:
    """Point /etc/resolv.conf at Unbound on localhost, with a fallback.

    Backs up the previous resolv.conf once (RESOLV_BACKUP) so the
    'disable' path can restore exactly what the admin had configured
    before opting in to the local resolver.
    """
    if RESOLV_CONF.exists() and not RESOLV_BACKUP.exists():
        try:
            RESOLV_BACKUP.write_text(RESOLV_CONF.read_text(errors="replace"))
        except OSError:
            pass

    fallback = _first_fallback(cfg)
    body = (
        "# Generated by MurOS : Unbound used as system resolver.\n"
        "# A non-loopback fallback is appended so apt/curl keep working\n"
        "# if Unbound is stopped or crashes.\n"
        "nameserver 127.0.0.1\n"
        f"nameserver {fallback}\n"
        "options edns0 trust-ad\n"
    )
    try:
        RESOLV_CONF.write_text(body)
        os.chmod(RESOLV_CONF, 0o644)
    except OSError as exc:
        log.error("Failed to write /etc/resolv.conf : %s", exc)


def _restore_system_resolv() -> None:
    """Restore the pre-Unbound resolv.conf if we kept a backup."""
    if not RESOLV_BACKUP.exists():
        return
    try:
        RESOLV_CONF.write_text(RESOLV_BACKUP.read_text(errors="replace"))
        os.chmod(RESOLV_CONF, 0o644)
        RESOLV_BACKUP.unlink()
    except OSError as exc:
        log.error("Failed to restore /etc/resolv.conf : %s", exc)


def _port53_squatter() -> str | None:
    """Return a human description of the process holding :53, or None.

    Run before `systemctl start unbound` so we can fail fast with a
    clear message instead of letting Unbound restart-loop and end up
    in `failed` state. Uses `ss -Hlnp 'sport = :53'` which is part of
    iproute2 (always present on Debian). The output mentions the
    process name + PID, perfect for the UI error message.
    """
    try:
        out = subprocess.run(
            ["ss", "-Hlnp", "sport", "=", ":53"],
            capture_output=True, timeout=5, text=True,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    # Filter Unbound's own sockets : if Unbound is already running we
    # are reloading, not starting fresh, no conflict.
    lines = [ln for ln in out.stdout.splitlines() if "unbound" not in ln]
    if not lines:
        return None
    return "; ".join(lines[:3])


class DnsApplyError(RuntimeError):
    """Raised when Unbound cannot be started safely."""


def write_conf(db: Session) -> None:
    """Render and persist /etc/unbound/unbound.conf.d/muros.conf only.

    No systemd action. Used by Save in the UI; the running Unbound
    keeps serving the previous config until the operator clicks Apply.
    """
    if not _APPLY:
        log.info("MUROS_APPLY=0, skipping unbound.conf write")
        return
    content = render(db)
    CONF_PATH.parent.mkdir(parents=True, exist_ok=True)
    if content:
        CONF_PATH.write_text(content)
    elif CONF_PATH.exists():
        CONF_PATH.unlink()


def reload(db: Session) -> None:
    """Restart unbound (or stop it) to pick up the on-disk config.

    Called only by the explicit Apply action. Assumes write_conf() has
    already been run.
    """
    if not _APPLY:
        log.info("MUROS_APPLY=0, skipping unbound reload")
        return
    content = render(db)
    cfg = _get_singleton(db)
    if cfg.enabled and content:
        # Trust anchor : `unbound-anchor` writes /var/lib/unbound/root.key
        # if missing. Required by the `auto-trust-anchor-file` directive
        # when DNSSEC is enabled. Without this, `unbound-checkconf`
        # rejects the conf on a fresh install where the file was never
        # provisioned (unbound was disabled at postinst time).
        if cfg.dnssec:
            try:
                subprocess.run(
                    ["unbound-anchor", "-a", "/var/lib/unbound/root.key"],
                    capture_output=True, timeout=15,
                )
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass

        # Verifie first que la conf est valide before de poker Unbound :
        # une conf cassee mettrait le service en failed et tuerait la
        # resolution pour tout le LAN. Best-effort : si unbound-checkconf
        # is not dispo, on continue.
        try:
            check = subprocess.run(
                ["unbound-checkconf"], capture_output=True, timeout=10,
            )
        except FileNotFoundError:
            # unbound-checkconf absent : best-effort, we proceed.
            check = None
        if check is not None and check.returncode != 0:
            raise DnsApplyError(
                "unbound-checkconf rejected the generated configuration: "
                + check.stderr.decode(errors="replace").strip()
            )

        # Pre-flight : refuse to start Unbound if something else holds
        # :53 (typical : systemd-resolved that someone unmasked, an old
        # dnsmasq running in DNS mode, ...). Otherwise Unbound enters a
        # restart loop and finishes in `failed` state, with a cryptic
        # "Address already in use" message in journalctl.
        squatter = _port53_squatter()
        if squatter:
            raise DnsApplyError(
                "Port 53 is already in use by another process, refusing to "
                "start Unbound. Stop or remove it first. Detected: "
                + squatter
            )

        # enable+start (idempotent), then restart to pick up the new
        # conf reliably. Some directives (interface, access-control)
        # are not honored by a SIGHUP reload, restart is the safe path.
        subprocess.run(
            ["systemctl", "unmask", "unbound.service"],
            capture_output=True, timeout=10,
        )
        subprocess.run(
            ["systemctl", "enable", "unbound.service"],
            capture_output=True, timeout=10,
        )
        r = subprocess.run(
            ["systemctl", "restart", "unbound.service"],
            capture_output=True, timeout=15,
        )
        if r.returncode != 0:
            err = r.stderr.decode(errors="replace").strip()
            log.error("systemctl restart unbound failed (rc=%s): %s",
                      r.returncode, err)
            raise DnsApplyError(
                "Failed to start unbound: " + (err or "see journalctl -u unbound")
            )

        # systemctl returned 0 but that only means the start command was
        # accepted; the service might still die a moment later (e.g. an
        # interface bind that fails after the conf is loaded). Wait
        # briefly and re-check is-active so we report the real state
        # to the operator instead of pretending success.
        import time
        for _ in range(8):  # up to ~2s
            time.sleep(0.25)
            check = subprocess.run(
                ["systemctl", "is-active", "unbound.service"],
                capture_output=True, timeout=5, text=True,
            )
            state = (check.stdout or "").strip()
            if state == "active":
                break
            if state in ("failed", "inactive"):
                continue
        else:
            state = "inactive"
        if state != "active":
            tail = ""
            try:
                jr = subprocess.run(
                    ["journalctl", "-u", "unbound.service",
                     "-n", "10", "--no-pager", "-o", "cat"],
                    capture_output=True, timeout=5, text=True,
                )
                tail = (jr.stdout or "").strip()
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass
            raise DnsApplyError(
                f"unbound failed to stay up (current state: {state}). "
                + (f"Last log: {tail.splitlines()[-1]}" if tail else
                   "Run journalctl -u unbound for details.")
            )
        if cfg.use_as_system_resolver:
            _write_system_resolv(cfg)
        else:
            _restore_system_resolv()
    else:
        subprocess.run(
            ["systemctl", "disable", "--now", "unbound.service"],
            capture_output=True, timeout=10,
        )
        # When the service is disabled, never leave /etc/resolv.conf
        # pointing at a dead 127.0.0.1.
        _restore_system_resolv()


def apply(db: Session) -> None:
    """Backwards-compatible helper: write_conf then reload in one go."""
    write_conf(db)
    reload(db)


def get_status(db: Session) -> dict:
    """Return a snapshot of the recursive DNS service state."""
    from app.service_state import service_state, pkg_version, which
    from app.models import DnsLocalRecord
    cfg = _get_singleton(db)
    return {
        "enabled": cfg.enabled,
        "installed": which("unbound"),
        "service_state": service_state("unbound.service"),
        "version": pkg_version("unbound"),
        "records_count": db.query(DnsLocalRecord).count(),
        "system_resolver_active": (
            cfg.use_as_system_resolver and RESOLV_BACKUP.exists()
        ),
        "config_path": str(CONF_PATH),
    }
