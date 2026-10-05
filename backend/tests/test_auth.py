"""Optional API-key auth for state-changing requests (backend/main.py
require_api_key_for_writes; settings in backend/config.py).

Design choice under test: GET endpoints always stay open (read-only
dashboard polling); every POST/PUT/PATCH/DELETE needs `X-API-Key` once
ANTARIKSHA_API_KEY is set. Unset/empty key = local prototype mode (open)."""

import importlib

import pytest

from backend.tests._asgi import request


@pytest.fixture
def auth_db(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.db.DB_PATH", tmp_path / "auth_test.db")
    from backend.db import init_db, insert_brief, insert_event, upsert_object
    init_db()
    upsert_object("1", "ASSET", "satellite", "India", "Tier1", "l1", "l2")
    upsert_object("2", "DEB", "debris", None, None, "l1", "l2")
    event_id = insert_event("1", "2", "collision_risk", "2026-07-21T04:12:00+00:00", 0.05, 7.1,
                            2e-4, "Critical", 600.0)
    insert_brief(event_id, "brief", "maneuver", 0.05, "fallback_template")
    # /api/refresh must never reach the real pipeline in these tests.
    from backend import main
    monkeypatch.setattr(main, "run_full_pipeline", lambda: {"stub": True})
    return event_id


def _set_auth(monkeypatch, key="", required=False):
    from backend import config
    monkeypatch.setattr(config, "API_KEY", key)
    monkeypatch.setattr(config, "AUTH_REQUIRED", required)


def _call(method, path, **kw):
    from backend.main import app
    return request(app, method, path, **kw)


def test_disabled_mode_allows_writes_without_header(auth_db, monkeypatch):
    _set_auth(monkeypatch, key="")
    status, _, body = _call("POST", f"/api/events/{auth_db}/approve")
    assert status == 200 and body["status"] == "approved"
    status, _, body = _call("POST", "/api/refresh")
    assert status == 200 and body["stub"] is True


@pytest.mark.parametrize("path_fmt", [
    "/api/events/{id}/approve", "/api/events/{id}/dismiss", "/api/refresh", "/api/demo/seed",
])
def test_enabled_mode_rejects_missing_key(auth_db, monkeypatch, path_fmt):
    _set_auth(monkeypatch, key="s3cret-key")
    status, headers, body = _call("POST", path_fmt.format(id=auth_db))
    assert status == 401
    assert "X-API-Key" in body["detail"]
    assert headers.get("www-authenticate") == "ApiKey"


def test_enabled_mode_rejects_wrong_key_and_records_nothing(auth_db, monkeypatch):
    from backend.db import decisions_for_event, get_event_with_brief

    _set_auth(monkeypatch, key="s3cret-key")
    status, _, _ = _call("POST", f"/api/events/{auth_db}/approve", headers={"X-API-Key": "wrong"})
    assert status == 401
    status, _, _ = _call("POST", f"/api/events/{auth_db}/approve", headers={"X-API-Key": ""})
    assert status == 401
    assert get_event_with_brief(auth_db)["status"] == "pending"
    assert decisions_for_event(auth_db) == []


def test_enabled_mode_accepts_correct_key(auth_db, monkeypatch):
    _set_auth(monkeypatch, key="s3cret-key")
    status, _, body = _call("POST", f"/api/events/{auth_db}/dismiss",
                            headers={"X-API-Key": "s3cret-key"}, json_body={"reason": "dup"})
    assert status == 200 and body["status"] == "dismissed"
    status, _, _ = _call("POST", "/api/refresh", headers={"X-API-Key": "s3cret-key"})
    assert status == 200


def test_get_endpoints_stay_open_when_enabled(auth_db, monkeypatch):
    _set_auth(monkeypatch, key="s3cret-key")
    for path in ("/api/events", f"/api/events/{auth_db}", f"/api/events/{auth_db}/decisions",
                 "/api/decisions/stats"):
        status, _, _ = _call("GET", path)
        assert status == 200, path


def test_cors_preflight_not_blocked_by_auth(auth_db, monkeypatch):
    _set_auth(monkeypatch, key="s3cret-key")
    status, headers, _ = _call("OPTIONS", f"/api/events/{auth_db}/approve", headers={
        "Origin": "http://localhost:5173",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "x-api-key",
    })
    assert status == 200
    assert headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_auth_required_without_key_fails_closed(auth_db, monkeypatch):
    _set_auth(monkeypatch, key="", required=True)
    status, _, body = _call("POST", f"/api/events/{auth_db}/approve")
    assert status == 503
    assert "ANTARIKSHA_API_KEY" in body["detail"]
    status, _, _ = _call("GET", "/api/events")
    assert status == 200


def test_config_reads_env(monkeypatch):
    from backend import config
    try:
        monkeypatch.setenv("ANTARIKSHA_API_KEY", "  k1  ")
        monkeypatch.setenv("ANTARIKSHA_AUTH_REQUIRED", "true")
        monkeypatch.setenv("ANTARIKSHA_CORS_ORIGINS", "http://a.example, http://b.example ,")
        importlib.reload(config)
        assert config.API_KEY == "k1"
        assert config.AUTH_REQUIRED is True
        assert config.CORS_ORIGINS == ["http://a.example", "http://b.example"]
    finally:
        for name in ("ANTARIKSHA_API_KEY", "ANTARIKSHA_AUTH_REQUIRED", "ANTARIKSHA_CORS_ORIGINS"):
            monkeypatch.delenv(name, raising=False)
        importlib.reload(config)
    assert config.API_KEY == "" and config.AUTH_REQUIRED is False
    assert config.CORS_ORIGINS == ["http://localhost:5173"]


def test_included_translation_router_is_covered_by_auth(auth_db, monkeypatch):
    """Routers mounted with include_router inherit the app-wide write check."""
    _set_auth(monkeypatch, key="s3cret-key")
    status, _, _ = _call("POST", "/api/translation/brief", json_body={"event_id": auth_db, "target_language": "hi"})
    assert status == 401
    status, _, body = _call("GET", "/api/translation/status")
    assert status == 200
