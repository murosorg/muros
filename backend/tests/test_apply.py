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


def test_iso_utc_tags_naive_datetime_as_utc():
    # Naive UTC datetimes (the MurOS DB convention) must be serialized
    # with an explicit offset, otherwise a non-UTC browser parses them
    # as local time and the rollback countdown collapses to 0s.
    from datetime import datetime, timezone

    from app.rollback import iso_utc

    naive = datetime(2026, 5, 30, 20, 3, 0)
    out = iso_utc(naive)
    assert out is not None
    assert out.endswith("+00:00")
    # Round-trips back to the same instant.
    assert datetime.fromisoformat(out) == naive.replace(tzinfo=timezone.utc)


def test_iso_utc_none_returns_none():
    from app.rollback import iso_utc
    assert iso_utc(None) is None


def test_apply_status_expires_at_is_offset_aware(tmp_db):
    # End-to-end: the public status dict must carry an offset so the UI
    # computes a positive remaining time regardless of client timezone.
    from app import apply as apply_mod
    apply_mod.manager = apply_mod.ApplyManager()
    apply_mod.manager.apply("table inet test {}\n", timeout=60)
    payload = apply_mod.manager.status.to_dict()
    assert payload["expires_at"] is not None
    assert payload["expires_at"].endswith("+00:00")
