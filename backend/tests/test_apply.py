"""Tests for the nftables apply mechanism with automatic rollback."""


def test_apply_state_idle_at_start(tmp_db):
    from app import apply as apply_mod
    apply_mod.manager = apply_mod.ApplyManager()  # reset
    status = apply_mod.manager.status  # property
    assert status.state == "idle"


def test_apply_then_confirm(tmp_db):
    from app import apply as apply_mod
    apply_mod.manager = apply_mod.ApplyManager()
    status = apply_mod.manager.apply("table inet test {}\n", timeout=10)
    assert status.state == "pending"
    assert status.timeout_seconds == 10
    assert status.expires_at is not None

    confirmed = apply_mod.manager.confirm()
    assert confirmed.state == "committed"


def test_apply_then_rollback(tmp_db):
    from app import apply as apply_mod
    apply_mod.manager = apply_mod.ApplyManager()
    apply_mod.manager.apply("table inet test {}\n", timeout=10)
    rolled = apply_mod.manager.rollback()
    assert rolled.state == "rolled_back"


def test_default_timeout_is_60_seconds():
    from app import apply as apply_mod
    assert apply_mod.DEFAULT_TIMEOUT == 60
