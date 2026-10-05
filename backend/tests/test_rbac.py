"""Prototype RBAC enforced by the backend (backend/auth.py): login/logout/me,
sessions (expiry, revocation, inactive users), CSRF header, role permission
matrix, identity-aware audit, user administration, bootstrap and the
route-guard invariant. All against throwaway DBs (conftest)."""

import csv
import io
import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from backend.tests._asgi import request
from backend.tests._auth_helpers import CSRF, PASSWORD, call, cookie_from, login, make_user

pytestmark = pytest.mark.real_auth

TLE = ("1 25544U 98067A   26275.50000000  .00016717  00000-0  10270-3 0  9005",
       "2 25544  51.6416 247.4627 0006703 130.5360 325.0288 15.72125391563537")
ROLES = ("VIEWER", "OPERATOR", "ASSET_MANAGER", "ADMINISTRATOR")


def _obj(norad_id, name, object_type="debris", criticality=None, owner=None):
    return {"norad_id": norad_id, "name": name, "object_type": object_type, "owner_country": owner,
            "criticality": criticality, "tle_line1": TLE[0], "tle_line2": TLE[1], "source_format": "tle"}


@pytest.fixture
def ws(tmp_path, monkeypatch):
    path = tmp_path / "ws.json"
    path.write_text(json.dumps({"group_a": [
        {"name_query": "ASSET-1", "criticality": "Tier1", "note": "Strategic comms"},
        {"name_query": "ASSET-2", "exact_match": "ASSET-2", "criticality": "Tier3", "note": "Civil EO"},
    ], "group_b_debris_groups": [], "group_c": []}), encoding="utf-8")
    for target in ("backend.config.WORKING_SET_PATH", "backend.ingest.WORKING_SET_PATH",
                   "backend.catalog_view.WORKING_SET_PATH"):
        monkeypatch.setattr(target, path)
    return path


@pytest.fixture
def env(ws, monkeypatch):
    """Users of every role, one event with a brief, one new-to-catalog object."""
    from backend import config, main
    from backend.db import init_db, insert_brief, insert_event, reconcile_catalog

    monkeypatch.setattr(config, "API_KEY", "")
    monkeypatch.setattr(config, "AUTH_REQUIRED", False)
    init_db()
    base = [_obj("1", "ASSET-1", "satellite", "Tier1", "India"), _obj("2", "DEB-2")]
    reconcile_catalog(base)
    reconcile_catalog(base + [_obj("3", "DEB-3")])
    event_id = insert_event("1", "2", "collision_risk", "2026-07-21T04:12:00+00:00", 0.05, 7.1,
                            2e-4, "Critical", 600.0)
    insert_brief(event_id, "brief", "maneuver", 0.05, "fallback_template")
    monkeypatch.setattr(main, "run_full_pipeline", lambda: {"stub": True})
    monkeypatch.setattr("backend.demo_seed.seed_event", lambda **kw: {"event_id": event_id, "stub": True})
    users = {role: make_user(role.lower().replace("_", "-"), role) for role in ROLES}
    return {"event_id": event_id, "users": users}


def _token(role):
    status, _, _, token = login(role.lower().replace("_", "-"))
    assert status == 200
    return token


def _governance(action=None):
    from backend.db import list_governance_audit
    rows = list_governance_audit(limit=1000)
    return [r for r in rows if action is None or r["action"] == action]


# --- password hashing ---------------------------------------------------------------

def test_password_hash_format_and_verify():
    from backend.auth import hash_password, verify_password

    h = hash_password("a-long-password")
    scheme, iterations, salt, digest = h.split("$")
    assert scheme == "pbkdf2_sha256" and int(iterations) >= 200_000 and salt and digest
    assert verify_password("a-long-password", h) and not verify_password("a-long-passworD", h)
    assert hash_password("a-long-password") != h  # random salt
    assert not verify_password("x", "garbage") and not verify_password("x", None)
    with pytest.raises(ValueError):
        hash_password("a-long-password", iterations=1000)


def test_no_default_credentials_exist(ws):
    from backend.db import count_users, init_db

    init_db()
    assert count_users() == 0
    for user, pw in (("admin", "admin"), ("admin", "password"), ("root", "root")):
        status, _, body, _ = login(user, pw)
        assert status == 401 and body["error"] == "invalid_credentials"


def test_password_policy_and_username_rules(ws):
    from backend.auth import ValidationFailed, create_user_account

    with pytest.raises(ValidationFailed):
        create_user_account("shorty", "VIEWER", "123456789")  # 9 chars
    with pytest.raises(ValidationFailed):
        create_user_account("bad name", "VIEWER", PASSWORD)
    with pytest.raises(ValidationFailed):
        create_user_account("okname", "SUPERUSER", PASSWORD)


# --- login / logout / me -------------------------------------------------------------

def test_login_success_contract_cookie_and_hashed_session(env):
    from backend.db import get_connection

    status, headers, body, token = login("operator")
    assert status == 200
    assert body["user"] == {"id": env["users"]["OPERATOR"]["id"], "username": "operator",
                            "display_name": None, "role": "OPERATOR"}
    assert body["permissions"] == ["view", "translate", "view_audit", "refresh", "demo", "decide",
                                   "review_objects"]
    assert datetime.fromisoformat(body["expires_at"]) > datetime.now(timezone.utc) + timedelta(hours=11)
    assert "password" not in json.dumps(body)
    cookie = headers["set-cookie"].lower()
    assert cookie.startswith("antariksha_session=")
    assert "httponly" in cookie and "samesite=lax" in cookie and "path=/" in cookie
    assert "max-age=43200" in cookie and "secure" not in cookie
    conn = get_connection()
    try:
        rows = [dict(r) for r in conn.execute("SELECT * FROM sessions").fetchall()]
    finally:
        conn.close()
    assert len(rows) == 1 and rows[0]["token_hash"] != token and len(rows[0]["token_hash"]) == 64
    assert token not in json.dumps(rows)
    [audit] = _governance("login_success")
    assert audit["actor_username"] == "operator" and audit["actor_role"] == "OPERATOR"


def test_cookie_secure_and_lifetime_follow_config(env, monkeypatch):
    monkeypatch.setattr("backend.config.COOKIE_SECURE", True)
    monkeypatch.setattr("backend.config.SESSION_HOURS", 2)
    status, headers, _, _ = login("viewer")
    assert status == 200
    cookie = headers["set-cookie"].lower()
    assert "secure" in cookie and "max-age=7200" in cookie


def test_login_username_is_case_insensitive(env):
    status, _, body, _ = login("OpErAtOr")
    assert status == 200 and body["user"]["username"] == "operator"


def test_invalid_credentials_same_message_for_unknown_user(env):
    s1, h1, b1, _ = login("operator", "wrong-password-123")
    s2, h2, b2, _ = login("nobody-here", "wrong-password-123")
    assert s1 == s2 == 401
    assert b1 == b2 == {"detail": "Invalid username or password.", "error": "invalid_credentials"}
    assert "set-cookie" not in h1 and "set-cookie" not in h2
    failures = _governance("login_failure")
    assert {f["actor_username"] for f in failures} == {"operator", "nobody-here"}
    assert "wrong-password-123" not in json.dumps(failures)


def test_login_rejects_disallowed_origin_and_accepts_configured_one(env):
    status, _, body, _ = login("operator", headers={"Origin": "http://evil.example"})
    assert status == 403 and body["error"] == "origin_not_allowed"
    status, _, _, _ = login("operator", headers={"Origin": "http://localhost:5173"})
    assert status == 200


def test_login_does_not_require_csrf_header(env):
    status, _, _, token = login("viewer")  # helper sends no CSRF header
    assert status == 200 and token


def test_me_returns_session_shape_or_401(env):
    status, _, body = call("GET", "/api/auth/me")
    assert status == 401 and body == {"detail": "Authentication required.", "error": "not_authenticated"}
    _, _, login_body, token = login("asset-manager")
    status, _, body = call("GET", "/api/auth/me", token)
    assert status == 200
    assert body["user"]["role"] == "ASSET_MANAGER" and "manage_assets" in body["permissions"]
    assert body["expires_at"] == login_body["expires_at"]


def test_logout_revokes_session_and_clears_cookie(env):
    token = _token("OPERATOR")
    status, headers, body = call("POST", "/api/auth/logout", token, csrf=False)
    assert status == 200 and body == {"ok": True}
    assert "antariksha_session=" in headers["set-cookie"] and "max-age=0" in headers["set-cookie"].lower()
    status, _, _ = call("GET", "/api/events", token)
    assert status == 401
    assert len(_governance("logout")) == 1
    # Already logged out / no cookie at all: still fine.
    assert call("POST", "/api/auth/logout", token)[0] == 200
    assert call("POST", "/api/auth/logout")[0] == 200


def test_expired_session_is_rejected(env):
    from backend.db import get_connection

    token = _token("OPERATOR")
    assert call("GET", "/api/events", token)[0] == 200
    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    conn = get_connection()
    conn.execute("UPDATE sessions SET expires_at = ?", (past,))
    conn.commit()
    conn.close()
    status, _, body = call("GET", "/api/events", token)
    assert status == 401 and body["error"] == "not_authenticated"


def test_unknown_or_garbage_token_is_rejected(env):
    assert call("GET", "/api/events", "not-a-real-token")[0] == 401
    assert call("GET", "/api/events", "x" * 1000)[0] == 401


def test_inactive_user_cannot_login_and_loses_access_immediately(env):
    from backend.db import update_user

    token = _token("OPERATOR")
    assert call("GET", "/api/events", token)[0] == 200
    update_user(env["users"]["OPERATOR"]["id"], None, active=False)
    assert call("GET", "/api/events", token)[0] == 401
    status, _, body, _ = login("operator")
    assert status == 401 and body["detail"] == "Invalid username or password."


def test_role_change_applies_immediately(env):
    from backend.db import get_connection

    token = _token("OPERATOR")
    eid = env["event_id"]
    conn = get_connection()
    conn.execute("UPDATE users SET role = 'VIEWER' WHERE username = 'operator'")
    conn.commit()
    conn.close()
    status, _, body = call("POST", f"/api/events/{eid}/approve", token)
    assert status == 403 and body["required_permission"] == "decide"


# --- unauthenticated / CSRF ------------------------------------------------------------

@pytest.mark.parametrize("method,path", [
    ("GET", "/api/events"), ("GET", "/api/objects"), ("GET", "/api/status"), ("GET", "/api/catalog/registry"),
    ("GET", "/api/decisions/export"), ("GET", "/api/translation/status"), ("POST", "/api/refresh"),
    ("POST", "/api/demo/seed"), ("POST", "/api/events/1/approve"), ("GET", "/api/admin/users"),
    ("POST", "/api/protected-assets"), ("GET", "/api/protected-assets/audit"),
])
def test_unauthenticated_requests_get_401(env, method, path):
    status, _, body = call(method, path, json_body={} if method == "POST" else None)
    assert status == 401
    assert body == {"detail": "Authentication required.", "error": "not_authenticated"}


def test_health_is_public(env, monkeypatch):
    monkeypatch.setattr("backend.main._ollama_reachable", lambda: False)
    status, _, body = call("GET", "/api/health")
    assert status == 200 and body["ok"] is True


def test_csrf_header_required_on_mutations(env):
    token = _token("ADMINISTRATOR")
    eid = env["event_id"]
    for method, path, body in (("POST", f"/api/events/{eid}/approve", None), ("POST", "/api/refresh", None),
                               ("POST", "/api/protected-assets", {"name_query": "X-1", "criticality": "Tier1"}),
                               ("PATCH", "/api/admin/users/1", {"display_name": "x"})):
        status, _, resp = call(method, path, token, csrf=False, json_body=body)
        assert status == 403 and resp["error"] == "csrf", path
        status, _, _ = call(method, path, token, csrf=False, json_body=body,
                            headers={"X-Antariksha-Client": "other"})
        assert status == 403, path
    from backend.db import decisions_for_event
    assert decisions_for_event(eid) == []
    # GET needs no CSRF header.
    assert call("GET", "/api/events", token, csrf=False)[0] == 200


def test_cors_preflight_allows_credentials_for_configured_origin(env):
    from backend.main import app

    status, headers, _ = request(app, "OPTIONS", "/api/refresh", headers={
        "Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "x-antariksha-client,content-type"})
    assert status == 200
    assert headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert headers.get("access-control-allow-credentials") == "true"
    status, headers, _ = request(app, "OPTIONS", "/api/refresh", headers={
        "Origin": "http://evil.example", "Access-Control-Request-Method": "POST"})
    assert headers.get("access-control-allow-origin") != "http://evil.example"


# --- permission matrix -------------------------------------------------------------------

MIN_ROLE = {
    "refresh": "OPERATOR", "demo": "OPERATOR", "decide": "OPERATOR", "dismiss": "OPERATOR",
    "review": "OPERATOR", "asset_create": "ASSET_MANAGER", "users_list": "ADMINISTRATOR",
    "user_create": "ADMINISTRATOR", "admin_audit": "ADMINISTRATOR", "asset_audit": "VIEWER",
    "translate": "VIEWER",
}
PERMISSION = {
    "refresh": "refresh", "demo": "demo", "decide": "decide", "dismiss": "decide", "review": "review_objects",
    "asset_create": "manage_assets", "users_list": "manage_users", "user_create": "manage_users",
    "admin_audit": "view_admin_audit", "asset_audit": "view_audit", "translate": "translate",
}


def _action_request(action, role, eid):
    suffix = role.lower().replace("_", "-")
    return {
        "refresh": ("POST", "/api/refresh", None),
        "demo": ("POST", "/api/demo/seed?mode=collision", None),
        "decide": ("POST", f"/api/events/{eid}/approve", None),
        "dismiss": ("POST", f"/api/events/{eid}/dismiss", {"reason": "dup"}),
        "review": ("POST", "/api/catalog/new-objects/3/review", {"note": "ok"}),
        "asset_create": ("POST", "/api/protected-assets", {"name_query": f"NEW-{suffix}", "criticality": "Tier2"}),
        "users_list": ("GET", "/api/admin/users", None),
        "user_create": ("POST", "/api/admin/users", {"username": f"made-by-{suffix}", "role": "VIEWER",
                                                     "password": PASSWORD}),
        "admin_audit": ("GET", "/api/admin/audit", None),
        "asset_audit": ("GET", "/api/protected-assets/audit", None),
        "translate": ("POST", "/api/translation/brief", {"target_language": "en", "brief_text": "hello"}),
    }[action]


@pytest.mark.parametrize("action", sorted(MIN_ROLE))
def test_permission_matrix(env, action):
    eid = env["event_id"]
    tokens = {role: _token(role) for role in ROLES}
    method, path, body = _action_request(action, "anon", eid)
    assert call(method, path, None, json_body=body)[0] == 401
    min_index = ROLES.index(MIN_ROLE[action])
    for i, role in enumerate(ROLES):
        method, path, body = _action_request(action, role, eid)
        status, _, resp = call(method, path, tokens[role], json_body=body)
        if i < min_index:
            assert status == 403, (action, role, resp)
            assert resp == {"detail": f"Your role ({role}) does not permit this action.", "error": "forbidden",
                            "required_permission": PERMISSION[action]}
        else:
            assert 200 <= status < 300, (action, role, status, resp)


def test_forbidden_mutation_records_nothing(env):
    from backend.db import decisions_for_event, get_event_with_brief, list_protected_assets

    token = _token("VIEWER")
    eid = env["event_id"]
    assert call("POST", f"/api/events/{eid}/approve", token)[0] == 403
    assert decisions_for_event(eid) == [] and get_event_with_brief(eid)["status"] == "pending"
    before = list_protected_assets()
    assert call("POST", "/api/protected-assets", token, json_body={"name_query": "Z-9", "criticality": "Tier1"})[0] == 403
    assert list_protected_assets() == before


# --- identity-aware audit ------------------------------------------------------------------

def test_decision_actor_comes_from_session_not_body(env):
    from backend.db import get_connection

    token = _token("OPERATOR")
    eid = env["event_id"]
    status, _, _ = call("POST", f"/api/events/{eid}/dismiss", token,
                        json_body={"reason": "dup", "actor_username": "mallory", "actor_role": "ADMINISTRATOR"})
    assert status == 200
    status, _, history = call("GET", f"/api/events/{eid}/decisions", token)
    assert status == 200
    assert history[0]["actor_username"] == "operator" and history[0]["actor_role"] == "OPERATOR"
    conn = get_connection()
    row = dict(conn.execute("SELECT * FROM decision_log").fetchone())
    conn.close()
    assert row["actor_user_id"] == env["users"]["OPERATOR"]["id"]
    status, _, text = call("GET", "/api/decisions/export", token)
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0][-2:] == ["actor_username", "actor_role"] and rows[1][-2:] == ["operator", "OPERATOR"]


def test_object_review_records_actor(env):
    token = _token("ASSET_MANAGER")
    status, _, body = call("POST", "/api/catalog/new-objects/3/review", token, json_body={"note": "seen"})
    assert status == 200
    assert body["actor_username"] == "asset-manager" and body["actor_role"] == "ASSET_MANAGER"
    assert body["actor_user_id"] == env["users"]["ASSET_MANAGER"]["id"]


def test_legacy_audit_rows_migrate_with_null_actor_and_stay_intact(tmp_path, monkeypatch, ws):
    db_file = tmp_path / "legacy_audit.db"
    monkeypatch.setattr("backend.db.DB_PATH", db_file)
    conn = sqlite3.connect(db_file)
    conn.executescript("""
        CREATE TABLE decision_log (id INTEGER PRIMARY KEY AUTOINCREMENT, event_class TEXT NOT NULL,
          object_a_name TEXT, object_b_name TEXT, risk_tier TEXT, pc_score REAL, miss_distance_km REAL,
          generated_by TEXT, decision TEXT NOT NULL, rejection_reason TEXT, decided_at TEXT NOT NULL,
          event_id INTEGER, is_demo INTEGER, screening_run_id INTEGER, tca_timestamp TEXT);
        INSERT INTO decision_log (event_class, object_a_name, object_b_name, risk_tier, pc_score,
          miss_distance_km, generated_by, decision, rejection_reason, decided_at, event_id)
          VALUES ('collision_risk', 'A', 'B', 'High', 1e-4, 0.5, 'llm', 'dismissed', 'old reason',
                  '2026-01-01T00:00:00+00:00', 42);
        CREATE TABLE object_reviews (id INTEGER PRIMARY KEY AUTOINCREMENT, norad_id TEXT NOT NULL,
          reviewed_at TEXT NOT NULL, note TEXT);
        INSERT INTO object_reviews (norad_id, reviewed_at, note) VALUES ('77', '2026-01-02T00:00:00+00:00', 'legacy');
    """)
    conn.commit()
    conn.close()
    from backend.db import decisions_for_event, init_db, insert_decision

    init_db()
    init_db()
    [legacy] = decisions_for_event(42)
    assert legacy["rejection_reason"] == "old reason" and legacy["decided_at"] == "2026-01-01T00:00:00+00:00"
    assert legacy["actor_username"] is None and legacy["actor_role"] is None and legacy["actor_user_id"] is None
    insert_decision("collision_risk", "A", "B", "High", 1e-4, 0.5, "llm", "approved", event_id=42,
                    actor={"user_id": 5, "username": "op", "role": "OPERATOR"})
    new, old = decisions_for_event(42)
    assert new["actor_username"] == "op" and old == legacy
    conn = sqlite3.connect(db_file)
    review = conn.execute("SELECT norad_id, note, actor_user_id, actor_username, actor_role FROM object_reviews").fetchone()
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert review == ("77", "legacy", None, None, None)
    assert {"users", "sessions", "governance_audit", "protected_assets"} <= tables


def test_governance_audit_is_append_only(env):
    from backend.db import get_connection

    login("operator")
    conn = get_connection()
    try:
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            conn.execute("UPDATE governance_audit SET action = 'x'")
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            conn.execute("DELETE FROM governance_audit")
    finally:
        conn.close()


# --- user administration ---------------------------------------------------------------------

def test_admin_user_listing_never_contains_hashes(env):
    token = _token("ADMINISTRATOR")
    status, _, body = call("GET", "/api/admin/users", token)
    assert status == 200 and len(body["users"]) == 4
    dumped = json.dumps(body)
    assert "password" not in dumped and "pbkdf2" not in dumped
    assert body["role_permissions"]["VIEWER"] == ["view", "translate", "view_audit"]


def test_admin_create_user_validation_and_duplicates(env):
    token = _token("ADMINISTRATOR")
    ok = {"username": "new-op", "role": "OPERATOR", "password": PASSWORD, "display_name": "New Op"}
    status, _, body = call("POST", "/api/admin/users", token, json_body=ok)
    assert status == 201 and body["username"] == "new-op" and body["active"] is True
    assert "password_hash" not in body
    new_id = body["id"]
    status, _, body = call("POST", "/api/admin/users", token, json_body={**ok, "username": "NEW-OP"})
    assert status == 409 and body["error"] == "duplicate"
    for bad in ({**ok, "username": "x2", "password": "short"}, {**ok, "username": "bad name"},
                {**ok, "username": "x3", "role": "ROOT"}, {**ok, "username": "x4", "display_name": "a\nb"}):
        status, _, body = call("POST", "/api/admin/users", token, json_body=bad)
        assert status == 422 and body["error"] == "invalid_input", bad
    status, _, _, _ = login("new-op")
    assert status == 200
    [created] = [r for r in _governance("user_create") if r["target_id"] == str(new_id)]
    assert created["actor_username"] == "administrator" and created["actor_role"] == "ADMINISTRATOR"
    assert created["details"]["after"]["role"] == "OPERATOR" and PASSWORD not in json.dumps(created)


def test_admin_patch_user_and_deactivation_revokes_sessions(env):
    admin = _token("ADMINISTRATOR")
    op_token = _token("OPERATOR")
    op_id = env["users"]["OPERATOR"]["id"]
    status, _, body = call("PATCH", f"/api/admin/users/{op_id}", admin, json_body={"role": "ASSET_MANAGER"})
    assert status == 200 and body["role"] == "ASSET_MANAGER"
    assert call("GET", "/api/auth/me", op_token)[2]["user"]["role"] == "ASSET_MANAGER"
    status, _, body = call("PATCH", f"/api/admin/users/{op_id}", admin, json_body={"active": False})
    assert status == 200 and body["active"] is False
    assert call("GET", "/api/events", op_token)[0] == 401
    status, _, body = call("PATCH", f"/api/admin/users/{op_id}", admin, json_body={"active": True,
                                                                                  "password": "another-secret-1"})
    assert status == 200
    assert login("operator")[0] == 401 and login("operator", "another-secret-1")[0] == 200
    audit = [r for r in _governance("user_update") if r["target_id"] == str(op_id)]
    assert len(audit) == 3 and all(r["actor_username"] == "administrator" for r in audit)
    assert "another-secret-1" not in json.dumps(audit) and "pbkdf2" not in json.dumps(audit)
    assert call("PATCH", "/api/admin/users/9999", admin, json_body={"role": "VIEWER"})[0] == 404
    assert call("PATCH", f"/api/admin/users/{op_id}", admin, json_body={"password": "short"})[0] == 422


def test_last_admin_cannot_be_demoted_or_deactivated(env):
    from backend.db import LastAdministratorError, update_user

    admin = _token("ADMINISTRATOR")
    admin_id = env["users"]["ADMINISTRATOR"]["id"]
    for change in ({"role": "VIEWER"}, {"active": False}):
        status, _, body = call("PATCH", f"/api/admin/users/{admin_id}", admin, json_body=change)
        assert status == 409 and body["error"] == "last_admin"
    with pytest.raises(LastAdministratorError):
        update_user(admin_id, None, active=False)
    # With a second active administrator the change is allowed.
    make_user("second-admin", "ADMINISTRATOR")
    status, _, body = call("PATCH", f"/api/admin/users/{admin_id}", admin, json_body={"role": "OPERATOR"})
    assert status == 200 and body["role"] == "OPERATOR"


def test_admin_audit_lists_governance_entries(env):
    admin = _token("ADMINISTRATOR")
    status, _, body = call("GET", "/api/admin/audit", admin)
    assert status == 200
    actions = [e["action"] for e in body["entries"]]
    assert "login_success" in actions and "user_create" in actions
    assert all("details" in e for e in body["entries"])


# --- bootstrap + CLI --------------------------------------------------------------------------

def test_startup_without_users_logs_cli_instruction(ws, caplog, monkeypatch):
    from backend.auth import bootstrap_users
    from backend.db import count_users, init_db

    monkeypatch.setattr("backend.config.BOOTSTRAP_ADMIN_USERNAME", "")
    monkeypatch.setattr("backend.config.BOOTSTRAP_ADMIN_PASSWORD", "")
    init_db()
    with caplog.at_level(logging.WARNING):
        assert bootstrap_users() is None
    assert "python -m backend.users create" in caplog.text and count_users() == 0


def test_env_bootstrap_creates_admin_once(ws, caplog, monkeypatch):
    from backend.auth import bootstrap_users
    from backend.db import count_users, init_db, list_users

    secret = "bootstrap-secret-xyz"
    monkeypatch.setattr("backend.config.BOOTSTRAP_ADMIN_USERNAME", "chief")
    monkeypatch.setattr("backend.config.BOOTSTRAP_ADMIN_PASSWORD", secret)
    init_db()
    with caplog.at_level(logging.INFO):
        user = bootstrap_users()
    assert user["role"] == "ADMINISTRATOR" and user["username"] == "chief"
    assert "chief" in caplog.text and secret not in caplog.text
    assert bootstrap_users() is None and count_users() == 1  # users exist: never again
    assert list_users()[0]["created_by"] == "bootstrap"
    assert login("chief", secret)[0] == 200


def test_env_bootstrap_rejects_weak_password(ws, monkeypatch):
    from backend.auth import bootstrap_users
    from backend.db import count_users, init_db

    monkeypatch.setattr("backend.config.BOOTSTRAP_ADMIN_USERNAME", "chief")
    monkeypatch.setattr("backend.config.BOOTSTRAP_ADMIN_PASSWORD", "short")
    init_db()
    assert bootstrap_users() is None and count_users() == 0


def test_startup_hook_seeds_assets_and_bootstraps(ws, monkeypatch):
    from backend import main
    from backend.db import count_users, list_protected_assets

    monkeypatch.setattr("backend.config.BOOTSTRAP_ADMIN_USERNAME", "")
    main._startup()
    assert [a["name_query"] for a in list_protected_assets()] == ["ASSET-1", "ASSET-2"]
    assert count_users() == 0


def test_cli_user_lifecycle_is_audited_as_cli(ws, monkeypatch, capsys):
    from backend import users
    from backend.db import get_user_by_username

    assert users.run(["create", "--username", "ops1", "--role", "OPERATOR", "--display-name", "Ops One",
                      "--password-stdin"], stdin=io.StringIO(PASSWORD + "\n")) == 0
    assert get_user_by_username("ops1")["display_name"] == "Ops One"
    assert login("ops1")[0] == 200
    assert users.run(["list"]) == 0
    out = capsys.readouterr().out
    assert "ops1" in out and "OPERATOR" in out and "pbkdf2" not in out and PASSWORD not in out
    assert users.run(["set-role", "--username", "ops1", "--role", "VIEWER"]) == 0
    assert get_user_by_username("ops1")["role"] == "VIEWER"
    assert users.run(["set-password", "--username", "ops1", "--password-stdin"],
                     stdin=io.StringIO("brand-new-password\n")) == 0
    assert login("ops1")[0] == 401 and login("ops1", "brand-new-password")[0] == 200
    assert users.run(["deactivate", "--username", "ops1"]) == 0
    assert login("ops1", "brand-new-password")[0] == 401
    assert users.run(["activate", "--username", "ops1"]) == 0
    assert login("ops1", "brand-new-password")[0] == 200
    cli_rows = [r for r in _governance() if r["target_type"] == "user" and r["actor_username"] == "cli"]
    assert [r["action"] for r in reversed(cli_rows)] == ["user_create", "user_set_role", "user_set_password",
                                                          "user_deactivate", "user_activate"]
    assert all(r["actor_user_id"] is None for r in cli_rows)


def test_cli_refuses_bad_input(ws, monkeypatch, capsys):
    from backend import users

    assert users.run(["create", "--username", "u1", "--role", "VIEWER", "--password-stdin"],
                     stdin=io.StringIO("short\n")) == 1
    answers = iter(["first-password-1", "second-password-2"])
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(answers))
    assert users.run(["create", "--username", "u1", "--role", "VIEWER"]) == 1
    assert "do not match" in capsys.readouterr().err
    assert users.run(["set-role", "--username", "ghost", "--role", "VIEWER"]) == 1
    assert users.run(["create", "--username", "admin1", "--role", "ADMINISTRATOR", "--password-stdin"],
                     stdin=io.StringIO(PASSWORD + "\n")) == 0
    assert users.run(["deactivate", "--username", "admin1"]) == 1  # last administrator


# --- API-key layer -------------------------------------------------------------------------------

def test_api_key_gate_is_evaluated_before_session(env, monkeypatch):
    from backend import config

    token = _token("OPERATOR")
    eid = env["event_id"]
    monkeypatch.setattr(config, "API_KEY", "deploy-key")
    status, headers, body = call("POST", f"/api/events/{eid}/approve", token)
    assert status == 401 and "X-API-Key" in body["detail"] and headers.get("www-authenticate") == "ApiKey"
    status, _, body = call("POST", f"/api/events/{eid}/approve", None, headers={"X-API-Key": "deploy-key"})
    assert status == 401 and body["error"] == "not_authenticated"  # key alone is not a session
    status, _, body = call("POST", f"/api/events/{eid}/approve", token, headers={"X-API-Key": "deploy-key"})
    assert status == 200 and body["status"] == "approved"
    monkeypatch.setattr(config, "API_KEY", "")
    monkeypatch.setattr(config, "AUTH_REQUIRED", True)
    assert call("POST", f"/api/events/{eid}/dismiss", token)[0] == 503


# --- route guard -----------------------------------------------------------------------------------

def _api_operations():
    """Every (METHOD, path) the app serves under /api, from the generated
    OpenAPI schema (covers routes added with include_router too)."""
    from backend.main import app

    app.openapi_schema = None
    ops = set()
    for path, item in app.openapi()["paths"].items():
        if path.startswith("/api/"):
            ops |= {(m.upper(), path) for m in item if m in ("get", "post", "put", "patch", "delete")}
    return ops


def _as_permissionless_principal(monkeypatch):
    """Override the session with a principal whose role grants nothing, so
    any route protected by require_permission answers 403 with its
    required_permission."""
    from backend.auth import Principal, get_principal
    from backend.main import app

    nobody = Principal(id=None, username="nobody", display_name=None, role="NO_ROLE")
    app.dependency_overrides[get_principal] = lambda: nobody


def _required_permission(method, path):
    from backend.main import app

    concrete = path.replace("{event_id}", "1").replace("{norad_id}", "3").replace("{asset_id}", "1")         .replace("{user_id}", "1")
    status, _, body = request(app, method, concrete, headers=CSRF, json_body={} if method != "GET" else None)
    if status == 403 and isinstance(body, dict) and body.get("error") == "forbidden":
        return body["required_permission"]
    return None


def test_every_mutating_route_declares_a_permission(ws, monkeypatch):
    """Guards future endpoints: any non-GET /api route must be protected by
    require_permission (login/logout are the only exceptions). Checked by
    behaviour: with a role that grants nothing, each must answer 403."""
    # The external-cron endpoint is exempt from session RBAC by design: it is
    # guarded by its own shared secret instead (asserted below) and can only
    # trigger the scheduled refresh.
    from backend.auth import SCHEDULER_ENDPOINT
    from backend.main import app

    exempt = {"/api/auth/login", "/api/auth/logout", SCHEDULER_ENDPOINT}
    _as_permissionless_principal(monkeypatch)
    mutating = sorted(op for op in _api_operations() if op[0] != "GET" and op[1] not in exempt)
    assert len(mutating) >= 12
    unguarded = [op for op in mutating if _required_permission(*op) is None]
    assert unguarded == []
    # Secret-guarded instead: no secret configured -> 503, any session -> irrelevant.
    for method in ("GET", "POST"):
        status, _, body = request(app, method, SCHEDULER_ENDPOINT, headers=CSRF)
        assert status == 503 and body["error"] == "scheduler_disabled"
    monkeypatch.setenv("SCHEDULER_SECRET", "route-guard-secret")
    for method in ("GET", "POST"):
        status, _, body = request(app, method, SCHEDULER_ENDPOINT, headers=CSRF)
        assert status == 401 and body["error"] == "invalid_scheduler_secret"


def test_route_permissions_match_contract(ws, monkeypatch):
    expected = {
        ("POST", "/api/refresh"): "refresh", ("POST", "/api/demo/seed"): "demo",
        ("POST", "/api/events/{event_id}/approve"): "decide",
        ("POST", "/api/events/{event_id}/dismiss"): "decide",
        ("POST", "/api/catalog/new-objects/{norad_id}/review"): "review_objects",
        ("POST", "/api/protected-assets"): "manage_assets",
        ("PATCH", "/api/protected-assets/{asset_id}"): "manage_assets",
        ("POST", "/api/protected-assets/{asset_id}/status"): "manage_assets",
        ("GET", "/api/protected-assets/audit"): "view_audit",
        ("GET", "/api/admin/users"): "manage_users", ("POST", "/api/admin/users"): "manage_users",
        ("PATCH", "/api/admin/users/{user_id}"): "manage_users",
        ("GET", "/api/admin/audit"): "view_admin_audit",
        ("POST", "/api/translation/brief"): "translate",
        ("PUT", "/api/settings/screening-horizon"): "configure_system",
    }
    assert set(expected) <= _api_operations()
    _as_permissionless_principal(monkeypatch)
    assert {op: _required_permission(*op) for op in expected} == expected


def test_brief_agent_does_not_touch_registry_or_user_admin():
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "brief_agent.py").read_text(encoding="utf-8")
    for forbidden in ("protected-assets", "create_protected_asset", "update_protected_asset",
                      "set_protected_asset_status", "/api/admin", "backend.auth", "update_user"):
        assert forbidden not in src
