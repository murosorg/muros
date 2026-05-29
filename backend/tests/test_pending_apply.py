"""Tests pour le mecanisme pending_apply (HTTP/SSH/TLS)."""


def test_default_timeout_is_60_seconds():
    # Aligned with the unified rollback manager and with industry
    # defaults (Cisco IOS-XE 60s, Palo Alto 60s). pending_apply remains
    # DB-backed (it must survive a process restart for SSH/TLS rollback
    # to work) so it is not folded into app.rollback yet.
    from app import pending_apply
    assert pending_apply.DEFAULT_TIMEOUT_SECONDS == 60


def test_safe_apply_default_timeout_is_60_seconds():
    # safe_apply is now a shim that re-exports from app.rollback (unified
    # manager). The unified default timeout was raised from 10s to 60s
    # because nftables + conntrack sync on HA pairs can legitimately take
    # several seconds before the admin gets a chance to confirm.
    from app import safe_apply
    assert safe_apply.DEFAULT_TIMEOUT_SECONDS == 60
