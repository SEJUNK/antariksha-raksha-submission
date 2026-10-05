"""Optional Telegram notifications (backend/notify.py): configuration gate,
message content, secret hygiene, protected test endpoint, non-blocking
delivery, demo policy and per-track de-duplication. No real network: the
Bot API call is replaced via notify._http_post."""

import json
import logging
from datetime import datetime, timezone

import pytest
import requests

from backend import notify
from backend.tests.test_demo_seed import demo_db  # noqa: F401  (fixture)

FAKE_TOKEN = "123456:TEST-ONLY-NOT-A-REAL-TOKEN"
FAKE_CHAT = "-1000000000001"


class _Resp:
    def __init__(self, status=200, payload=None, bad_json=False):
        self.status_code = status
        self._payload = {"ok": True, "result": {}} if payload is None else payload
        self._bad_json = bad_json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code} Error", response=self)

    def json(self):
        if self._bad_json:
            raise ValueError("not json")
        return self._payload


@pytest.fixture
def tg(monkeypatch):
    """Telegram enabled + configured; records every Bot API call."""
    monkeypatch.setenv("TELEGRAM_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", FAKE_TOKEN)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", FAKE_CHAT)
    for var in ("TELEGRAM_MIN_RISK", "TELEGRAM_NOTIFY_DEMO", "ANTARIKSHA_PUBLIC_APP_URL"):
        monkeypatch.delenv(var, raising=False)
    calls = []

    def fake_post(url, json=None, timeout=None):
        calls.append({"url": url, "json": json, "timeout": timeout})
        return _Resp()

    monkeypatch.setattr(notify, "_http_post", fake_post)
    return calls


EVENT = {
    "event_id": 41, "track_id": 7, "event_class": "collision_risk", "risk_tier": "High",
    "object_a_name": "CARTOSAT-3", "object_b_name": "FENGYUN 1C",
    "tca_timestamp": "2026-10-04T11:00:00.123+00:00", "miss_distance_km": 0.52,
    "rel_velocity_km_s": 0.2, "pc_score": 4.18e-5, "is_demo": 0,
    "data_source": "CelesTrak public GP/TLE data (celestrak.org)",
}


# --- Configuration ------------------------------------------------------------

def test_disabled_by_default_even_with_credentials(monkeypatch):
    monkeypatch.delenv("TELEGRAM_ENABLED", raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", FAKE_TOKEN)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", FAKE_CHAT)
    monkeypatch.setattr(notify, "_http_post", lambda *a, **k: pytest.fail("must not send when disabled"))
    assert notify.send_message("x") == {"sent": False, "status": "disabled", "error": None}
    assert notify.notify_run(1)["status"] == "disabled"


@pytest.mark.parametrize("missing,var", [("bot_token", "TELEGRAM_BOT_TOKEN"), ("chat_id", "TELEGRAM_CHAT_ID")])
def test_enabled_but_missing_credential_fails_gracefully(tg, monkeypatch, missing, var):
    monkeypatch.delenv(var, raising=False)
    result = notify.send_message("x")
    assert result == {"sent": False, "status": "unconfigured", "error": f"missing_{missing}"}
    assert tg == []


def test_min_risk_policy_uses_existing_tiers(monkeypatch):
    monkeypatch.delenv("TELEGRAM_MIN_RISK", raising=False)
    assert [notify.is_notifiable_tier(t) for t in ("Low", "Medium", "High", "Critical")] == [False, False, True, True]
    monkeypatch.setenv("TELEGRAM_MIN_RISK", "critical")
    assert [notify.is_notifiable_tier(t) for t in ("High", "Critical")] == [False, True]
    monkeypatch.setenv("TELEGRAM_MIN_RISK", "Medium")
    assert notify.is_notifiable_tier("Medium") and not notify.is_notifiable_tier("Low")
    monkeypatch.setenv("TELEGRAM_MIN_RISK", "Low")  # Low is never allowed -> safe default
    assert notify.min_risk_tier() == "High"


# --- Message ------------------------------------------------------------------

def test_message_contains_event_facts_and_boundary():
    text = notify.build_message(EVENT)
    for expected in ("HIGH CONJUNCTION RISK", "Object A: CARTOSAT-3", "Object B: FENGYUN 1C",
                     "TCA: 2026-10-04 11:00 UTC", "Miss distance: 0.520 km", "Relative velocity: 0.20 km/s",
                     "Risk: HIGH", "Pc indicator: 4.18e-05", "CelesTrak public GP/TLE",
                     "Simplified analytic collision-probability indicator", "Event #41 · track #7",
                     "not an operational maneuver command"):
        assert expected in text, expected
    assert "DEMO" not in text and "Review:" not in text


def test_message_demo_label_and_review_link(monkeypatch):
    monkeypatch.setenv("ANTARIKSHA_PUBLIC_APP_URL", "https://console.example.app/")
    text = notify.build_message({**EVENT, "is_demo": 1})
    assert text.startswith("🧪 DEMO — CONTROLLED SIMULATION\nThis is NOT an operational conjunction warning.")
    assert text.rstrip().endswith("Review: https://console.example.app")
    monkeypatch.setenv("ANTARIKSHA_PUBLIC_APP_URL", "http://insecure.example")  # only https links
    assert "Review:" not in notify.build_message(EVENT)


def test_proximity_message_has_no_pc_or_velocity():
    text = notify.build_message({**EVENT, "event_class": "proximity_watch", "rel_velocity_km_s": None, "pc_score": 0.0})
    assert "PROXIMITY WATCH" in text and "Minimum separation: 0.520 km" in text
    assert "Pc indicator" not in text and "Relative velocity" not in text


# --- Reliability (never raises, structured result) ------------------------------

@pytest.mark.parametrize("behaviour,expected_error", [
    ("timeout", "Timeout"),
    ("http_error", "HTTPError HTTP 401"),
    ("malformed", "unexpected_response"),
    ("not_ok", "unexpected_response"),
])
def test_delivery_failures_are_reported_not_raised(tg, monkeypatch, behaviour, expected_error):
    def post(url, json=None, timeout=None):
        if behaviour == "timeout":
            raise requests.exceptions.Timeout(f"timed out: {url}")
        if behaviour == "http_error":
            return _Resp(status=401)
        if behaviour == "malformed":
            return _Resp(bad_json=True)
        return _Resp(payload={"ok": False, "description": "Bad Request"})

    monkeypatch.setattr(notify, "_http_post", post)
    result = notify.send_message("x")
    assert result["sent"] is False and result["status"] == "failed" and result["error"] == expected_error


def test_send_uses_timeout_and_success_result(tg):
    assert notify.send_message("hello") == {"sent": True, "status": "sent", "error": None}
    assert tg[0]["timeout"] == notify.SEND_TIMEOUT_SECONDS and tg[0]["json"]["chat_id"] == FAKE_CHAT


# --- Secret hygiene --------------------------------------------------------------

def test_token_never_in_logs_results_or_status(tg, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)

    def post(url, json=None, timeout=None):
        raise requests.exceptions.ConnectionError(f"Max retries exceeded with url: {url}")

    monkeypatch.setattr(notify, "_http_post", post)
    result = notify.send_message("x")
    logging.getLogger("urllib3.connectionpool").debug(
        '%s "POST /bot%s/sendMessage HTTP/1.1" 200 None', "https://api.telegram.org:443", FAKE_TOKEN)
    assert FAKE_TOKEN not in caplog.text and FAKE_TOKEN not in json.dumps(result)
    status = notify.status()
    assert FAKE_TOKEN not in json.dumps(status) and FAKE_CHAT not in json.dumps(status)
    assert status["bot_token"] == "********" and status["chat_id"] == "configured" and status["active"] is True


# --- Pipeline integration: trigger, de-duplication, demo policy, non-blocking ---

def _obs(event_id, tier, is_demo=False, pair=("90001", "90002"), tca="2026-10-04T11:00:00+00:00"):
    return {"event_id": event_id, "object_a_id": pair[0], "object_b_id": pair[1], "event_class": "collision_risk",
            "tca_timestamp": tca, "miss_distance_km": 0.5, "rel_velocity_km_s": 0.2, "pc_score": 4e-5,
            "risk_tier": tier, "priority_score": 1.0, "dwell_minutes": None, "is_demo": is_demo}


def _record_run(mode, observations):
    from backend.db import get_connection
    from backend.event_history import record_run_observations

    conn = get_connection()
    with conn:
        cur = conn.execute("INSERT INTO screening_runs (started_at, mode) VALUES (?, ?)",
                           (datetime.now(timezone.utc).isoformat(), mode))
        run_id = cur.lastrowid
    conn.close()
    record_run_observations(run_id, mode, observations)
    return run_id


def _notification_rows():
    from backend.db import list_governance_audit

    return [r for r in list_governance_audit(limit=1000) if r["action"] == "notification"]


@pytest.fixture
def history_db(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.db.DB_PATH", tmp_path / "notify.db")
    from backend.db import init_db

    init_db()


def test_dedup_same_track_same_tier_notifies_once_and_escalation_again(history_db, tg):
    r1 = _record_run("live", [_obs(1, "High")])
    assert notify.notify_run(r1)["sent"] == 1
    r2 = _record_run("live", [_obs(2, "High")])  # same encounter, next refresh
    assert notify.notify_run(r2)["sent"] == 0
    r3 = _record_run("live", [_obs(3, "Critical")])  # escalation
    assert notify.notify_run(r3)["sent"] == 1
    r4 = _record_run("live", [_obs(4, "High")])  # de-escalation
    assert notify.notify_run(r4)["sent"] == 0
    r5 = _record_run("live", [_obs(5, "Critical")])  # back to an already-notified tier
    assert notify.notify_run(r5)["sent"] == 0
    assert len(tg) == 2 and "HIGH" in tg[0]["json"]["text"] and "CRITICAL" in tg[1]["json"]["text"]
    rows = _notification_rows()
    assert [r["details"]["risk_tier"] for r in rows][::-1] == ["High", "Critical"]
    assert all(r["details"]["channel"] == "telegram" and r["details"]["status"] == "sent" for r in rows)
    assert rows[0]["details"]["escalated_from"] == "High"
    assert all(FAKE_TOKEN not in json.dumps(r) for r in rows)


def test_low_and_medium_not_notified_by_default_and_new_track_notifies(history_db, tg):
    r1 = _record_run("live", [_obs(1, "Low"), _obs(2, "Medium", pair=("90001", "90003"))])
    assert notify.notify_run(r1) == {"run_id": r1, "sent": 0, "failed": 0, "skipped": 2, "deferred": 0, "status": "ok"}
    r2 = _record_run("live", [_obs(3, "High", pair=("90001", "90004"))])
    assert notify.notify_run(r2)["sent"] == 1


def test_failed_delivery_is_audited_and_retried_next_run(history_db, tg, monkeypatch):
    monkeypatch.setattr(notify, "_http_post", lambda *a, **k: _Resp(status=502))
    r1 = _record_run("live", [_obs(1, "High")])
    assert notify.notify_run(r1)["failed"] == 1
    assert _notification_rows()[0]["details"]["status"] == "failed"
    monkeypatch.setattr(notify, "_http_post", lambda *a, **k: _Resp())
    r2 = _record_run("live", [_obs(2, "High")])
    assert notify.notify_run(r2)["sent"] == 1


def test_demo_events_suppressed_by_default_and_labelled_when_enabled(history_db, tg, monkeypatch):
    r1 = _record_run("demo", [_obs(1, "Critical", is_demo=True)])
    assert notify.notify_run(r1)["sent"] == 0 and tg == []
    monkeypatch.setenv("TELEGRAM_NOTIFY_DEMO", "true")
    r2 = _record_run("demo", [_obs(2, "Critical", is_demo=True, pair=("90001", "90005"))])
    assert notify.notify_run(r2)["sent"] == 1
    assert tg[0]["json"]["text"].startswith("🧪 DEMO — CONTROLLED SIMULATION")
    assert _notification_rows()[0]["is_demo"] is True


def test_pipeline_survives_telegram_timeout(demo_db, tg, monkeypatch):
    from backend.db import get_event_with_brief
    from backend.demo_seed import seed_event

    monkeypatch.setenv("TELEGRAM_NOTIFY_DEMO", "true")
    monkeypatch.setenv("TELEGRAM_MIN_RISK", "Medium")

    attempts = []

    def boom(*a, **k):
        attempts.append(1)
        raise requests.exceptions.Timeout("Telegram unreachable")

    monkeypatch.setattr(notify, "_http_post", boom)
    result = seed_event(asset_hint="TEST-ASSET", proximity=False, debris_hint="TEST-DEBRIS")
    event = get_event_with_brief(result["event_id"])
    assert event and event["risk_tier"] and event["pc_score"] is not None
    assert notify.is_notifiable_tier(event["risk_tier"]) and attempts  # a send was attempted and failed
    assert _notification_rows()[0]["details"]["status"] == "failed"


def test_pipeline_survives_notifier_crash(demo_db, monkeypatch):
    from backend.db import get_event_with_brief
    from backend.demo_seed import seed_event

    monkeypatch.setattr("backend.pipeline.notify_run", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    result = seed_event(asset_hint="TEST-ASSET", proximity=False, debris_hint="TEST-DEBRIS")
    assert get_event_with_brief(result["event_id"])["risk_tier"]



# --- Protected endpoints (real session / CSRF / RBAC) ------------------------------

@pytest.mark.real_auth
def test_status_and_test_endpoints_rbac_csrf_and_no_secrets(history_db, tg, monkeypatch):
    from backend.tests._auth_helpers import call, login, make_user

    monkeypatch.setattr(notify, "_last_test_at", 0.0)
    for name, role in (("tg-viewer", "VIEWER"), ("tg-operator", "OPERATOR"),
                       ("tg-assets", "ASSET_MANAGER"), ("tg-admin", "ADMINISTRATOR")):
        make_user(name, role)
    tokens = {name: login(name)[3] for name in ("tg-viewer", "tg-operator", "tg-assets", "tg-admin")}

    assert call("GET", "/api/admin/notifications/telegram")[0] == 401
    assert call("POST", "/api/admin/notifications/telegram/test")[0] == 401
    for name in ("tg-viewer", "tg-operator", "tg-assets"):
        assert call("GET", "/api/admin/notifications/telegram", tokens[name])[0] == 403
        assert call("POST", "/api/admin/notifications/telegram/test", tokens[name])[0] == 403
    assert tg == []

    status, _, body = call("GET", "/api/admin/notifications/telegram", tokens["tg-admin"])
    assert status == 200 and body["active"] is True and body["bot_token"] == "********"
    assert call("POST", "/api/admin/notifications/telegram/test", tokens["tg-admin"], csrf=False)[0] == 403
    status, _, body = call("POST", "/api/admin/notifications/telegram/test", tokens["tg-admin"],
                           json_body={"text": "attacker supplied text"})
    assert status == 200 and body["sent"] is True
    assert len(tg) == 1 and "attacker supplied text" not in tg[0]["json"]["text"]
    assert tg[0]["json"]["text"].startswith("🧪 TEST — ANTARIKSHA-RAKSHA")
    assert call("POST", "/api/admin/notifications/telegram/test", tokens["tg-admin"])[0] == 429  # cooldown
    assert len(tg) == 1
    audit = [r for r in _notification_rows() if r["target_type"] == "telegram_test"]
    assert audit and audit[0]["actor_username"] == "tg-admin" and audit[0]["details"]["status"] == "sent"

    health = call("GET", "/api/health")[2]
    everything = json.dumps([body, health, audit])
    assert FAKE_TOKEN not in everything and FAKE_CHAT not in everything


def test_message_handles_zero_or_missing_values():
    text = notify.build_message({**EVENT, "pc_score": None, "rel_velocity_km_s": None, "tca_timestamp": None})
    assert "Pc indicator: N/A" in text and "Relative velocity: N/A km/s" in text and "TCA: N/A" in text
    assert "Pc indicator: 0.00e+00" in notify.build_message({**EVENT, "pc_score": 0.0})


def test_first_failure_stops_the_run_and_unconfigured_audits_once(history_db, tg, monkeypatch):
    calls = []
    monkeypatch.setattr(notify, "_http_post", lambda *a, **k: calls.append(1) or _Resp(status=401))
    pairs = [("90001", f"9010{i}") for i in range(4)]
    r1 = _record_run("live", [_obs(i, "High", pair=p) for i, p in enumerate(pairs)])
    s1 = notify.notify_run(r1)
    assert s1["failed"] == 1 and s1["sent"] == 0 and len(calls) == 1  # no burst against a broken channel
    assert len(_notification_rows()) == 1
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN")
    r2 = _record_run("live", [_obs(10 + i, "High", pair=p) for i, p in enumerate(pairs)])
    notify.notify_run(r2)
    rows = _notification_rows()
    assert len(rows) == 2 and rows[0]["details"]["status"] == "unconfigured" and len(calls) == 1


def test_alerts_per_run_are_capped_and_deferred_to_next_run(history_db, tg):
    pairs = [("90001", f"902{i:02d}") for i in range(notify.MAX_ALERTS_PER_RUN + 2)]
    r1 = _record_run("live", [_obs(i, "High", pair=p) for i, p in enumerate(pairs)])
    s1 = notify.notify_run(r1)
    assert s1["sent"] == notify.MAX_ALERTS_PER_RUN and s1["deferred"] == 2
    r2 = _record_run("live", [_obs(100 + i, "High", pair=p) for i, p in enumerate(pairs)])
    s2 = notify.notify_run(r2)
    assert s2["sent"] == 2 and len(tg) == len(pairs)  # every track alerted exactly once


def test_demo_message_does_not_claim_live_catalog_data():
    text = notify.build_message({**EVENT, "is_demo": 1})
    assert "Data: controlled demo scenario (engineered orbit override of public CelesTrak data)" in text


def test_health_flag_true_only_when_enabled_and_configured(monkeypatch):
    from backend.db import init_db
    from backend.main import app
    from backend.tests._asgi import request

    init_db()
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", FAKE_TOKEN)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", FAKE_CHAT)
    monkeypatch.delenv("TELEGRAM_ENABLED", raising=False)
    assert request(app, "GET", "/api/health")[2]["telegram_configured"] is False
    monkeypatch.setenv("TELEGRAM_ENABLED", "true")
    body = request(app, "GET", "/api/health")[2]
    assert body["telegram_configured"] is True and FAKE_TOKEN not in json.dumps(body)
