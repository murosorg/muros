"""Initial data: created on first boot if the database is empty."""
import ipaddress
import logging
import os

from sqlalchemy.orm import Session

from app import models
from app.system import list_system_interfaces, get_default_gateway

log = logging.getLogger("muros.seed")

# Default management address used as an anti-lock-out safety net when the
# box comes up with no usable IP at all (no DHCP at install, nothing
# configured statically). Follows the common appliance convention of
# shipping 192.168.1.1 on the LAN. Overridable at boot through
# MUROS_FALLBACK_MGMT_CIDR.
DEFAULT_FALLBACK_MGMT_CIDR = "192.168.1.1/24"


def seed_root_user(db: Session) -> None:
    """Ensure the 'root' mirror row exists and is granted UI access.

    Authentication is delegated to the system PAM stack: the web UI and
    SSH share the same Linux accounts. The default administrator is the
    system 'root' account, and MurOS uses its existing password as-is
    (the package never resets it, so there is no forced password change).
    This DB row carries no real password hash, only the JWT subject, the
    admin flag and the ui_access grant.

    root is the only account granted ui_access by default; every other
    Linux account stays locked out of the web UI until root enables it
    from Access > Users. We always (re)assert root's grant here, even on
    a non-empty DB, so an operator can never accidentally lock root out.
    """
    root = db.query(models.User).filter(models.User.username == "root").first()
    if root is None:
        root = models.User(
            username="root",
            password_hash="!",  # PAM is the source of truth, not this column
            is_admin=True,
            ui_access=True,
            must_change_password=False,
        )
        db.add(root)
        db.commit()
        log.info("Mirror root row created (system account 'root', admin + UI access)")
        return
    # Existing row: make sure root keeps admin rights and UI access.
    if not root.is_admin or not root.ui_access:
        root.is_admin = True
        root.ui_access = True
        db.commit()
        log.info("root mirror row re-asserted with is_admin + ui_access")


# Backwards-compatible alias: older call sites / tests import the
# previous name. Kept so an in-flight upgrade does not break imports.
seed_admin_user = seed_root_user


def seed_ssh_disabled_by_default(db: Session) -> None:
    """On a fresh install, SSH is closed by default.

    Materialize the SshConfig row with admin_disabled=True so the UI
    shows the 'disabled by admin' state, mirroring the `systemctl
    disable --now ssh` set by the postinst on a fresh install. The admin
    re-enables SSH explicitly from the SSH Access page. An existing row
    is never touched (upgrade case): only the initial creation imposes
    the closed default.
    """
    if db.get(models.SshConfig, 1) is not None:
        return
    db.add(models.SshConfig(id=1, admin_disabled=True))
    db.commit()
    log.info("SshConfig seeded with admin_disabled=True (SSH closed by default)")


def seed_snmp_if_missing(db: Session) -> None:
    """Create the SnmpConfig row on first boot with enabled=True.

    SNMP is enabled by default on a firewall appliance (monitoring is
    expected). Listening stays limited to private LANs (10/8, 172.16/12,
    192.168/16) via allowed_networks, with a read-only 'public'
    community. The admin can disable or harden it from Notifications >
    SNMP.
    """
    cfg = db.get(models.SnmpConfig, 1)
    if cfg is not None:
        return
    db.add(models.SnmpConfig(id=1))
    db.commit()
    log.info("SnmpConfig seeded with enabled=True (appliance default)")


def apply_snmp_if_enabled(db: Session) -> None:
    """At boot, if SnmpConfig.enabled=True and snmpd is not active, apply it.

    Best-effort: exceptions are swallowed so a missing snmpd or a refused
    config does not block API startup. The user sees the state in the UI
    and can click Apply.
    """
    cfg = db.get(models.SnmpConfig, 1)
    if cfg is None or not cfg.enabled:
        return
    try:
        from app import snmp
        st = snmp.get_status()
        if not st.get("snmpd_installed") or st.get("service_active"):
            return
        snmp.apply_config(cfg)
        log.info("SNMP applied at boot (enabled by default)")
    except Exception as exc:  # noqa: BLE001
        log.warning("Cannot apply SNMP at boot: %s", exc)


def seed_if_empty(db: Session) -> None:
    """Initialize the default zones and import the real physical interfaces."""
    if db.query(models.Zone).count() > 0:
        return

    wan = models.Zone(name="wan", description="External network (Internet)")
    lan = models.Zone(name="lan", description="Internal network")
    dmz = models.Zone(name="dmz", description="Demilitarized zone")
    db.add_all([wan, lan, dmz])
    db.flush()

    # Automatically import detected physical interfaces, WITH their
    # current IP and gateway. This is CRITICAL on the first boot of an
    # appliance installed over DHCP: if the active IP is not captured,
    # muros-boot writes ip_mode='none' on the next reboot and the box
    # loses its IP, the admin loses access. A firewall does not use DHCP
    # at runtime, but it inherits one ONCE at install time. We freeze
    # that config as 'static' in the DB; the admin can adjust it later
    # from the UI.
    imported = 0
    seeded_with_ip = 0
    # Interfaces already in DB (typically adoption at the FastAPI
    # lifespan already inserted them from the kernel). Skip them here to
    # avoid a UNIQUE constraint failure.
    existing_iface_names = {
        row[0] for row in db.query(models.Interface.name).all()
    }
    for sysif in list_system_interfaces():
        if sysif["is_virtual"]:
            continue
        if sysif["name"] in existing_iface_names:
            continue  # already adopted from the kernel
        # First global IPv4 (ignore link-local 169.254/16 and v6).
        cidr: str | None = None
        for addr in sysif["addresses"]:
            ip_part = addr.split("/", 1)[0]
            if ":" in ip_part:
                continue  # IPv6 not auto-seeded
            if ip_part.startswith("169.254."):
                continue  # link-local DHCP fallback
            if ip_part.startswith("127."):
                continue
            cidr = addr
            break
        gw = get_default_gateway(sysif["name"]) if cidr else None
        if cidr:
            ip_mode = "static"
            seeded_with_ip += 1
        else:
            ip_mode = "none"
        db.add(models.Interface(
            name=sysif["name"],
            description=(
                f"Detected at install (MAC {sysif['mac']})"
                if sysif["mac"] else "Detected at install"
            ),
            zone_id=None,
            ip_mode=ip_mode,
            ip_address=cidr,
            gateway=gw,
            mtu=sysif["mtu"] if sysif["mtu"] else None,
            enabled=sysif["state"] != "DOWN",
        ))
        imported += 1
    log.info(
        "Seed: %d interface(s) imported, %d with IP frozen from install",
        imported, seeded_with_ip,
    )

    # Bootstrap rules. Deliberately permissive on admin access (any ->
    # firewall on 22/80/443) to avoid a lock-out on first boot while the
    # interfaces are not yet attached to zones. The admin then restricts
    # via the UI once the zones are wired.
    rules = [
        models.FirewallRule(
            position=10, chain="input", action="accept",
            src_zone_id=None, protocol="tcp", dst_port="22",
            comment="SSH admin (restrict by zone once configured)",
            log=False,
        ),
        models.FirewallRule(
            position=20, chain="input", action="accept",
            src_zone_id=None, protocol="tcp", dst_port="80,443",
            comment="MurOS UI (restrict by zone once configured)",
        ),
        models.FirewallRule(
            position=30, chain="input", action="accept",
            src_zone_id=None, protocol="icmp",
            comment="ICMP (ping)",
        ),
        # Default "allow LAN to firewall": the LAN is the trusted zone, so
        # LAN clients can reach the box services (DNS, NTP, GUI, ...) out
        # of the box. Without this the input policy drop would block NTP
        # (123) and DNS (53) from the LAN even though the services run.
        # Restrict once zones are wired.
        models.FirewallRule(
            position=40, chain="input", action="accept",
            src_zone_id=lan.id,
            comment="LAN to firewall (default, restrict once configured)",
        ),
        # Default "allow LAN to any" (egress to Internet and other zones):
        # the trusted LAN can go out by default. Restrict once configured.
        models.FirewallRule(
            position=10, chain="forward", action="accept",
            src_zone_id=lan.id, dst_zone_id=None,
            comment="LAN to any (default allow, restrict once configured)",
        ),
        # No explicit catch-all drop: the forward chain has policy drop,
        # which already denies anything not matched by a previous rule.
        # Same convention as the input and output chains.
    ]
    db.add_all(rules)

    db.commit()


def _has_management_ip(db: Session) -> bool:
    """True if the box can already be reached on some IPv4.

    Either an interface carries a static IPv4 in the DB (the common case:
    the install-time DHCP lease is frozen as static by seed_if_empty), or
    the kernel currently holds a usable global IPv4 on any interface.
    Loopback, link-local (169.254/16) and IPv6 do not count.
    """
    for i in db.query(models.Interface).filter(models.Interface.enabled.is_(True)).all():
        if i.ip_mode == "static" and i.ip_address:
            try:
                ipaddress.ip_interface(i.ip_address)
                return True
            except ValueError:
                pass
    for sif in list_system_interfaces():
        for addr in sif.get("addresses", []):
            ip = addr.split("/", 1)[0]
            if ":" in ip or ip.startswith("169.254.") or ip.startswith("127."):
                continue
            return True
    return False


def _pick_fallback_interface(db: Session) -> models.Interface | None:
    """Choose the NIC that carries the fallback management address.

    Prefer a physical interface that is not attached to the WAN zone (the
    LAN/management side), falling back to the first physical interface.
    """
    wan = db.query(models.Zone).filter(models.Zone.name == "wan").first()
    wan_id = wan.id if wan else None
    candidates = (
        db.query(models.Interface)
        .filter(models.Interface.type == "physical")
        .order_by(models.Interface.name)
        .all()
    )
    non_wan = [i for i in candidates if i.zone_id != wan_id]
    pool = non_wan or candidates
    return pool[0] if pool else None


def _fallback_pool_range(net: ipaddress.IPv4Interface) -> tuple[str, str]:
    """Derive a sane DHCP range inside the fallback subnet.

    Uses .100-.200 of the subnet by default (matches the /24 case), and
    clamps to the usable host range for smaller subnets so the range
    never spills onto the network/broadcast addresses or the gateway.
    """
    base = int(net.network.network_address)
    bcast = int(net.network.broadcast_address)
    gw = int(net.ip)
    first_host, last_host = base + 1, bcast - 1
    start = min(max(base + 100, first_host), last_host)
    end = min(base + 200, last_host)
    if end < start:
        end = start
    # Never start the pool on the gateway address.
    if start == gw and start < last_host:
        start += 1
    if end < start:
        end = start
    return str(ipaddress.ip_address(start)), str(ipaddress.ip_address(end))


def ensure_management_fallback(db: Session) -> None:
    """Anti-lock-out safety net: guarantee the UI is reachable on first boot.

    When the box comes up with no usable IPv4 anywhere (no DHCP at install
    and nothing configured statically), an operator would otherwise have
    to drop to a shell to set an address. Instead we assign a deterministic
    management IP on a physical NIC and stand up a DHCP pool on it, so the
    operator just plugs a laptop into that port, gets a lease from Kea and
    reaches https://<gateway>/. The choice is persisted in the DB and stays
    editable from the Network page, exactly like an appliance default.

    This only fires when there is no other way in: a box that already holds
    an IP (frozen install lease or a static address) is left untouched.
    """
    if _has_management_ip(db):
        return

    raw = os.environ.get("MUROS_FALLBACK_MGMT_CIDR", DEFAULT_FALLBACK_MGMT_CIDR)
    try:
        net = ipaddress.ip_interface(raw)
        if not isinstance(net, ipaddress.IPv4Interface) or net.network.prefixlen >= 31:
            raise ValueError("need an IPv4 address in a subnet large enough for clients")
    except ValueError as exc:
        log.error("Invalid MUROS_FALLBACK_MGMT_CIDR=%r (%s), using %s",
                  raw, exc, DEFAULT_FALLBACK_MGMT_CIDR)
        net = ipaddress.ip_interface(DEFAULT_FALLBACK_MGMT_CIDR)

    iface = _pick_fallback_interface(db)
    if iface is None:
        log.error("No physical interface available to host the management "
                  "fallback address; the box may be unreachable")
        return

    iface.ip_mode = "static"
    iface.ip_address = str(net)
    iface.enabled = True
    iface.dirty = True
    # Attach to the LAN zone so the seeded "LAN to firewall" rule lets the
    # operator reach SSH/UI/services through this interface.
    lan = db.query(models.Zone).filter(models.Zone.name == "lan").first()
    if lan is not None and iface.zone_id is None:
        iface.zone_id = lan.id
    db.flush()

    # Stand up Kea on this interface so a directly attached laptop gets a
    # lease without any upstream DHCP server.
    cfg = db.get(models.DhcpConfig, 1)
    if cfg is None:
        cfg = models.DhcpConfig(id=1, enabled=True)
        db.add(cfg)
    else:
        cfg.enabled = True
    existing = (
        db.query(models.DhcpPool)
        .filter(models.DhcpPool.interface_id == iface.id)
        .first()
    )
    if existing is None:
        start, end = _fallback_pool_range(net)
        db.add(models.DhcpPool(
            interface_id=iface.id,
            range_start=start,
            range_end=end,
            enabled=True,
            comment="Auto-created management fallback pool",
        ))
    db.commit()
    log.error(
        "No management IP detected: assigned fallback %s on %s and enabled a "
        "DHCP pool. Plug a laptop into that port and open https://%s/ to "
        "finish setup (change this from the Network page).",
        str(net), iface.name, net.ip,
    )
