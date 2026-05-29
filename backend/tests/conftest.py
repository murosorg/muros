"""Fixtures pytest partagees.

Les env MUROS_DB et MUROS_SECRET_FILE sont fixees AVANT tout import app pour
que lengine SQLAlchemy pointe sur une DB temporaire.
"""
import os
import tempfile

import pytest

# Repertoire dedie pour les artefacts de test, vide au depart.
_TEST_DIR = tempfile.mkdtemp(prefix="muros-test-")
os.environ["MUROS_DB"] = os.path.join(_TEST_DIR, "muros-test.db")
os.environ["MUROS_SECRET_FILE"] = os.path.join(_TEST_DIR, "muros-test.key")
os.environ["MUROS_APPLY"] = "0"


@pytest.fixture(scope="session", autouse=True)
def _init_db():
    """Initialise la DB de test et seed admin admin/admin."""
    from app import db
    from app.seed import seed_admin_user
    db.init_db()
    with db.SessionLocal() as s:
        seed_admin_user(s)
    yield


@pytest.fixture(scope="function")
def tmp_db():
    """Donne acces a la SessionLocal pour les tests."""
    from app import db
    return db.SessionLocal
