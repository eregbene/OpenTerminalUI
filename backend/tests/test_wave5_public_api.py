from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from backend.main import app
from backend.shared.test_db_safety import redirect_shared_db_to_isolated_sqlite

def _init_fresh_db(monkeypatch: pytest.MonkeyPatch):
    # Isolated in-memory sqlite per test -- never touches the shared/application
    # database. See backend/shared/test_db_safety.py (added after the 2026-08-07
    # incident where this file's previous Base.metadata.drop_all(bind=engine) wiped
    # the shared dev database).
    return redirect_shared_db_to_isolated_sqlite(
        monkeypatch,
        extra_engine_sites=("backend.api.routes.public_api", "backend.api.routes.api_keys"),
    )

def test_public_api_key_auth(monkeypatch: pytest.MonkeyPatch):
    SessionLocal = _init_fresh_db(monkeypatch)
    from backend.api.deps import get_db

    def _override_get_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    # setitem (not a plain assignment) so monkeypatch reverts this override at test
    # teardown -- app is a shared module-level singleton, and a leftover override
    # here would leak into whichever test happens to run next in the same session.
    monkeypatch.setitem(app.dependency_overrides, get_db, _override_get_db)
    monkeypatch.setattr(app.state, "db_session_factory", SessionLocal, raising=False)
    client = TestClient(app)

    # 0. Create User
    client.post("/api/auth/register", json={"email": "api@example.com", "password": "password", "role": "trader"})
    login = client.post("/api/auth/login", json={"email": "api@example.com", "password": "password"})
    token = login.json()["access_token"]

    from backend.models.user import User
    db = SessionLocal()
    user = db.query(User).filter(User.email == "api@example.com").first()
    user_id = user.id
    db.close()
    
    # 1. Create API key
    res = client.post("/api/settings/api-keys", json={"name": "Test Key"}, headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    key_data = res.json()
    api_key = key_data["key"]
    
    # 2. Access public API without key
    res = client.get("/api/v1/quote/RELIANCE")
    assert res.status_code == 401
    
    # 3. Access with valid key
    res = client.get("/api/v1/quote/RELIANCE", headers={"X-API-Key": api_key})
    assert res.status_code == 200
    assert "data" in res.json()
    
    # 4. Verify rate limiting (mocked or small limit for test)
    # We won't test full rate limit here but ensure it doesn't crash
    
    # 5. Revoke key
    res = client.delete(f"/api/settings/api-keys/{key_data['id']}", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    
    # 6. Access with revoked key
    res = client.get("/api/v1/quote/RELIANCE", headers={"X-API-Key": api_key})
    assert res.status_code == 401
