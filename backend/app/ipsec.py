# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""IPsec via strongSwan (swanctl/vici).

MurOS s'appuie sur le paquet Debian `strongswan` (et son plugin
`strongswan-swanctl` pour l'interface moderne). Les tunnels sont decrits
dans `/etc/swanctl/conf.d/muros.conf` au format swanctl, rendu depuis la
DB SQLite. Le daemon `strongswan-starter.service` (Debian) charge la conf
au demarrage, et `swanctl --load-all` permet un reload a chaud.
"""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

from app.apply import APPLY_ENABLED

log = logging.getLogger("muros.ipsec")

IPSEC_PACKAGES = ["strongswan", "strongswan-swanctl"]
# On Debian 12+, the main service is called strongswan-starter.
# On Debian 11 it was strongswan, we try both.
IPSEC_SERVICES = ["strongswan", "strongswan-starter"]

SWANCTL_CONF = Path("/etc/swanctl/conf.d/muros.conf")
SWANCTL_SECRETS = Path("/etc/swanctl/conf.d/muros.secrets")


from app.service_state import is_active as _systemd_active, which as _which  # noqa: E402


def get_or_create_global_config(db):
    """Fetch the IpsecGlobalConfig singleton, creating it if absent.

    Centralised so every caller (API, muros_boot, apply pipeline)
    observes the same default (enabled=True) when migrating from a
    release that did not have this table.
    """
    from app import models
    cfg = db.get(models.IpsecGlobalConfig, 1)
    if cfg is None:
        cfg = models.IpsecGlobalConfig(id=1, enabled=True)
        db.add(cfg)
        db.commit()
        db.refresh(cfg)
    return cfg


def _ipsec_service_active() -> tuple[bool, str | None]:
    for svc in IPSEC_SERVICES:
        if _systemd_active(svc):
            return True, svc
    return False, None


def _ipsec_service_installed() -> str | None:
    """Return the name of the systemd unit that is actually present, else None.

    Lets us distinguish "unknown service" (nothing installed) from "inactive
    service" (unit present but stopped). We look in order strongswan-starter
    then strongswan: on Debian 12+ the first one really exists, on Debian 11
    it was the second.
    """
    if not _which("systemctl"):
        return None
    for svc in ("strongswan-starter", "strongswan"):
        try:
            r = subprocess.run(
                ["systemctl", "list-unit-files", f"{svc}.service", "--no-legend"],
                text=True, capture_output=True, timeout=3,
            )
            if r.returncode == 0 and r.stdout.strip():
                return svc
        except (subprocess.SubprocessError, FileNotFoundError):
            continue
    return None


def _swanctl_version() -> str | None:
    """Version strongSwan via dpkg (source de verite).

    On evite le binaire `swanctl --version` qui peut sortir en code non
    zero a cause de warnings plugin parasites sur Debian, donnant un
    "version indisponible" trompeur. La VRAIE version est celle que dpkg
    a installee : c'est celle qu'on affiche, et c'est instantane.
    """
    from app.service_state import pkg_version
    return pkg_version("strongswan", "strongSwan")


def _list_active_sas() -> list[dict]:
    """Retourne les Security Associations actives via `swanctl --list-sas`.

    Format brut, parse minimal : on retient le nom de la connexion et l'etat.
    Le parsing fin viendra en phase 2 quand on aura un modele formel.
    """
    if not _which("swanctl"):
        return []
    try:
        out = subprocess.check_output(
            ["swanctl", "--list-sas"], text=True, timeout=5, stderr=subprocess.STDOUT,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return []

    sas: list[dict] = []
    current: dict | None = None
    for line in out.splitlines():
        if not line:
            continue
        # Ligne de header IKE_SA : "name[1]: ESTABLISHED ..."
        if not line.startswith(" ") and ":" in line and "[" in line:
            name = line.split("[", 1)[0].strip()
            rest = line.split(":", 1)[1].strip()
            state = rest.split()[0] if rest else "unknown"
            current = {"name": name, "state": state, "details": rest[:200]}
            sas.append(current)
    return sas


def get_status() -> dict:
    """Etat live IPsec : paquets, service, version, SAs actives.

    `installed` se base uniquement sur swanctl, qui est l'interface moderne
    livree par strongswan-swanctl. Le binaire historique `ipsec` n'est plus
    distribue par defaut sur Debian 12+, le tester ferait remonter "non
    installe" alors que strongswan tourne bien.
    """
    from app.service_state import service_state as _state
    installed = _which("swanctl")
    active, service_name = _ipsec_service_active()
    # If no unit is active, we still look for the installed unit
    # (strongswan-starter on Debian 12+, strongswan on Debian 11) to report
    # a clean "inactive" state instead of an "unknown" that yields
    # "Service strongswan inconnu" cote UI.
    if service_name is None:
        service_name = _ipsec_service_installed()
    return {
        "installed": installed,
        "version": _swanctl_version(),
        "service_active": active,
        "service_state": _state(service_name) if service_name else "unknown",
        "service_name": service_name,
        "active_sas": _list_active_sas(),
        "globally_enabled": _read_global_enabled(),
    }


def _read_global_enabled() -> bool:
    """Lookup the IpsecGlobalConfig singleton without forcing the caller
    to pass a Session. Returns True if the table is missing (compat with
    a freshly migrated install where the row was not created yet)."""
    try:
        from app.db import SessionLocal
        with SessionLocal() as db:
            cfg = get_or_create_global_config(db)
            return bool(cfg.enabled)
    except Exception:  # noqa: BLE001
        return True


def install_packages() -> dict:
    """Installe strongswan + strongswan-swanctl via apt. Idempotente."""
    already = _which("swanctl") and _which("ipsec")
    if already:
        return {
            "installed": True,
            "already_present": IPSEC_PACKAGES,
            "newly_installed": [],
            "output_tail": "",
        }

    if not APPLY_ENABLED:
        return {
            "installed": False,
            "already_present": [],
            "newly_installed": [],
            "output_tail": (
                f"dry-run : aurait execute 'apt-get install -y {' '.join(IPSEC_PACKAGES)}' "
                "(MUROS_APPLY off)."
            ),
        }

    if os.geteuid() != 0:
        raise RuntimeError(
            "Installation impossible : MurOS doit tourner en root. "
            f"Installer manuellement : apt install -y {' '.join(IPSEC_PACKAGES)}"
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
            f"apt-get update a echoue : {(proc_update.stderr or '').strip()[:400]}"
        )

    # For strongswan we keep --no-install-recommends, the recommends include
    # many modules we do not use (charon-cmd, libcharon-extauth-plugins).
    proc = subprocess.run(
        ["apt-get", "install", "-y", "--no-install-recommends", *IPSEC_PACKAGES],
        env=env, capture_output=True, text=True, timeout=600,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"apt-get install a echoue (code {proc.returncode}) : "
            f"{(proc.stderr or '').strip()[:400]}"
        )

    if not (_which("swanctl") and _which("ipsec")):
        raise RuntimeError(
            f"Binaires absents apres install : swanctl/ipsec. Sortie : {proc.stdout[-400:]}"
        )

    return {
        "installed": True,
        "already_present": [],
        "newly_installed": IPSEC_PACKAGES,
        "output_tail": proc.stdout[-800:],
    }


# --- Rendu de la conf swanctl ---

def render_swanctl_conf(connections: list, certs_by_id: dict | None = None) -> str:
    """Render the connections { ... } block of the swanctl.conf file.

    connections: list of IpsecConnection (only the enabled ones are included).
    certs_by_id: dict {id: IpsecCert} to resolve local_cert_id and
                 remote_cert_id in auth=cert mode. Optional (None = PSK only).
    """
    from app import ipsec_pki
    if certs_by_id is None:
        certs_by_id = {}

    lines = [
        "# Genere par MurOS - ne pas editer a la main.",
        "# Recharger avec : swanctl --load-all",
        "",
        "connections {",
    ]
    has_enabled = False
    for c in connections:
        if not c.enabled:
            continue
        has_enabled = True
        local_id = c.local_id or c.local_addrs
        remote_id = c.remote_id or c.remote_addrs
        auth_mode = (c.auth_mode or "psk").lower()

        # Local section (auth depends on mode).
        local_lines = ["        local {"]
        if auth_mode == "cert":
            local_cert = certs_by_id.get(c.local_cert_id) if c.local_cert_id else None
            local_lines.append("            auth = pubkey")
            if local_cert:
                local_lines.append(f"            certs = {ipsec_pki.cert_filename(local_cert)}")
            local_lines.append(f"            id = {local_id}")
        else:
            local_lines.append("            auth = psk")
            local_lines.append(f"            id = {local_id}")
        local_lines.append("        }")

        # Section remote.
        remote_lines = ["        remote {"]
        if auth_mode == "cert":
            remote_lines.append("            auth = pubkey")
            # cacerts : la CA muros valide tout cert signe par elle.
            remote_lines.append(f"            cacerts = {ipsec_pki.CA_FILENAME}")
            # Si un cert distant precis est attendu, on l'ajoute en
            # validation supplementaire via id.
            remote_cert = certs_by_id.get(c.remote_cert_id) if c.remote_cert_id else None
            if remote_cert:
                # Force l'id sur le CN du cert distant.
                remote_lines.append(f"            id = {remote_cert.subject_cn}")
            else:
                remote_lines.append(f"            id = {remote_id}")
        else:
            remote_lines.append("            auth = psk")
            remote_lines.append(f"            id = {remote_id}")
        remote_lines.append("        }")

        lines.extend([
            f"    {c.name} {{",
            "        version = 2",
            f"        local_addrs = {c.local_addrs}",
            f"        remote_addrs = {c.remote_addrs}",
            f"        proposals = {c.ike_proposals}",
            *local_lines,
            *remote_lines,
            "        children {",
            f"            {c.name} {{",
            f"                local_ts = {c.local_ts}",
            f"                remote_ts = {c.remote_ts}",
            f"                esp_proposals = {c.esp_proposals}",
            f"                start_action = {c.start_action}",
            "                dpd_action = restart",
            "            }",
            "        }",
            "    }",
        ])
    lines.append("}")
    if not has_enabled:
        lines.append("# (no connection enabled)")
    return "\n".join(lines) + "\n"


def render_swanctl_secrets(connections: list, certs_by_id: dict | None = None) -> str:
    """Render the secrets file.

    - Mode PSK : ike-<name> { secret = "..." id-1 = ... id-2 = ... }
    - Mode cert : private-<name> { file = muros-<cert>-key.pem }
    """
    if certs_by_id is None:
        certs_by_id = {}

    lines = [
        "# Genere par MurOS - ne pas editer a la main.",
        "",
        "secrets {",
    ]
    for c in connections:
        if not c.enabled:
            continue
        auth_mode = (c.auth_mode or "psk").lower()
        if auth_mode == "cert":
            local_cert = certs_by_id.get(c.local_cert_id) if c.local_cert_id else None
            if local_cert and local_cert.is_local and local_cert.key_pem:
                # The file name is <prefix>muros-<name>-key.pem
                safe_name = local_cert.name.replace("/", "_")
                lines.extend([
                    f"    private-{c.name} {{",
                    f"        file = muros-{safe_name}-key.pem",
                    "    }",
                ])
        elif c.psk:
            local_id = c.local_id or c.local_addrs
            remote_id = c.remote_id or c.remote_addrs
            psk_escaped = c.psk.replace('\\', '\\\\').replace('"', '\\"')
            lines.extend([
                f"    ike-{c.name} {{",
                f'        secret = "{psk_escaped}"',
                f"        id-1 = {local_id}",
                f"        id-2 = {remote_id}",
                "    }",
            ])
    lines.append("}")
    return "\n".join(lines) + "\n"


# --- Apply ---

class IpsecApplyError(Exception):
    """Raised when swanctl refuses the rendered configuration.

    Caught by the Apply route and surfaced as a 409, so a bad
    connection definition does not silently leave strongSwan with
    the old config + a stale dirty flag.
    """


def write_conf(connections: list, ca=None, certs: list | None = None,
               revoked_certs: list | None = None) -> dict:
    """Materialise /etc/swanctl/conf.d/muros.conf + secrets + PKI only.

    No swanctl --load-all here, no systemd action. The running daemon
    keeps the previous config until the operator clicks Apply.
    """
    from app import ipsec_pki
    certs = certs or []
    revoked_certs = revoked_certs or []
    certs_by_id = {c.id: c for c in certs}

    conf_text = render_swanctl_conf(connections, certs_by_id)
    secrets_text = render_swanctl_secrets(connections, certs_by_id)

    if not APPLY_ENABLED:
        return {
            "message": f"dry-run : aurait ecrit {SWANCTL_CONF} et {SWANCTL_SECRETS}.",
            "conf_preview": conf_text,
        }

    SWANCTL_CONF.parent.mkdir(parents=True, exist_ok=True)
    SWANCTL_CONF.write_text(conf_text, encoding="utf-8")
    os.chmod(SWANCTL_CONF, 0o600)

    SWANCTL_SECRETS.write_text(secrets_text, encoding="utf-8")
    os.chmod(SWANCTL_SECRETS, 0o600)

    if ca is not None and ca.cert_pem:
        ipsec_pki.deploy_to_disk(ca, certs, revoked_certs)

    return {
        "message": "IPsec configuration saved.",
        "conf_path": str(SWANCTL_CONF),
    }


def reload(connections: list, ca=None, certs: list | None = None,  # noqa: D401
           revoked_certs: list | None = None, *,
           globally_enabled: bool = True) -> dict:
    """Reload swanctl with the on-disk config (or stop strongSwan).

    Called only by the explicit Apply action ; assumes write_conf has
    already been run.
    """
    return apply_config(connections, ca=ca, certs=certs,
                        revoked_certs=revoked_certs,
                        globally_enabled=globally_enabled)


def apply_config(connections: list, ca=None, certs: list | None = None,
                 revoked_certs: list | None = None, *,
                 defer_start: bool = False,
                 globally_enabled: bool = True) -> dict:
    """Ecrit les fichiers swanctl et fait un reload a chaud via swanctl --load-all.

    Si ca et certs sont fournis, deploie aussi la PKI (CA + certs + CRL)
    dans /etc/swanctl/x509ca/, x509/, private/, x509crl/.

    En dry-run : retourne le contenu sans ecrire.

    defer_start: en contexte boot (muros-boot.service avec
    Before=network-online.target), on ne peut pas faire
    `systemctl enable --now strongswan` car le service a
    After=network-online.target -> deadlock 15s. On separe alors
    enable (persistance) et start (--no-block, non bloquant).
    """
    from app import ipsec_pki
    certs = certs or []
    revoked_certs = revoked_certs or []
    certs_by_id = {c.id: c for c in certs}

    # Si une connexion est en mode cert, on a besoin de la PKI.
    needs_pki = any(
        (c.auth_mode or "psk").lower() == "cert"
        for c in connections if c.enabled
    )
    if needs_pki and (ca is None or not ca.cert_pem):
        raise RuntimeError(
            "Une connexion est en mode certificat mais la CA MurOS n'a pas "
            "ete generee. Generer la CA depuis l'onglet PKI d'abord."
        )

    conf_text = render_swanctl_conf(connections, certs_by_id)
    secrets_text = render_swanctl_secrets(connections, certs_by_id)

    if not APPLY_ENABLED:
        return {
            "message": f"dry-run : aurait ecrit {SWANCTL_CONF} et {SWANCTL_SECRETS}.",
            "conf_preview": conf_text,
        }

    # Pre-requis : swanctl present + service tournant. Si le service n'est
    # pas la, on ecrit quand meme les fichiers (ils seront lus au prochain
    # demarrage) et on previent.
    if not _which("swanctl"):
        raise RuntimeError(
            "swanctl not found. Install strongswan-swanctl first "
            "(bouton 'Installer maintenant')."
        )

    SWANCTL_CONF.parent.mkdir(parents=True, exist_ok=True)
    SWANCTL_CONF.write_text(conf_text, encoding="utf-8")
    os.chmod(SWANCTL_CONF, 0o600)

    SWANCTL_SECRETS.write_text(secrets_text, encoding="utf-8")
    os.chmod(SWANCTL_SECRETS, 0o600)

    # Deploiement de la PKI si on a une CA (meme si pas de connexion cert,
    # on garde la CA prete pour l'avenir).
    if ca is not None and ca.cert_pem:
        ipsec_pki.deploy_to_disk(ca, certs, revoked_certs)

    nb_enabled = len([c for c in connections if c.enabled])
    active, svc_name = _ipsec_service_active()

    # Global toggle off : tear down the service no matter how many
    # connections are enabled in DB. Keeps the swanctl conf on disk so
    # the operator can still see/edit it, but disables the unit so it
    # does not come back at reboot.
    if not globally_enabled:
        for s in IPSEC_SERVICES:
            subprocess.run(
                ["systemctl", "disable", "--now", s],
                capture_output=True, text=True, timeout=15,
            )
        return {
            "message": (
                "IPsec server globally disabled : strongswan stopped and "
                "disabled at boot (configuration preserved)."
            ),
            "service": svc_name or "",
        }

    if nb_enabled == 0:
        # No active connection left: shut strongswan down completely.
        for s in IPSEC_SERVICES:
            subprocess.run(
                ["systemctl", "disable", "--now", s],
                capture_output=True, text=True, timeout=15,
            )
        return {
            "message": "IPsec configuration saved, no active connection: strongswan disabled.",
            "service": svc_name or "",
        }

    # enable + start (persistant + immediat). Essai des deux noms de
    # service au cas ou (strongswan vs strongswan-starter selon la distro).
    target_svc = svc_name or "strongswan-starter"
    if not active:
        for s in IPSEC_SERVICES:
            # enable (persistance) sans --now : ne touche pas l'etat
            # runtime, ne depend d'aucune target, safe en contexte boot.
            r_en = subprocess.run(
                ["systemctl", "enable", s],
                capture_output=True, text=True, timeout=5,
            )
            if r_en.returncode != 0:
                # nom de service inexistant : essaie le suivant
                continue
            target_svc = s
            # Demarrage : --no-block en contexte boot (rend la main
            # tout de suite, systemd executera le start apres muros-boot
            # une fois network-online.target atteinte ; pas de deadlock).
            start_cmd = ["systemctl", "start", s]
            if defer_start:
                start_cmd.insert(2, "--no-block")
            r_start = subprocess.run(
                start_cmd, capture_output=True, text=True,
                timeout=5 if defer_start else 15,
            )
            if r_start.returncode == 0:
                break

    # Reload a chaud des connexions et secrets. En contexte boot avec
    # demarrage differe, le daemon n'est pas encore tourne donc le
    # socket vici n'existe pas : on saute le --load-all, la conf sera
    # chargee par strongswan lui-meme a son demarrage.
    if defer_start:
        return {
            "message": (
                f"Conf IPsec ecrite, demarrage differe de {target_svc} "
                f"({nb_enabled} connexion(s) active(s))."
            ),
            "service": target_svc,
        }
    res = subprocess.run(
        ["swanctl", "--load-all"], capture_output=True, text=True, timeout=30,
    )
    if res.returncode != 0:
        raise RuntimeError(
            f"swanctl --load-all a echoue : {(res.stderr or res.stdout).strip()[:400]}"
        )

    return {
        "message": f"Conf IPsec rechargee ({nb_enabled} connexion(s) active(s)).",
        "service": target_svc,
        "swanctl_output": (res.stdout or res.stderr).strip()[:400],
    }
