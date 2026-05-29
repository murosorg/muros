# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Gestion du certificat TLS de l'interface web (nginx).

Le drop-in nginx (`/etc/nginx/sites-available/muros`) pointe vers
`/etc/nginx/ssl/muros.crt` et `/etc/nginx/ssl/muros.key`. A l'install
ces fichiers sont des symlinks vers le snakeoil ssl-cert. L'admin peut :
  - uploader un cert + cle PEM (signe par sa propre CA ou Let's Encrypt)
  - regenerer un nouveau snakeoil (utile si l'ancien est expire)

Apres ecriture, on fait `systemctl reload nginx` (pas restart, pas
d'interruption visible).
"""
from __future__ import annotations

import logging
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.x509.oid import NameOID

from app.apply import APPLY_ENABLED

log = logging.getLogger("muros.tls")

NGINX_SSL_DIR = Path("/etc/nginx/ssl")
CERT_PATH = NGINX_SSL_DIR / "muros.crt"
KEY_PATH = NGINX_SSL_DIR / "muros.key"


def get_status() -> dict:
    """Renvoie les infos du cert actuel (CN, SAN, expiration, fingerprint)."""
    if not CERT_PATH.exists():
        return {
            "present": False,
            "subject_cn": None,
            "issuer_cn": None,
            "san": [],
            "not_before": None,
            "not_after": None,
            "days_remaining": None,
            "fingerprint_sha256": None,
            "is_self_signed": None,
            "key_present": KEY_PATH.exists(),
        }
    try:
        cert_pem = CERT_PATH.read_bytes()
        cert = x509.load_pem_x509_certificate(cert_pem)
    except Exception as exc:  # noqa: BLE001
        return {
            "present": True,
            "subject_cn": None,
            "issuer_cn": None,
            "san": [],
            "not_before": None,
            "not_after": None,
            "days_remaining": None,
            "fingerprint_sha256": None,
            "is_self_signed": None,
            "key_present": KEY_PATH.exists(),
            "error": str(exc),
        }

    def _cn(name) -> str | None:
        for attr in name:
            if attr.oid == NameOID.COMMON_NAME:
                return str(attr.value)
        return None

    subject_cn = _cn(cert.subject)
    issuer_cn = _cn(cert.issuer)

    san_list: list[str] = []
    try:
        san_ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        for name in san_ext.value:
            if isinstance(name, x509.DNSName):
                san_list.append(f"DNS:{name.value}")
            elif isinstance(name, x509.IPAddress):
                san_list.append(f"IP:{name.value}")
            elif isinstance(name, x509.RFC822Name):
                san_list.append(f"email:{name.value}")
    except x509.ExtensionNotFound:
        pass

    not_before = cert.not_valid_before_utc.replace(tzinfo=None)
    not_after = cert.not_valid_after_utc.replace(tzinfo=None)
    days_rem = (cert.not_valid_after_utc - datetime.now(timezone.utc)).days

    fp = cert.fingerprint(__import__("cryptography.hazmat.primitives.hashes", fromlist=["SHA256"]).SHA256())
    fp_hex = ":".join(f"{b:02X}" for b in fp)

    return {
        "present": True,
        "subject_cn": subject_cn,
        "issuer_cn": issuer_cn,
        "san": san_list,
        "not_before": not_before.isoformat() if not_before else None,
        "not_after": not_after.isoformat() if not_after else None,
        "days_remaining": days_rem,
        "fingerprint_sha256": fp_hex,
        "is_self_signed": (subject_cn == issuer_cn),
        "key_present": KEY_PATH.exists(),
    }


def _validate_cert_and_key(cert_pem: str, key_pem: str) -> None:
    """Valide que le PEM est lisible et que la cle correspond au cert."""
    try:
        cert = x509.load_pem_x509_certificate(cert_pem.encode("ascii"))
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Invalid PEM certificate : {exc}") from exc
    try:
        key = serialization.load_pem_private_key(key_pem.encode("ascii"), password=None)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Invalid PEM private key : {exc}") from exc

    # Verif que la cle correspond au cert (cles publiques egales).
    cert_pub = cert.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    key_pub = key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    if cert_pub != key_pub:
        raise ValueError("La cle privee ne correspond pas au certificat fourni.")


def upload_cert(cert_pem: str, key_pem: str) -> dict:
    """Ecrit le cert + cle dans /etc/nginx/ssl/ et reload nginx."""
    _validate_cert_and_key(cert_pem, key_pem)

    if not APPLY_ENABLED:
        return {
            "applied": False,
            "message": "dry-run : cert valide mais pas ecrit (MUROS_APPLY off).",
        }
    if os.geteuid() != 0:
        raise RuntimeError("Ecriture cert impossible : MurOS doit tourner en root.")

    NGINX_SSL_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(NGINX_SSL_DIR, 0o755)

    # Si c'etait un symlink (snakeoil), on le supprime avant d'ecrire.
    if CERT_PATH.is_symlink() or CERT_PATH.exists():
        try:
            CERT_PATH.unlink()
        except OSError:
            pass
    if KEY_PATH.is_symlink() or KEY_PATH.exists():
        try:
            KEY_PATH.unlink()
        except OSError:
            pass

    CERT_PATH.write_text(cert_pem, encoding="utf-8")
    KEY_PATH.write_text(key_pem, encoding="utf-8")
    os.chmod(CERT_PATH, 0o644)
    os.chmod(KEY_PATH, 0o600)

    _reload_nginx()
    return {"applied": True, "message": "Certificat installe et nginx recharge."}


def regenerate_snakeoil(**_kwargs) -> dict:
    """Regenere le couple snakeoil de Debian via make-ssl-cert.

    On delegue a l'outil systeme `make-ssl-cert generate-default-snakeoil`
    plutot que de batir un cert avec cryptography : c'est ce que fait le
    paquet ssl-cert lui-meme, le CN est le hostname courant, la cle reste
    sous /etc/ssl/private avec les bons droits. MurOS se contente d'avoir
    des symlinks /etc/nginx/ssl/muros.{crt,key} qui pointent vers eux.
    """
    import subprocess
    try:
        subprocess.run(
            ["make-ssl-cert", "generate-default-snakeoil", "--force-overwrite"],
            check=True, capture_output=True, timeout=30,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("make-ssl-cert manquant (paquet ssl-cert)") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"make-ssl-cert echec : {exc.stderr.decode()}") from exc

    # nginx reload pour reprendre le nouveau cert (les symlinks pointent
    # deja vers les bons chemins, c'est le sujet du Common Name qui change)
    try:
        subprocess.run(["nginx", "-t"], check=True, capture_output=True, timeout=5)
        subprocess.run(["systemctl", "reload", "nginx"], check=True, capture_output=True, timeout=5)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"reload nginx echec : {exc.stderr.decode()}") from exc

    return {"applied": True, "message": "Cert snakeoil regenere et nginx reloade."}


def _reload_nginx() -> None:
    """Reload nginx pour prendre en compte le nouveau cert."""
    r = subprocess.run(
        ["systemctl", "reload", "nginx.service"],
        capture_output=True, text=True, timeout=15,
    )
    if r.returncode != 0:
        log.warning("systemctl reload nginx : %s", r.stderr)
