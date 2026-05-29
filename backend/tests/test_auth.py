"""Tests auth : login admin/admin par defaut, change password."""
from fastapi.testclient import TestClient


def test_login_default_admin(tmp_db):
    from app.main import app
    client = TestClient(app)
    r = client.post("/api/auth/login", json={"username": "admin", "password": "muros"})
    assert r.status_code == 200
    data = r.json()
    assert "access_token" in data


def test_login_bad_password(tmp_db):
    from app.main import app
    client = TestClient(app)
    r = client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
    assert r.status_code in (401, 400)


def test_change_password_requires_current(tmp_db):
    from app.main import app
    client = TestClient(app)
    # Login
    r = client.post("/api/auth/login", json={"username": "admin", "password": "muros"})
    token = r.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    # Mauvais mdp actuel = refuse
    r2 = client.post("/api/auth/change-password", headers=headers, json={
        "current_password": "WRONG", "new_password": "NewSecure123!",
    })
    assert r2.status_code in (400, 401)
