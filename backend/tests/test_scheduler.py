"""Shared refresh pipeline + scheduled refresh (backend/pipeline.py
run_refresh_pipeline, backend/scheduler.py, /api/internal/scheduled-refresh):
secret auth, one shared entry point, refresh_attempts + trigger recording,
last-known-good on failure, overlap protection, too_soon / demo_active
guards, cadence computation, status/health fields."""

import json
import threading
from datetime import datetime, timedelta, timezone

import pytest

from backend.tests._asgi import request
from backend.tests._auth_helpers import call, login, make_user

ENDPOINT = "/api/internal/scheduled-refresh"
SECRET = "test-scheduler-secret-0123456789"


@pytest.fixture(autouse=True)
def db(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.db.DB_PATH", tmp_path / "scheduler_test.db")
    monkeypatch.setattr("backend.config.API_KEY", "")
    monkeypatch.setattr("backend.config.AUTH_REQUIRED", False)
    from backend.db import init_db
    init_db()


def _app():
    from backend.main import app
    return app


def _bearer(secret=SECRET):
    return {"Authorization": f"Bearer {secret}"}


def _stub_full_pipeline(monkeypatch, result=None, exc=None, calls=None):
    """Replace the inner refresh work (ingest + screening) in BOTH places the
    shared entry point may take it from."""
    def fake():
        if calls is not None:
            calls.append(1)
        if exc is not None:
            raise exc
        from backend.pipeline import run_screening_and_briefs
        return result if result is not None else {"stub": True, **run_screening_and_briefs()}
    monkeypatch.setattr("backend.pipeline.run_full_pipeline", fake)
    monkeypatch.setattr("backend.main.run_full_pipeline", fake)
    monkeypatch.setattr("backend.pipeline.propagate_all", lambda **kw: ([], {}))
    return fake


def _join_background():
    from backend import pipeline
    t = pipeline._LAST_BACKGROUND_THREAD
    if t is not None:
        t.join(30)
        assert not t.is_alive()


def _attempts():
    from backend.db import list_refresh_attempts
    return list_refresh_attempts()


# --- endpoint authentication ----------------------------------------------------------

def test_endpoint_503_without_configured_secret(monkeypatch):
    for method in ("GET", "POST"):
        status, _, body = request(_app(), method, ENDPOINT, headers=_bearer())
        assert status == 503 and body["error"] == "scheduler_disabled"
    assert _attempts() == []


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": SECRET},
                                     {"Authorization": f"Basic {SECRET}"}, {"X-API-Key": SECRET}])
def test_endpoint_401_for_wrong_or_missing_secret(monkeypatch, headers):
    monkeypatch.setenv("SCHEDULER_SECRET", SECRET)
    for method in ("GET", "POST"):
        status, _, body = request(_app(), method, ENDPOINT, headers=headers)
        assert status == 401 and body["error"] == "invalid_scheduler_secret"
        assert SECRET not in json.dumps(body)
    assert _attempts() == []


def test_cron_secret_is_accepted_when_scheduler_secret_unset(monkeypatch):
    _stub_full_pipeline(monkeypatch)
    monkeypatch.setenv("CRON_SECRET", "vercel-cron-secret")
    status, _, body = request(_app(), "GET", ENDPOINT + "?wait=true", headers=_bearer("vercel-cron-secret"))
    assert status == 200 and body["status"] == "success" and body["trigger"] == "scheduled"
    monkeypatch.setenv("SCHEDULER_SECRET", SECRET)  # SCHEDULER_SECRET takes precedence
    assert request(_app(), "GET", ENDPOINT, headers=_bearer("vercel-cron-secret"))[0] == 401


def test_correct_secret_returns_202_and_runs_in_background(monkeypatch):
    calls = []
    _stub_full_pipeline(monkeypatch, calls=calls)
    monkeypatch.setenv("SCHEDULER_SECRET", SECRET)
    status, _, body = request(_app(), "POST", ENDPOINT, headers=_bearer())
    assert status == 202
    assert body["status"] == "accepted" and body["trigger"] == "scheduled" and body["attempt_id"]
    _join_background()
    from backend.db import get_refresh_attempt
    attempt = get_refresh_attempt(body["attempt_id"])
    assert attempt["status"] == "success" and attempt["trigger"] == "scheduled" and calls == [1]


def test_correct_secret_returns_200_skipped_when_guard_applies(monkeypatch):
    _stub_full_pipeline(monkeypatch)
    monkeypatch.setenv("SCHEDULER_SECRET", SECRET)
    assert request(_app(), "POST", ENDPOINT + "?wait=true", headers=_bearer())[2]["status"] == "success"
    status, _, body = request(_app(), "POST", ENDPOINT, headers=_bearer())
    assert status == 200 and body == {"status": "skipped", "reason": "too_soon",
                                      "attempt_id": body["attempt_id"], "trigger": "scheduled"}


@pytest.mark.real_auth
def test_endpoint_not_usable_with_session_cookie_alone(monkeypatch):
    calls = []
    _stub_full_pipeline(monkeypatch, calls=calls)
    make_user("ops-admin", "ADMINISTRATOR")
    token = login("ops-admin")[3]
    # No secret configured: a valid admin session still cannot use it.
    assert call("POST", ENDPOINT, token)[0] == 503
    monkeypatch.setenv("SCHEDULER_SECRET", SECRET)
    status, _, body = call("POST", ENDPOINT, token)
    assert status == 401 and body["error"] == "invalid_scheduler_secret"
    assert call("GET", ENDPOINT, token)[0] == 401
    assert calls == []
    # Secret works without any session / CSRF header and without the API key.
    monkeypatch.setattr("backend.config.API_KEY", "deploy-key")
    status, _, body = request(_app(), "POST", ENDPOINT + "?wait=true", headers=_bearer())
    assert status == 200 and body["status"] == "success" and calls == [1]


@pytest.mark.real_auth
def test_secret_does_not_grant_any_other_route(monkeypatch):
    monkeypatch.setenv("SCHEDULER_SECRET", SECRET)
    for method, path in (("GET", "/api/events"), ("POST", "/api/refresh"), ("GET", "/api/status"),
                         ("PUT", "/api/settings/screening-horizon")):
        status, _, _ = request(_app(), method, path, headers={**_bearer(), "X-Antariksha-Client": "console"},
                               json_body={"hours": 24} if method == "PUT" else None)
        assert status == 401, path


# --- one shared pipeline --------------------------------------------------------------

def test_manual_and_scheduled_both_use_run_refresh_pipeline(monkeypatch):
    from backend import pipeline

    _stub_full_pipeline(monkeypatch)
    seen = []
    real = pipeline.run_refresh_pipeline

    def spy(trigger, *args, **kwargs):
        seen.append(trigger)
        return real(trigger, *args, **kwargs)
    monkeypatch.setattr(pipeline, "run_refresh_pipeline", spy)
    monkeypatch.setenv("SCHEDULER_SECRET", SECRET)

    status, _, body = request(_app(), "POST", "/api/refresh", headers={"X-Antariksha-Client": "console"})
    assert status == 200 and body["trigger"] == "manual"
    # Bypass the too_soon guard for the scheduled call.
    monkeypatch.setattr("backend.config.SCHEDULER_MIN_INTERVAL_MINUTES", 0)
    status, _, body = request(_app(), "POST", ENDPOINT + "?wait=true", headers=_bearer())
    assert status == 200 and body["status"] == "success"
    from backend.scheduler import run_scheduled_refresh
    assert run_scheduled_refresh()["status"] == "success"
    assert seen == ["manual", "scheduled", "scheduled"]


def test_trigger_recorded_in_attempts_screening_runs_and_audit(monkeypatch):
    from backend.db import get_screening_run, list_governance_audit
    from backend.pipeline import run_refresh_pipeline

    _stub_full_pipeline(monkeypatch)
    manual = run_refresh_pipeline("manual", actor={"user_id": 7, "username": "op1", "role": "OPERATOR"})
    monkeypatch.setattr("backend.config.SCHEDULER_MIN_INTERVAL_MINUTES", 0)
    scheduled = run_refresh_pipeline("scheduled")
    assert manual["status"] == scheduled["status"] == "success"

    assert get_screening_run(manual["summary"]["screening_run_id"])["trigger"] == "manual"
    assert get_screening_run(scheduled["summary"]["screening_run_id"])["trigger"] == "scheduled"
    by_id = {a["id"]: a for a in _attempts()}
    m, s = by_id[manual["attempt_id"]], by_id[scheduled["attempt_id"]]
    assert (m["trigger"], m["status"], m["actor_username"], m["actor_role"]) == ("manual", "success", "op1",
                                                                                 "OPERATOR")
    assert (s["trigger"], s["status"], s["actor_username"]) == ("scheduled", "success", "scheduler")
    assert m["screening_run_id"] == manual["summary"]["screening_run_id"] and m["horizon_hours"] == 72
    assert m["completed_at"] and s["completed_at"]
    audit = [r for r in list_governance_audit(limit=100) if r["action"] == "refresh"]
    actors = {r["target_id"]: (r["actor_username"], r["details"]["trigger"]) for r in audit}
    assert actors[str(manual["attempt_id"])] == ("op1", "manual")
    assert actors[str(scheduled["attempt_id"])] == ("scheduler", "scheduled")


def test_successful_scheduled_refresh_updates_status(monkeypatch):
    _stub_full_pipeline(monkeypatch)
    from backend.pipeline import run_refresh_pipeline

    result = run_refresh_pipeline("scheduled")
    assert result["status"] == "success"
    body = request(_app(), "GET", "/api/status")[2]
    sch = body["scheduler"]
    assert sch["last_attempt_status"] == "success" and sch["last_attempt_trigger"] == "scheduled"
    assert sch["last_successful_refresh_at"] is not None and sch["running"] is False
    assert sch["last_attempt_at"] == sch["last_successful_refresh_at"]


@pytest.mark.parametrize("exc_factory,expected", [
    (lambda: __import__("backend.ingest", fromlist=["x"]).IngestSafetyError("refused", "group_a_unresolved", 3),
     ("refused", "group_a_unresolved")),
    (lambda: __import__("backend.ingest", fromlist=["x"]).IngestFailedError("fetch failed"),
     ("failed", "ingest_failed")),
    (lambda: KeyError("/secret/path"), ("failed", "internal_error")),
])
def test_failed_scheduled_refresh_preserves_last_known_good(monkeypatch, exc_factory, expected):
    from backend.db import list_events, list_objects, upsert_object
    from backend.pipeline import run_refresh_pipeline
    from backend.tests.test_demo_seed import _make_tle

    l1, l2 = _make_tle(90001)
    upsert_object("90001", "KEEP-SAT", "satellite", "India", "Tier1", l1, l2)
    _stub_full_pipeline(monkeypatch)
    ok = run_refresh_pipeline("scheduled")
    assert ok["status"] == "success"
    before_objects, before_events = list_objects(include_inactive=True), list_events()
    status_before = request(_app(), "GET", "/api/status")[2]["scheduler"]

    monkeypatch.setattr("backend.config.SCHEDULER_MIN_INTERVAL_MINUTES", 0)
    _stub_full_pipeline(monkeypatch, exc=exc_factory())
    failed = run_refresh_pipeline("scheduled")  # never raises for scheduled runs
    assert (failed["status"], failed["reason"]) == expected
    assert list_objects(include_inactive=True) == before_objects
    assert list_events() == before_events
    sch = request(_app(), "GET", "/api/status")[2]["scheduler"]
    assert sch["last_successful_refresh_at"] == status_before["last_successful_refresh_at"]
    assert (sch["last_attempt_status"], sch["last_attempt_reason"]) == expected
    assert sch["last_attempt_at"] is not None and sch["last_attempt_at"] >= status_before["last_attempt_at"]
    assert sch["last_attempt_trigger"] == "scheduled"


def test_manual_failure_keeps_existing_response_shapes_and_records_attempt(monkeypatch):
    from backend.ingest import IngestFailedError, IngestSafetyError

    hdrs = {"X-Antariksha-Client": "console"}
    _stub_full_pipeline(monkeypatch, exc=IngestSafetyError("Only 1 of 4 resolved.", "group_a_unresolved", 9))
    status, _, body = request(_app(), "POST", "/api/refresh", headers=hdrs)
    assert status == 409 and body["error"] == "ingest_refused" and body["ingest_run_id"] == 9
    _stub_full_pipeline(monkeypatch, exc=IngestFailedError("Catalog refresh failed."))
    status, _, body = request(_app(), "POST", "/api/refresh", headers=hdrs)
    assert status == 502 and body["error"] == "ingest_failed"
    _stub_full_pipeline(monkeypatch, exc=RuntimeError("boom"))
    status, _, body = request(_app(), "POST", "/api/refresh", headers=hdrs)
    assert status == 500 and body["error"] == "internal_error"
    got = [(a["trigger"], a["status"], a["reason"]) for a in reversed(_attempts())]
    assert got == [("manual", "refused", "group_a_unresolved"), ("manual", "failed", "ingest_failed"),
                   ("manual", "failed", "internal_error")]
    assert _attempts()[-1]["ingest_run_id"] == 9


# --- overlap ------------------------------------------------------------------------------

def test_overlap_manual_409_and_scheduled_skipped(monkeypatch):
    from backend.pipeline import run_refresh_pipeline

    started, release = threading.Event(), threading.Event()

    def slow():
        started.set()
        assert release.wait(30)
        return {"stub": True}
    monkeypatch.setattr("backend.pipeline.run_full_pipeline", slow)
    monkeypatch.setattr("backend.main.run_full_pipeline", slow)
    result = {}
    t = threading.Thread(target=lambda: result.setdefault("r", run_refresh_pipeline("manual")))
    t.start()
    try:
        assert started.wait(30)
        status, _, body = request(_app(), "POST", "/api/refresh", headers={"X-Antariksha-Client": "console"})
        assert status == 409 and body["error"] == "refresh_in_progress"
        skipped = run_refresh_pipeline("scheduled")
        assert skipped["status"] == "skipped" and skipped["reason"] == "refresh_in_progress"
        assert request(_app(), "GET", "/api/status")[2]["scheduler"]["running"] is True
    finally:
        release.set()
        t.join(30)
    assert result["r"]["status"] == "success"
    from backend.pipeline import refresh_in_progress
    assert refresh_in_progress() is False


# --- scheduled-run guards -------------------------------------------------------------------

def _set_last_success(minutes_ago):
    from backend.db import insert_refresh_attempt
    when = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    insert_refresh_attempt("manual", "success", started_at=when, completed_at=when)


def test_too_soon_guard_skips_scheduled_but_not_manual(monkeypatch):
    from backend.pipeline import run_refresh_pipeline

    calls = []
    _stub_full_pipeline(monkeypatch, calls=calls)
    _set_last_success(100)
    skipped = run_refresh_pipeline("scheduled")
    assert (skipped["status"], skipped["reason"]) == ("skipped", "too_soon") and calls == []
    assert run_refresh_pipeline("manual")["status"] == "success" and calls == [1]


def test_too_soon_guard_allows_after_110_minutes(monkeypatch):
    from backend.pipeline import run_refresh_pipeline

    _stub_full_pipeline(monkeypatch)
    _set_last_success(115)
    assert run_refresh_pipeline("scheduled")["status"] == "success"


def test_too_soon_falls_back_to_last_successful_ingest(monkeypatch):
    from backend.db import insert_ingest_run
    from backend.pipeline import run_refresh_pipeline

    _stub_full_pipeline(monkeypatch)
    insert_ingest_run("CelesTrak", False, 3, {"satellite": 3})
    assert run_refresh_pipeline("scheduled")["reason"] == "too_soon"


def _demo_run(minutes_ago):
    from backend.db import insert_screening_run
    when = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    return insert_screening_run(when, "demo", window_hours=72, step_seconds=60)


def test_demo_active_guard_never_wipes_a_recent_demo(monkeypatch):
    from backend.pipeline import run_refresh_pipeline

    calls = []
    _stub_full_pipeline(monkeypatch, calls=calls)
    _demo_run(30)
    skipped = run_refresh_pipeline("scheduled")
    assert (skipped["status"], skipped["reason"]) == ("skipped", "demo_active") and calls == []
    # An operator's manual refresh is still allowed ("exit demo").
    assert run_refresh_pipeline("manual")["status"] == "success"


def test_demo_guard_expires_after_60_minutes(monkeypatch):
    from backend.pipeline import run_refresh_pipeline

    _stub_full_pipeline(monkeypatch)
    _demo_run(61)
    assert run_refresh_pipeline("scheduled")["status"] == "success"


def test_skipped_attempts_are_recorded_and_audited(monkeypatch):
    from backend.db import list_governance_audit
    from backend.pipeline import run_refresh_pipeline

    _stub_full_pipeline(monkeypatch)
    _demo_run(5)
    result = run_refresh_pipeline("scheduled")
    row = _attempts()[0]
    assert (row["id"], row["status"], row["reason"], row["trigger"]) == (result["attempt_id"], "skipped",
                                                                        "demo_active", "scheduled")
    audit = [r for r in list_governance_audit(limit=10) if r["action"] == "refresh"][0]
    assert audit["actor_username"] == "scheduler" and audit["details"]["status"] == "skipped"


# --- cadence + internal thread --------------------------------------------------------------

@pytest.mark.parametrize("now,expected", [
    ("2026-10-03T09:15:00+00:00", "2026-10-03T10:00:00+00:00"),
    ("2026-10-03T10:00:00+00:00", "2026-10-03T12:00:00+00:00"),
    ("2026-10-03T10:59:59+00:00", "2026-10-03T12:00:00+00:00"),
    ("2026-10-03T11:00:00+00:00", "2026-10-03T12:00:00+00:00"),
    ("2026-10-03T23:30:00+00:00", "2026-10-04T00:00:00+00:00"),
    ("2026-10-03T22:00:00.5+00:00", "2026-10-04T00:00:00+00:00"),
    ("2026-10-03T15:30:00+05:30", "2026-10-03T12:00:00+00:00"),  # 10:00 UTC -> 12:00 UTC
])
def test_next_even_utc_hour(now, expected):
    from backend.scheduler import next_even_utc_hour

    got = next_even_utc_hour(datetime.fromisoformat(now))
    assert got == datetime.fromisoformat(expected)
    assert got.tzinfo is not None and got.minute == 0 and got.hour % 2 == 0


def test_internal_thread_not_started_when_off_or_external(monkeypatch):
    from backend import scheduler

    for mode in ("off", "external"):
        monkeypatch.setattr("backend.config.SCHEDULER_MODE", mode)
        assert scheduler.start_internal_scheduler() is None
        assert scheduler.internal_thread_running() is False
    assert not any(t.name == "antariksha-scheduler" for t in threading.enumerate())


def test_tests_run_with_scheduler_off():
    from backend import config
    from backend.scheduler import scheduler_status

    assert config.SCHEDULER_MODE == "off"
    s = scheduler_status()
    assert s["mode"] == "off" and s["enabled"] is False and s["next_scheduled_at"] is None


def test_internal_thread_starts_and_stops(monkeypatch):
    from backend import scheduler

    monkeypatch.setattr("backend.config.SCHEDULER_MODE", "internal")
    ran = []
    monkeypatch.setattr(scheduler, "run_scheduled_refresh", lambda **kw: ran.append(1) or {})
    try:
        t = scheduler.start_internal_scheduler()
        assert t is not None and t.daemon and scheduler.internal_thread_running()
        assert scheduler.start_internal_scheduler() is t  # idempotent
    finally:
        scheduler.stop_internal_scheduler()
    assert scheduler.internal_thread_running() is False
    assert ran == []  # stopped long before the next even hour


def test_status_scheduler_block_shape(monkeypatch):
    monkeypatch.setattr("backend.config.SCHEDULER_MODE", "internal")
    sch = request(_app(), "GET", "/api/status")[2]["scheduler"]
    assert set(sch) == {"mode", "enabled", "cadence", "cadence_label", "next_scheduled_at",
                        "last_successful_refresh_at", "last_attempt_at", "last_attempt_status",
                        "last_attempt_trigger", "last_attempt_reason", "running"}
    assert sch["mode"] == "internal" and sch["enabled"] is True
    assert sch["cadence"] == "0 */2 * * *" and sch["cadence_label"] == "every 2 hours"
    nxt = datetime.fromisoformat(sch["next_scheduled_at"])
    assert nxt.minute == 0 and nxt.hour % 2 == 0 and nxt > datetime.now(timezone.utc)
    monkeypatch.setattr("backend.config.SCHEDULER_MODE", "external")
    assert request(_app(), "GET", "/api/status")[2]["scheduler"]["enabled"] is False  # no secret
    monkeypatch.setenv("SCHEDULER_SECRET", SECRET)
    assert request(_app(), "GET", "/api/status")[2]["scheduler"]["enabled"] is True


def test_last_refresh_at_semantics_unchanged(monkeypatch):
    from backend.db import insert_ingest_run, latest_ingest_run

    insert_ingest_run("CelesTrak", False, 3, {"satellite": 3})
    body = request(_app(), "GET", "/api/status")[2]
    assert body["last_refresh_at"] == latest_ingest_run()["completed_at"]


# --- health ---------------------------------------------------------------------------------

@pytest.mark.real_auth
def test_health_is_public_and_contains_no_secrets(monkeypatch):
    monkeypatch.setattr("backend.main._ollama_reachable", lambda: False)
    monkeypatch.setenv("SCHEDULER_SECRET", SECRET)
    monkeypatch.setenv("CRON_SECRET", "cron-secret-value")
    monkeypatch.setattr("backend.config.API_KEY", "deploy-api-key-value")
    monkeypatch.setattr("backend.config.SCHEDULER_MODE", "external")
    status, _, body = request(_app(), "GET", "/api/health")
    assert status == 200 and body["ok"] is True and body["status"] == "ok"
    assert set(body["scheduler"]) == {"mode", "enabled", "next_scheduled_at", "last_successful_refresh_at",
                                      "last_attempt_at", "last_attempt_status"}
    assert body["scheduler"]["mode"] == "external" and body["scheduler"]["enabled"] is True
    assert body["screening_horizon_hours"] == 72 and body["object_count"] == 0
    assert body["ai"] == {"mode": "ollama", "available": False, "status": "AI_FALLBACK_ACTIVE"}
    raw = json.dumps(body)
    for secret in (SECRET, "cron-secret-value", "deploy-api-key-value", "pbkdf2", "11434", "antariksha.db",
                   "scheduler_test.db", "tle_cache", "\\\\", "/api/generate"):
        assert secret not in raw, secret
