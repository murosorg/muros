"""PKI integree pour IPsec : CA racine + certificats peers signes.

Utilise la lib cryptography (deja installee pour WireGuard X25519).
Les certs et cles sont stockes en PEM dans la DB SQLite. Au moment
de l'apply, on ecrit les fichiers sur disque :
  - /etc/swanctl/x509ca/muros-ca.pem      (cert CA racine)
  - /etc/swanctl/x509/<name>.pem          (cert peer ou local)
  - /etc/swanctl/private/<name>-key.pem   (cle privee, 0600)
  - /etc/swanctl/x509crl/muros-crl.pem    (CRL des certs revoques)

La cle privee de la CA est stockee en clair dans la DB sqlite (qui est
elle-meme en 0600 sous /var/lib/muros/). C'est le compromis pratique
standard pour une PKI integree d'appliance.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from app.apply import APPLY_ENABLED

log = logging.getLogger("muros.ipsec_pki")

SWANCTL_BASE = Path("/etc/swanctl")
DIR_X509CA = SWANCTL_BASE / "x509ca"
DIR_X509 = SWANCTL_BASE / "x509"
DIR_PRIVATE = SWANCTL_BASE / "private"
DIR_CRL = SWANCTL_BASE / "x509crl"

CA_FILENAME = "muros-ca.pem"
CRL_FILENAME = "muros-crl.pem"

# RSA 4096 pour la CA, 3072 pour les certs (compromis securite/perf).
CA_KEY_BITS = 4096
CERT_KEY_BITS = 3072


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _utcnow_naive() -> datetime:
    return _utcnow().replace(tzinfo=None)


# --- Generation CA ---

def generate_ca(subject_cn: str, subject_o: str, validity_days: int = 3650) -> dict:
    """Generate a new self-signed (key, cert) pair for the root CA."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=CA_KEY_BITS)
    now = _utcnow()
    not_after = now + timedelta(days=validity_days)

    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, subject_cn),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, subject_o),
    ])

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(not_after)
        .add_extension(
            x509.BasicConstraints(ca=True, path_length=None), critical=True,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=False, content_commitment=False,
                key_encipherment=False, data_encipherment=False,
                key_agreement=False, key_cert_sign=True, crl_sign=True,
                encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode("ascii")
    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")

    return {
        "cert_pem": cert_pem,
        "key_pem": key_pem,
        "expires_at": not_after.replace(tzinfo=None),
    }


# --- Generation cert peer signe par la CA ---

def _parse_san(san_str: str | None) -> list[x509.GeneralName]:
    """Parse a string 'DNS:foo.com,IP:1.2.3.4,email:x@y' into a list of SANs."""
    if not san_str:
        return []
    import ipaddress
    result: list[x509.GeneralName] = []
    for raw in san_str.split(","):
        raw = raw.strip()
        if not raw:
            continue
        if ":" in raw:
            kind, val = raw.split(":", 1)
            kind = kind.strip().lower()
            val = val.strip()
        else:
            kind, val = "dns", raw
        if kind == "dns":
            result.append(x509.DNSName(val))
        elif kind == "ip":
            try:
                result.append(x509.IPAddress(ipaddress.ip_address(val)))
            except ValueError:
                log.warning("Ignored invalid SAN IP : %s", val)
        elif kind == "email":
            result.append(x509.RFC822Name(val))
        else:
            log.warning("SAN type inconnu ignore : %s", kind)
    return result


def sign_cert(ca, subject_cn: str, san: str | None,
              validity_days: int = 825, is_local: bool = True) -> dict:
    """Generate a key/cert pair signed by the CA.

    is_local: True = cert usable as the firewall's local identity.
              False = useful to validate a peer (but usually we import the
              remote cert, we do not generate it).
    """
    # Load the CA private key and cert.
    ca_key = serialization.load_pem_private_key(
        ca.key_pem.encode("ascii"), password=None,
    )
    ca_cert = x509.load_pem_x509_certificate(ca.cert_pem.encode("ascii"))

    # Generate the key for this cert.
    key = rsa.generate_private_key(public_exponent=65537, key_size=CERT_KEY_BITS)
    now = _utcnow()
    not_after = now + timedelta(days=validity_days)

    subject = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, subject_cn),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, ca.subject_o),
    ])

    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(not_after)
        .add_extension(
            x509.BasicConstraints(ca=False, path_length=None), critical=True,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, content_commitment=False,
                key_encipherment=True, data_encipherment=False,
                key_agreement=False, key_cert_sign=False, crl_sign=False,
                encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([
                # CLIENT_AUTH + SERVER_AUTH suffisent pour IKEv2.
                # On ajoute aussi les OIDs IPsec end-system et tunnel
                # explicitement (IPSEC_END_SYSTEM/IPSEC_TUNNEL ont ete
                # retires de la lib cryptography recente).
                x509.oid.ExtendedKeyUsageOID.CLIENT_AUTH,
                x509.oid.ExtendedKeyUsageOID.SERVER_AUTH,
                x509.ObjectIdentifier("1.3.6.1.5.5.7.3.5"),  # IPSEC end system
                x509.ObjectIdentifier("1.3.6.1.5.5.7.3.6"),  # IPSEC tunnel
            ]),
            critical=False,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_cert.public_key()),
            critical=False,
        )
    )

    san_names = _parse_san(san)
    if san_names:
        builder = builder.add_extension(
            x509.SubjectAlternativeName(san_names), critical=False,
        )

    cert = builder.sign(ca_key, hashes.SHA256())

    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode("ascii")
    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")

    serial_hex = f"{cert.serial_number:x}"

    return {
        "cert_pem": cert_pem,
        "key_pem": key_pem,
        "serial": serial_hex,
        "expires_at": not_after.replace(tzinfo=None),
    }


def import_remote_cert(name: str, cert_pem: str) -> dict:
    """Valide et importe un certificat distant (en clair PEM).

    Pas de cle privee. Le cert sera utilise pour valider l'identite du peer.
    """
    try:
        cert = x509.load_pem_x509_certificate(cert_pem.strip().encode("ascii"))
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Invalid PEM certificate : {exc}") from exc
    cn = ""
    for attr in cert.subject:
        if attr.oid == NameOID.COMMON_NAME:
            cn = str(attr.value)
            break
    return {
        "cert_pem": cert.public_bytes(serialization.Encoding.PEM).decode("ascii"),
        "subject_cn": cn or name,
        "serial": f"{cert.serial_number:x}",
        "expires_at": cert.not_valid_after_utc.replace(tzinfo=None),
    }


# --- Generation CRL ---

def render_crl(ca, revoked_certs: list) -> str:
    """Genere une CRL signee par la CA contenant les certs revoques.

    revoked_certs : liste d'IpsecCert avec revoked=True.
    """
    ca_key = serialization.load_pem_private_key(
        ca.key_pem.encode("ascii"), password=None,
    )
    ca_cert = x509.load_pem_x509_certificate(ca.cert_pem.encode("ascii"))

    builder = (
        x509.CertificateRevocationListBuilder()
        .issuer_name(ca_cert.subject)
        .last_update(_utcnow())
        .next_update(_utcnow() + timedelta(days=30))
    )

    for c in revoked_certs:
        try:
            serial_int = int(c.serial, 16) if c.serial else 0
        except ValueError:
            continue
        if serial_int == 0:
            continue
        revoked_at = c.revoked_at or _utcnow_naive()
        # Convertit en aware datetime pour x509.
        if revoked_at.tzinfo is None:
            revoked_at = revoked_at.replace(tzinfo=timezone.utc)
        rc = (
            x509.RevokedCertificateBuilder()
            .serial_number(serial_int)
            .revocation_date(revoked_at)
            .build()
        )
        builder = builder.add_revoked_certificate(rc)

    crl = builder.sign(ca_key, hashes.SHA256())
    return crl.public_bytes(serialization.Encoding.PEM).decode("ascii")


# --- Deploiement sur disque ---

def deploy_to_disk(ca, certs: list, revoked_certs: list) -> None:
    """Ecrit la CA, les certs et la CRL dans /etc/swanctl/.

    Crees si manquants : x509ca/, x509/, private/, x509crl/.
    Permissions : 0644 pour les certs publics, 0600 pour les cles privees,
    0700 pour le dossier private/.
    """
    if not APPLY_ENABLED:
        log.info("dry-run : aurait ecrit la PKI dans %s", SWANCTL_BASE)
        return

    for d in (DIR_X509CA, DIR_X509, DIR_PRIVATE, DIR_CRL):
        d.mkdir(parents=True, exist_ok=True)
    os.chmod(DIR_PRIVATE, 0o700)

    # CA
    ca_path = DIR_X509CA / CA_FILENAME
    ca_path.write_text(ca.cert_pem, encoding="utf-8")
    os.chmod(ca_path, 0o644)

    # Certs: we write the local ones (with a private key) and the remote
    # ones (without a private key).
    # First clean up the old certs to avoid leftovers.
    for old in DIR_X509.glob("muros-*.pem"):
        old.unlink()
    for old in DIR_PRIVATE.glob("muros-*-key.pem"):
        old.unlink()

    for c in certs:
        if c.revoked:
            continue
        safe_name = c.name.replace("/", "_")
        cert_path = DIR_X509 / f"muros-{safe_name}.pem"
        cert_path.write_text(c.cert_pem, encoding="utf-8")
        os.chmod(cert_path, 0o644)
        if c.is_local and c.key_pem:
            key_path = DIR_PRIVATE / f"muros-{safe_name}-key.pem"
            key_path.write_text(c.key_pem, encoding="utf-8")
            os.chmod(key_path, 0o600)

    # CRL
    crl_pem = render_crl(ca, revoked_certs)
    crl_path = DIR_CRL / CRL_FILENAME
    crl_path.write_text(crl_pem, encoding="utf-8")
    os.chmod(crl_path, 0o644)


def cert_filename(cert) -> str:
    """Return the .pem file name that will be used in swanctl.conf."""
    safe = cert.name.replace("/", "_")
    return f"muros-{safe}.pem"
