"""Shared pytest fixtures.

The MUROS_DB and MUROS_SECRET_FILE env vars are set BEFORE any app import
so the SQLAlchemy engine points at a temporary database.
"""
import os
import tempfile

import pytest

# Dedicated directory for test artifacts, empty at startup.
_TEST_DIR = tempfile.mkdtemp(prefix="muros-test-")
os.environ["MUROS_DB"] = os.path.join(_TEST_DIR, "muros-test.db")
os.environ["MUROS_SECRET_FILE"] = os.path.join(_TEST_DIR, "muros-test.key")
os.environ["MUROS_APPLY"] = "0"


@pytest.fixture(scope="session", autouse=True)
def _init_db():
    """Initialize the test DB and seed the default root admin row."""
    from app import db
    from app.seed import seed_root_user
    db.init_db()
    with db.SessionLocal() as s:
        seed_root_user(s)
    yield


@pytest.fixture(scope="function", autouse=True)
def _isolate_db():
    """Reset the shared test DB to a clean, seeded state before every test.

    The test database lives for the whole session (see _init_db), so without
    this fixture rows created by one test leak into the next, even across
    files. That coupling caused a real CI failure once: interfaces left
    behind by the lockout-guard tests inflated the network "pending" counter
    asserted in test_network.py, so a green test broke only because of the
    file that ran before it.

    Wiping every table and re-seeding the root admin before each test makes
    the suite order-independent: each test starts from the same known state,
    so a leak in one test can never cascade into another. Deletion runs in
    reverse dependency order so it holds even with SQLite foreign keys on.
    """
    from sqlalchemy import delete

    from app import db
    from app.seed import seed_root_user

    with db.SessionLocal() as s:
        for table in reversed(db.Base.metadata.sorted_tables):
            s.execute(delete(table))
        s.commit()
        seed_root_user(s)
        s.commit()
    yield


@pytest.fixture(scope="function", autouse=True)
def _reset_rollback_state():
    """Reset the in-memory rollback singleton before and after every test.

    The nftables apply path is a thin facade over a process-wide rollback
    manager (app.rollback.manager). Unlike the test database, that singleton
    is never rebuilt per test, so a test that leaves a ticket pending (for
    example an apply without a matching confirm or rollback) leaks an active
    "nftables" ticket into whatever test runs next. Under the randomized
    order enforced by pytest-randomly that surfaced as a real CI failure: a
    later apply test saw the stale ticket and raised "An nftables apply is
    already pending", and the idle-state assertion no longer held.

    The object is shared with app.apply through a bound import reference
    (``from app.rollback import manager as rollback_manager``), so it must be
    mutated in place, not replaced, otherwise app.apply would keep pointing
    at the old instance. Cancelling the pending timers and clearing the
    tickets makes the suite order-independent.
    """
    from app import rollback

    def _clear() -> None:
        with rollback.manager._lock:
            for timer in rollback.manager._timers.values():
                timer.cancel()
            rollback.manager._timers.clear()
            rollback.manager._tickets.clear()

    _clear()
    yield
    _clear()


@pytest.fixture(scope="function")
def tmp_db():
    """Expose SessionLocal to the tests."""
    from app import db
    return db.SessionLocal
