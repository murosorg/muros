"""Politique de mot de passe pour MurOS.

Regles ANSSI/CIS minimales :
  - Longueur minimale 12 caracteres
  - Au moins 1 majuscule, 1 minuscule, 1 chiffre, 1 special
  - Refus des mdp les plus communs (top 200 fuites publiques)
  - Refus des mdp qui contiennent le username

Utilise par auth.change_password (UI MurOS) et ssh_config.change_root_password
(compte Linux root).
"""
from __future__ import annotations

import re
import unicodedata

MIN_LENGTH = 12

# Top mots de passe les plus communs (fuites public)
# Source : HaveIBeenPwned, RockYou, top 200 (en lower).
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
    # Pattern d'admin lazy
    "motdepasse", "qwertyuiop", "azerty", "azertyuiop", "admin123",
    "admin1234", "adminadmin", "administrateur", "passw0rd", "P@ssw0rd",
    "P@ssword", "P@ssword1", "Password1", "Password123", "Pa$$w0rd",
    "Welcome123", "Welcome1", "Spring2024", "Summer2024", "Autumn2024",
    "Winter2024", "Spring2025", "Summer2025", "Autumn2025", "Winter2025",
    "Hiver2024", "Hiver2025", "Ete2024", "Ete2025", "Printemps2024",
    "Printemps2025", "Automne2024", "Automne2025",
    # Variantes avec chiffres simples
    "123", "1234", "12", "00000000", "000000", "00000", "abcdef",
    "abcdefgh", "qazwsx", "qazwsxedc", "1q2w3e4r", "1qaz2wsx",
    "1q2w3e4r5t", "qwer1234", "asdf1234",
})


class PasswordPolicyError(ValueError):
    """Exception levee quand le mot de passe ne respecte pas la politique."""
    def __init__(self, reasons: list[str]):
        self.reasons = reasons
        super().__init__(" ; ".join(reasons))


def _normalize(s: str) -> str:
    """Lowercase + retire les accents pour faciliter le matching common."""
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def validate(password: str, username: str | None = None) -> None:
    """Valide un mot de passe. Leve PasswordPolicyError avec la liste des regles non respectees.

    Tous les checks sont fait en une passe pour retourner toutes les raisons d'un coup.
    """
    reasons: list[str] = []

    if not password:
        raise PasswordPolicyError(["Le mot de passe est vide."])

    if len(password) < MIN_LENGTH:
        reasons.append(f"Doit faire au moins {MIN_LENGTH} caracteres (actuellement {len(password)}).")

    if not re.search(r"[A-Z]", password):
        reasons.append("Doit contenir au moins une majuscule (A-Z).")
    if not re.search(r"[a-z]", password):
        reasons.append("Doit contenir au moins une minuscule (a-z).")
    if not re.search(r"[0-9]", password):
        reasons.append("Doit contenir au moins un chiffre (0-9).")
    # Special = tout sauf alphanumeric
    if not re.search(r"[^A-Za-z0-9]", password):
        reasons.append("Doit contenir au moins un caractere special (!@#$%^&* etc.).")

    norm = _normalize(password)
    if norm in COMMON_PASSWORDS or password in COMMON_PASSWORDS:
        reasons.append("Ce mot de passe figure dans les listes de fuites publiques. Choisir un mot de passe unique.")

    if username:
        if _normalize(username) in norm:
            reasons.append("Ne doit pas contenir l'identifiant de l'utilisateur.")

    # Verification anti-newline (au cas ou)
    if "\n" in password or "\r" in password or "\0" in password:
        reasons.append("Ne doit pas contenir de retour a la ligne ou caractere null.")

    if reasons:
        raise PasswordPolicyError(reasons)


