"""Password policy for MurOS.

Minimal ANSSI/CIS rules:
  - Minimum length 12 characters
  - At least 1 uppercase, 1 lowercase, 1 digit, 1 special
  - Reject the most common passwords (top 200 public breaches)
  - Reject passwords that contain the username

Used by auth.change_password (MurOS UI).
"""
from __future__ import annotations

import re
import unicodedata

MIN_LENGTH = 12

# Most common passwords (public breaches)
# Source: HaveIBeenPwned, RockYou, top 200 (lowercased).
COMMON_PASSWORDS = frozenset({
    "123456", "password", "123456789", "12345", "12345678", "qwerty",
    "abc123", "password1", "111111", "1234567", "dragon", "123123",
    "baseball", "iloveyou", "trustno1", "1234567890", "sunshine",
    "master", "123321", "letmein", "welcome", "shadow", "monkey",
    "princess", "qwerty123", "michael", "jordan", "superman", "asdfghjkl",
    "hunter", "buster", "soccer", "harley", "batman", "andrew", "tigger",
    "2000", "charlie", "robert", "thomas",
    "hockey", "ranger", "daniel", "starwars", "klaster", "112233",
    "george", "computer", "michelle", "jessica", "pepper", "1111",
    "zxcvbn", "555555", "11111111", "131313", "freedom", "777777",
    "pass", "maggie", "159753", "aaaaaa", "ginger", "joshua", "cheese", "amanda", "summer", "love", "ashley", "nicole",
    "chelsea", "biteme", "matthew", "access", "yankees", "987654321",
    "dallas", "austin", "thunder", "taylor", "matrix", "mobilemail",
    "mom", "monitor", "monitoring", "montana", "moon", "moscow",
    "admin", "administrator", "root", "toor", "changeme", "default",
    "firewall", "cisco", "pfsense", "opnsense", "linux", "debian",
    "ubuntu", "router", "network", "system", "server", "backup",
    "public", "private", "secret", "test", "demo", "guest",
    "user", "oracle", "mysql", "postgres", "nagios", "zabbix",
    "jpetruzzi", "ecritel", "muros", "murosadmin", "muros2024", "muros2025",
    # Lazy admin patterns
    "motdepasse", "qwertyuiop", "azerty", "azertyuiop", "admin123",
    "admin1234", "adminadmin", "administrateur", "passw0rd", "P@ssw0rd",
    "P@ssword", "P@ssword1", "Password1", "Password123", "Pa$$w0rd",
    "Welcome123", "Welcome1", "Spring2024", "Summer2024", "Autumn2024",
    "Winter2024", "Spring2025", "Summer2025", "Autumn2025", "Winter2025",
    "Hiver2024", "Hiver2025", "Ete2024", "Ete2025", "Printemps2024",
    "Printemps2025", "Automne2024", "Automne2025",
    # Simple numeric variants
    "123", "1234", "12", "00000000", "000000", "00000", "abcdef",
    "abcdefgh", "qazwsx", "qazwsxedc", "1q2w3e4r", "1qaz2wsx",
    "1q2w3e4r5t", "qwer1234", "asdf1234",
})


class PasswordPolicyError(ValueError):
    """Raised when the password does not satisfy the policy."""
    def __init__(self, reasons: list[str]):
        self.reasons = reasons
        super().__init__(" ; ".join(reasons))


def _normalize(s: str) -> str:
    """Lowercase and strip accents to ease matching against the common list."""
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def validate(password: str, username: str | None = None) -> None:
    """Validate a password. Raise PasswordPolicyError with the list of failed rules.

    All checks run in one pass so every reason is reported at once.
    """
    reasons: list[str] = []

    if not password:
        raise PasswordPolicyError(["Password is empty."])

    if len(password) < MIN_LENGTH:
        reasons.append(f"Must be at least {MIN_LENGTH} characters (currently {len(password)}).")

    if not re.search(r"[A-Z]", password):
        reasons.append("Must contain at least one uppercase letter (A-Z).")
    if not re.search(r"[a-z]", password):
        reasons.append("Must contain at least one lowercase letter (a-z).")
    if not re.search(r"[0-9]", password):
        reasons.append("Must contain at least one digit (0-9).")
    # Special = anything non-alphanumeric
    if not re.search(r"[^A-Za-z0-9]", password):
        reasons.append("Must contain at least one special character (!@#$%^&* etc.).")

    norm = _normalize(password)
    if norm in COMMON_PASSWORDS or password in COMMON_PASSWORDS:
        reasons.append("This password appears in public breach lists. Choose a unique password.")

    if username:
        if _normalize(username) in norm:
            reasons.append("Must not contain the user login name.")

    # Anti-newline check (just in case)
    if "\n" in password or "\r" in password or "\0" in password:
        reasons.append("Must not contain a line break or null character.")

    if reasons:
        raise PasswordPolicyError(reasons)


