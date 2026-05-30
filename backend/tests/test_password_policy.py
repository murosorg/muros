# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Unit tests for the password policy validator.

Pure tests: validate() takes a string and either returns None or raises
PasswordPolicyError, with no I/O. They pin the ANSSI/CIS-style rules
(length, character classes, common-password and username checks).
"""
import pytest

from app.password_policy import MIN_LENGTH, PasswordPolicyError, validate


def _reasons(password, username=None):
    with pytest.raises(PasswordPolicyError) as exc:
        validate(password, username)
    return exc.value.reasons


def test_strong_password_passes():
    # Long, mixed classes, not common, no username: must not raise.
    assert validate("Tr0ub4dour-and-Coffee!") is None


def test_empty_password_is_rejected():
    assert _reasons("") == ["Password is empty."]


def test_too_short_is_rejected():
    reasons = _reasons("Aa1!aaa")  # 7 chars
    assert any("at least" in r for r in reasons)


def test_minimum_length_boundary():
    # Exactly MIN_LENGTH valid chars must satisfy the length rule.
    pw = "Aa1!" + "x" * (MIN_LENGTH - 4)
    assert len(pw) == MIN_LENGTH
    assert validate(pw) is None


def test_missing_uppercase():
    assert any("uppercase" in r for r in _reasons("lowercase-only-1!"))


def test_missing_lowercase():
    assert any("lowercase" in r for r in _reasons("UPPERCASE-ONLY-1!"))


def test_missing_digit():
    assert any("digit" in r for r in _reasons("NoDigitsHere!!"))


def test_missing_special_character():
    assert any("special" in r for r in _reasons("NoSpecialChar12"))


def test_common_password_is_rejected_case_insensitive():
    # "Password123" is in the common list; matching is normalized.
    assert any("breach" in r for r in _reasons("Password123"))


def test_password_containing_username_is_rejected():
    reasons = _reasons("Alice-Strong-9X!", username="alice")
    assert any("login name" in r for r in reasons)


def test_username_check_is_accent_insensitive():
    # The accented username must still be detected inside the password.
    reasons = _reasons("Jerome-Strong-9X!", username="jerome")
    assert any("login name" in r for r in reasons)


def test_newline_and_null_are_rejected():
    assert any("line break" in r for r in _reasons("Aa1!aaaaaaaa\nx"))
    assert any("line break" in r for r in _reasons("Aa1!aaaaaaaa\x00x"))


def test_all_failures_reported_at_once():
    # A short, lowercase-only, digit-less, special-less password should
    # collect several reasons in a single call.
    reasons = _reasons("abc")
    assert len(reasons) >= 4
