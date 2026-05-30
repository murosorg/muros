# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""TOTP (RFC 6238) helpers for the web UI two-factor authentication.

Thin wrapper around pyotp + qrcode. The shared secret is stored per user
(users.totp_secret); enrolment shows a QR code (otpauth URI) the operator
scans with any authenticator app, then confirms one code to enable it.
"""
from __future__ import annotations

import io

ISSUER = "MurOS"


def new_secret() -> str:
    import pyotp
    return pyotp.random_base32()


def verify(secret: str | None, code: str | None) -> bool:
    """Validate a 6-digit code against the secret (+/- one 30s window)."""
    if not secret or not code:
        return False
    import pyotp
    cleaned = code.strip().replace(" ", "")
    if not cleaned.isdigit():
        return False
    try:
        return pyotp.TOTP(secret).verify(cleaned, valid_window=1)
    except Exception:
        return False


def provisioning_uri(secret: str, username: str) -> str:
    import pyotp
    return pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name=ISSUER)


def qr_svg(uri: str) -> str:
    """Render the otpauth URI as an inline SVG (no pillow dependency).

    Matches the WireGuard QR rendering (qrcode SVG factory) so the same
    pure-python dependency covers both features.
    """
    import qrcode
    import qrcode.image.svg

    img = qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode()
