"""Admin-configurable screening horizon (backend/settings.py, app_settings):
default, strict validation, ADMINISTRATOR-only change (configure_system),
governance audit, and that each screening run records -- and propagates
with -- the horizon actually used, while historical runs keep theirs."""

import pytest

from backend.tests._asgi import request
from backend.tests._auth_helpers import call, login, make_user

ALLOWED = [24, 48, 72, 96, 120]
URL = "/api/settings/screening-horizon"


@pytest.fixture(autouse=True)
def db(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.db.DB_PATH", tmp_path / "settings_test.db")
    monkeypatch.setattr("backend.config.API_KEY", "")
    monkeypatch.setattr("backend.config.AUTH_REQUIRED", False)
    from backend.db import init_db
    init_db()


def _app():
    from backend.main import app
    return app


def _put(hours):
    return request(_app(), "PUT", URL, headers={"X-Antariksha-Client": "console"}, json_body={"hours": hours})


def _config_audit():
    from backend.db import list_governance_audit
    return [r for r in list_governance_audit(limit=1000) if r["action"] == "config_change"]


# --- default + contract ---------------------------------------------------------------

def test_default_horizon_is_72_when_unset():
    from backend.settings import get_screening_horizon_hours

    assert get_screening_horizon_hours() == 72
    status, _, body = request(_app(), "GET", URL)
    assert status == 200
    assert body == {
        "hours": 72, "allowed": ALLOWED, "default": 72, "step_seconds": 60,
        "updated_at": None, "updated_by": None,
        "description": "The prototype screens propagated trajectories for candidate close approaches "
                       "over this future window.",
    }


@pytest.mark.parametrize("hours", ALLOWED)
def test_each_allowed_value_is_accepted(hours):
    status, _, body = _put(hours)
    assert status == 200
    assert body["hours"] == hours and body["allowed"] == ALLOWED and body["step_seconds"] == 60
    assert request(_app(), "GET", URL)[2]["hours"] == hours
    from backend.settings import get_screening_horizon_hours
    assert get_screening_horizon_hours() == hours


@pytest.mark.parametrize("bad", [0, 36, 200, "72", True, 72.5, 72.0, None, [72], -24])
def test_invalid_values_are_rejected_with_422(bad):
    status, _, body = _put(bad)
    assert status == 422 and body["error"] == "invalid_input"
    assert request(_app(), "GET", URL)[2]["hours"] == 72
    assert _config_audit() == []


def test_missing_or_malformed_body_is_rejected():
    hdrs = {"X-Antariksha-Client": "console"}
    assert request(_app(), "PUT", URL, headers=hdrs, json_body={})[2]["error"] == "invalid_input"
    assert request(_app(), "PUT", URL, headers=hdrs, json_body=[72])[0] == 422
    assert request(_app(), "PUT", URL, headers=hdrs)[0] == 422


# --- RBAC (real session/CSRF path) -------------------------------------------------------

@pytest.mark.real_auth
def test_admin_can_change_other_roles_get_403():
    roles = ("VIEWER", "OPERATOR", "ASSET_MANAGER", "ADMINISTRATOR")
    for role in roles:
        make_user(role.lower().replace("_", "-"), role)
    tokens = {r: login(r.lower().replace("_", "-"))[3] for r in roles}
    for role in roles[:3]:
        status, _, body = call("PUT", URL, tokens[role], json_body={"hours": 24})
        assert status == 403, role
        assert body["required_permission"] == "configure_system"
        # Everyone can view.
        assert call("GET", URL, tokens[role])[0] == 200
    assert call("PUT", URL, None, json_body={"hours": 24})[0] == 401
    assert call("PUT", URL, tokens["ADMINISTRATOR"], csrf=False, json_body={"hours": 24})[0] == 403  # CSRF
    assert _config_audit() == []
    status, _, body = call("PUT", URL, tokens["ADMINISTRATOR"], json_body={"hours": 24})
    assert status == 200 and body["hours"] == 24 and body["updated_by"] == "administrator"


@pytest.mark.real_auth
def test_configure_system_is_admin_only_and_exported_by_me():
    from backend.auth import ROLE_PERMISSIONS

    assert [r for r, p in ROLE_PERMISSIONS.items() if "configure_system" in p] == ["ADMINISTRATOR"]
    make_user("admin-me", "ADMINISTRATOR")
    make_user("viewer-me", "VIEWER")
    assert "configure_system" in call("GET", "/api/auth/me", login("admin-me")[3])[2]["permissions"]
    assert "configure_system" not in call("GET", "/api/auth/me", login("viewer-me")[3])[2]["permissions"]


# --- audit -------------------------------------------------------------------------------

@pytest.mark.real_auth
def test_change_writes_audit_row_with_actor_role_name_old_new():
    make_user("chief-admin", "ADMINISTRATOR")
    token = login("chief-admin")[3]
    assert call("PUT", URL, token, json_body={"hours": 120})[0] == 200
    assert call("PUT", URL, token, json_body={"hours": 48})[0] == 200
    rows = _config_audit()
    assert len(rows) == 2
    latest = rows[0]
    assert latest["actor_username"] == "chief-admin" and latest["actor_role"] == "ADMINISTRATOR"
    assert latest["target_type"] == "setting" and latest["target_id"] == "screening_horizon_hours"
    assert latest["details"] == {"name": "screening_horizon_hours", "old": 120, "new": 48}
    assert rows[1]["details"] == {"name": "screening_horizon_hours", "old": 72, "new": 120}


def test_noop_change_returns_200_without_audit_row():
    status, _, body = _put(72)  # equals the default
    assert status == 200 and body["hours"] == 72 and body["updated_at"] is None
    assert _config_audit() == []
    assert _put(96)[0] == 200
    assert len(_config_audit()) == 1
    status, _, body = _put(96)
    assert status == 200 and body["hours"] == 96
    assert len(_config_audit()) == 1


# --- the horizon actually used by screening ---------------------------------------------

def _capture_propagate(monkeypatch):
    calls = []

    def fake_propagate_all(window_hours=None, step_seconds=None, objects=None, start=None):
        calls.append({"window_hours": window_hours, "step_seconds": step_seconds})
        return [], {}
    monkeypatch.setattr("backend.pipeline.propagate_all", fake_propagate_all)
    return calls


def test_screening_run_uses_and_records_the_active_horizon(monkeypatch):
    from backend.db import get_screening_run
    from backend.pipeline import run_screening_and_briefs

    calls = _capture_propagate(monkeypatch)
    first = run_screening_and_briefs()
    assert calls[-1]["window_hours"] == 72
    assert get_screening_run(first["screening_run_id"])["window_hours"] == 72

    assert _put(24)[0] == 200
    second = run_screening_and_briefs()
    assert calls[-1]["window_hours"] == 24
    run = get_screening_run(second["screening_run_id"])
    assert run["window_hours"] == 24 and run["step_seconds"] == 60
    assert second["window_hours"] == 24
    # The earlier run keeps the horizon it actually used.
    assert get_screening_run(first["screening_run_id"])["window_hours"] == 72


def test_manual_refresh_records_horizon_in_run_and_attempt(monkeypatch):
    from backend import main
    from backend.db import get_refresh_attempt, get_screening_run
    from backend.pipeline import run_screening_and_briefs

    calls = _capture_propagate(monkeypatch)
    monkeypatch.setattr(main, "run_full_pipeline", lambda: run_screening_and_briefs())
    assert _put(48)[0] == 200
    status, _, body = request(_app(), "POST", "/api/refresh", headers={"X-Antariksha-Client": "console"})
    assert status == 200, body
    assert calls[-1]["window_hours"] == 48
    run = get_screening_run(body["screening_run_id"])
    assert run["window_hours"] == 48 and run["trigger"] == "manual"
    assert get_refresh_attempt(body["attempt_id"])["horizon_hours"] == 48


def test_historical_event_provenance_unchanged_after_setting_change(monkeypatch):
    from unittest.mock import patch

    from backend.db import insert_screening_run
    from backend.provenance import get_event_provenance
    from backend.tests.test_provenance import OBJECT_A, OBJECT_B, _collision_event

    run_id = insert_screening_run("2026-07-20T00:00:00+00:00", "live", data_source="CelesTrak",
                                  object_count=2, window_hours=72, step_seconds=60, candidate_pairs=0,
                                  collision_events=1, proximity_events=0)
    event = {**_collision_event(), "screening_run_id": run_id}

    def prov():
        with patch("backend.provenance.get_event_with_brief", return_value=event), \
             patch("backend.provenance.list_objects", return_value=[OBJECT_A, OBJECT_B]), \
             patch("backend.provenance.tle_age_days_for", return_value=0.8):
            return get_event_provenance(event["id"])["propagation"]

    before = prov()
    assert _put(120)[0] == 200
    after = prov()
    assert after == before
    assert after["horizon_hours"] == 72 and after["horizon_source"] == "screening_run"


def test_status_configured_window_is_active_setting_and_run_window_from_latest_run(monkeypatch):
    from backend.pipeline import run_screening_and_briefs

    _capture_propagate(monkeypatch)
    run_screening_and_briefs()
    assert _put(96)[0] == 200
    status, _, body = request(_app(), "GET", "/api/status")
    assert status == 200
    assert body["configured_window_hours"] == 96
    assert body["configured_step_seconds"] == 60
    assert body["screening_window_hours"] == 72  # latest run was screened at 72 h
    assert body["screening_step_seconds"] == 60


def test_demo_run_uses_active_horizon(tmp_path, monkeypatch):
    from backend.tests.test_demo_seed import _make_tle

    import requests

    def _no_ollama(*a, **k):
        raise requests.exceptions.ConnectionError("no ollama in tests")
    monkeypatch.setattr("backend.brief_agent.requests.post", _no_ollama)
    from backend.db import get_screening_run, upsert_object
    a1, a2 = _make_tle(90001, mo_deg=0.0)
    d1, d2 = _make_tle(90002, mo_deg=90.0)
    upsert_object("90001", "TEST-ASSET-SAT", "satellite", "India", "Tier1", a1, a2)
    upsert_object("90002", "TEST-DEBRIS-FRAG", "debris", None, None, d1, d2)
    from backend.tests._registry_helpers import protect
    protect("90001", "TEST-ASSET-SAT")  # Group A via registry resolution, not object type
    assert _put(24)[0] == 200
    from backend.demo_seed import seed_event
    result = seed_event(asset_hint="TEST-ASSET", proximity=False, debris_hint="TEST-DEBRIS")
    from backend.db import get_event_with_brief
    event = get_event_with_brief(result["event_id"])
    run = get_screening_run(event["screening_run_id"])
    assert run["mode"] == "demo" and run["window_hours"] == 24 and run["trigger"] is None
