"""Donnees initiales : creees au premier demarrage si la base est vide."""
import logging

from sqlalchemy.orm import Session

from app import models
from app.auth import hash_password
from app.system import list_system_interfaces, get_default_gateway

log = logging.getLogger("muros.seed")


def seed_admin_user(db: Session) -> None:
    """Cree l'utilisateur admin par defaut si aucun utilisateur n'existe."""
    if db.query(models.User).count() > 0:
        return
    admin = models.User(
        username="admin",
        password_hash=hash_password("muros"),
        is_admin=True,
        must_change_password=True,
    )
    db.add(admin)
    db.commit()
    log.warning("Utilisateur admin cree avec mot de passe par defaut 'muros' (a changer)")


def seed_snmp_if_missing(db: Session) -> None:
    """Cree la ligne SnmpConfig au premier boot avec enabled=True.

    SNMP est active par defaut sur une appliance firewall (monitoring attendu).
    L'ecoute reste limitee aux LAN prives (10/8, 172.16/12, 192.168/16) via
    allowed_networks et community 'public' en lecture seule. L'admin peut
    desactiver ou durcir depuis Notifications > SNMP.
    """
    cfg = db.get(models.SnmpConfig, 1)
    if cfg is not None:
        return
    db.add(models.SnmpConfig(id=1))
    db.commit()
    log.info("SnmpConfig seede avec enabled=True (defaut appliance)")


def apply_snmp_if_enabled(db: Session) -> None:
    """Au boot, si SnmpConfig.enabled=True et snmpd n'est pas actif, on l'applique.

    Best-effort : on swallow les exceptions pour ne pas bloquer le demarrage de
    l'API si snmpd manque ou refuse la conf. L'utilisateur verra l'etat dans
    l'UI et pourra cliquer Appliquer.
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
        log.info("SNMP applique au boot (enabled par defaut)")
    except Exception as exc:  # noqa: BLE001
        log.warning("Application SNMP au boot impossible : %s", exc)


def seed_if_empty(db: Session) -> None:
    """Initialise les zones par defaut et importe les interfaces physiques reelles."""
    if db.query(models.Zone).count() > 0:
        return

    wan = models.Zone(name="wan", description="External network (Internet)")
    lan = models.Zone(name="lan", description="Internal network")
    dmz = models.Zone(name="dmz", description="Demilitarized zone")
    db.add_all([wan, lan, dmz])
    db.flush()

    # Import automatique des interfaces physiques detectees, AVEC l'IP
    # et la gateway en cours. C'est CRITIQUE au premier boot d'une
    # appliance installee via DHCP : si on ne capture pas l'IP active,
    # muros-boot ecrit ip_mode='none' au prochain reboot et le boitier
    # perd son IP, l'admin perd l'acces. Un firewall n'utilise pas DHCP
    # en run, mais il en herite UNE FOIS au moment de l'install. On fige
    # cette config en mode 'static' dans la DB, l'admin pourra ajuster
    # ensuite via l'UI.
    imported = 0
    seeded_with_ip = 0
    # Interfaces deja en DB (typiquement, l adoption a la lifespan FastAPI
    # les a deja inserees a partir du kernel). On les skip ici pour eviter
    # un UNIQUE constraint failed.
    existing_iface_names = {
        row[0] for row in db.query(models.Interface.name).all()
    }
    for sysif in list_system_interfaces():
        if sysif["is_virtual"]:
            continue
        if sysif["name"] in existing_iface_names:
            continue  # deja adoptee depuis le kernel
        # Premiere IPv4 globale (on ignore link-local 169.254/16 et v6).
        cidr: str | None = None
        for addr in sysif["addresses"]:
            ip_part = addr.split("/", 1)[0]
            if ":" in ip_part:
                continue  # IPv6 pas seede automatiquement
            if ip_part.startswith("169.254."):
                continue  # link-local fallback DHCP
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
        "Seed : %d interface(s) importee(s), %d avec IP figee depuis l'install",
        imported, seeded_with_ip,
    )

    # Regles d'amorce. Volontairement permissives sur l'admin (any -> firewall
    # sur 22/80/443) pour eviter le lock-out au premier boot tant que les
    # interfaces ne sont pas rattachees aux zones. L'admin restreindra
    # ensuite via l'UI une fois ses zones cablees.
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
        models.FirewallRule(
            position=10, chain="forward", action="accept",
            src_zone_id=lan.id, dst_zone_id=wan.id,
            comment="LAN egress to Internet",
        ),
        # No explicit catch-all drop: the forward chain has policy drop,
        # which already denies anything not matched by a previous rule.
        # Same convention as the input and output chains.
    ]
    db.add_all(rules)

    db.commit()
