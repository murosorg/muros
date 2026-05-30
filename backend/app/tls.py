# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""TLS certificate management for the web UI (nginx).

The nginx drop-in (`/etc/nginx/sites-available/muros`) points at
`/etc/nginx/ssl/muros.crt` and `/etc/nginx/ssl/muros.key`. At install
these files are symlinks to the ssl-cert snakeoil. The admin can:
  - upload a PEM cert + key (signed by their own CA or Let's Encrypt)
  - regenerate a fresh snakeoil (useful when the old one has expired)

After writing, we run `systemctl reload nginx` (not restart, no visible
interruption).
"""
from __future__ import annotations

import logging
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.x509.oid import NameOID

from app.apply import APPLY_ENABLED

log = logging.getLogger("muros.tls")

NGINX_SSL_DIR = Path("/etc/nginx/ssl")
CERT_PATH = NGINX_SSL_DIR / "muros.crt"
KEY_PATH = NGINX_SSL_DIR / "muros.key"


def get_status() -> dict:
    """Return the current cert info (CN, SAN, expiry, fingerprint)."""
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

    fp = cert.fingerprint(hashes.SHA256())
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
    """Validate that the PEM is readable and the key matches the cert."""
    try:
        cert = x509.load_pem_x509_certificate(cert_pem.encode("ascii"))
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Invalid PEM certificate: {exc}") from exc
    try:
        key = serialization.load_pem_private_key(key_pem.encode("ascii"), password=None)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Invalid PEM private key: {exc}") from exc

    # Check the key matches the cert (equal public keys).
    cert_pub = cert.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    key_pub = key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    if cert_pub != key_pub:
        raise ValueError("The private key does not match the provided certificate.")


def upload_cert(cert_pem: str, key_pem: str) -> dict:
    """Write the cert + key to /etc/nginx/ssl/ and reload nginx."""
    _validate_cert_and_key(cert_pem, key_pem)

    if not APPLY_ENABLED:
        return {
            "applied": False,
            "message": "dry-run: cert is valid but not written (MUROS_APPLY off).",
        }
    if os.geteuid() != 0:
        raise RuntimeError("Cannot write cert: MurOS must run as root.")

    NGINX_SSL_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(NGINX_SSL_DIR, 0o755)

    # If it was a symlink (snakeoil), remove it before writing.
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
    return {"applied": True, "message": "Certificate installed and nginx reloaded."}


def regenerate_snakeoil(**_kwargs) -> dict:
    """Regenerate Debian's snakeoil cert/key pair via make-ssl-cert.

    We delegate to the system tool `make-ssl-cert generate-default-snakeoil`
    rather than building a cert with cryptography: this is what the ssl-cert
    package itself does, the CN is the current hostname, and the key stays
    under /etc/ssl/private with the right permissions. MurOS only keeps the
    /etc/nginx/ssl/muros.{crt,key} symlinks pointing at them.
    """
    import subprocess
    try:
        subprocess.run(
            ["make-ssl-cert", "generate-default-snakeoil", "--force-overwrite"],
            check=True, capture_output=True, timeout=30,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("make-ssl-cert missing (ssl-cert package)") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"make-ssl-cert failed: {exc.stderr.decode()}") from exc

    # Reload nginx to pick up the new cert (the symlinks already point at
    # the right paths; only the Common Name subject changes).
    try:
        subprocess.run(["nginx", "-t"], check=True, capture_output=True, timeout=5)
        subprocess.run(["systemctl", "reload", "nginx"], check=True, capture_output=True, timeout=5)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"nginx reload failed: {exc.stderr.decode()}") from exc

    return {"applied": True, "message": "Snakeoil cert regenerated and nginx reloaded."}


def _reload_nginx() -> None:
    """Reload nginx so it picks up the new cert."""
    r = subprocess.run(
        ["systemctl", "reload", "nginx.service"],
        capture_output=True, text=True, timeout=15,
    )
    if r.returncode != 0:
        log.warning("systemctl reload nginx : %s", r.stderr)
