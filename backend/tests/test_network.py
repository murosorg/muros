"""Tests pour le mecanisme dirty flag + network apply."""
from fastapi.testclient import TestClient


def _login(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "muros"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_pending_zero_at_start(tmp_db):
    from app.main import app
    client = TestClient(app)
    h = _login(client)
    r = client.get("/api/network/pending", headers=h)
    assert r.status_code == 200
    assert r.json()["count"] == 0


def test_creating_interface_marks_dirty(tmp_db):
    from app.main import app
    client = TestClient(app)
    h = _login(client)
    # Cree une interface physique fictive
    r = client.post("/api/interfaces", headers=h, json={
        "name": "test0", "type": "physical", "ip_mode": "static",
        "ip_address": "10.99.0.1/24", "enabled": True,
    })
    assert r.status_code == 201, r.text
    # Verif pending count = 1
    r2 = client.get("/api/network/pending", headers=h)
    assert r2.json()["count"] == 1
    assert r2.json()["interfaces"][0]["name"] == "test0"


def test_apply_returns_pending_id(tmp_db, monkeypatch):
    # Avec MUROS_APPLY=0 le apply ne touche pas le noyau mais marque dirty=False
    from app.main import app
    client = TestClient(app)
    h = _login(client)
    client.post("/api/interfaces", headers=h, json={
        "name": "test1", "type": "physical", "ip_mode": "static",
        "ip_address": "10.99.1.1/24", "enabled": True,
    })
    r = client.post("/api/network/apply", headers=h)
    assert r.status_code == 200
    data = r.json()
    assert data["applied"] is True
    assert data["pending_id"] is not None
