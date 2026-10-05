"""Controlled two-step DEMO history (/api/demo/seed?mode=collision&history_step=1|2).

Runs the REAL screening/risk pipeline on the synthetic-TLE DB from
test_demo_seed.py (Ollama unreachable -> template briefs). Only the disclosed
designed miss distance differs between the steps; Pc and tier are whatever the
unchanged pipeline computes. Step 1 starts a fresh demo track, step 2 reuses
step 1's encounter epoch and is correlated onto that track.
"""

import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from backend.tests._asgi import request
from backend.tests.test_demo_seed import demo_db  # noqa: F401  (fixture)


def _seed(history_step=None):
    from backend.demo_seed import seed_event
    return seed_event(asset_hint="TEST-ASSET", proximity=False, debris_hint="TEST-DEBRIS",
                      history_step=history_step)


def _objects_hash():
    from backend.db import get_connection
    conn = get_connection()
    try:
        rows = conn.execute("SELECT norad_id, name, tle_line1, tle_line2, omm_json, source_format, active "
                            "FROM objects ORDER BY norad_id").fetchall()
    finally:
        conn.close()
    return hashlib.sha256(json.dumps([tuple(r) for r in rows]).encode()).hexdigest()


def _event(event_id):
    from backend.db import get_event_with_brief
    return get_event_with_brief(event_id)


def _shift_clock(monkeypatch, delta):
    """Make backend.demo_seed see `now + delta` (geometry scheduling and the
    step-2 staleness check); screening/risk keep the real clock."""
    from backend import demo_seed

    class _ShiftedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + delta

    monkeypatch.setattr(demo_seed, "datetime", _ShiftedDatetime)


def test_history_steps_go_high_then_critical_on_one_track(demo_db):
    from backend.demo_seed import COLLISION_HISTORY_MISS_KM
    from backend.event_history import get_event_evolution

    before = _objects_hash()
    s1 = _seed(1)
    e1 = _event(s1["event_id"])  # events are rebuilt by every screening run
    s2 = _seed(2)
    e2 = _event(s2["event_id"])

    assert COLLISION_HISTORY_MISS_KM == {1: 0.52, 2: 0.02}
    assert s1["history"]["step"] == 1 and s2["history"]["step"] == 2
    assert s1["history"]["id"] == s2["history"]["id"] and s2["history"]["of"] == 2
    assert s1["separation_km"] == 0.52 and s2["separation_km"] == 0.02
    # Tiers come from the real pipeline for the two designed misses.
    assert e1["risk_tier"] == "High" and 1e-5 < e1["pc_score"] <= 1e-4
    assert e2["risk_tier"] == "Critical" and e2["pc_score"] > 1e-4
    assert e1["is_demo"] and e2["is_demo"]
    assert abs(e1["miss_distance_km"] - 0.52) < 0.01 and abs(e2["miss_distance_km"] - 0.02) < 0.005
    # Same designed encounter: TCAs within seconds (well inside the 1200 s window).
    tca1 = datetime.fromisoformat(e1["tca_timestamp"])
    tca2 = datetime.fromisoformat(e2["tca_timestamp"])
    assert abs((tca2 - tca1).total_seconds()) < 5
    assert s1["encounter_epoch"] == s2["encounter_epoch"]

    evo = get_event_evolution(s2["event_id"])
    assert evo["history_available"] is True and evo["is_demo"] is True
    assert [o["risk_tier"] for o in evo["observations"]] == ["High", "Critical"]
    assert evo["trends"]["risk_tier"] == "increased"
    assert evo["trends"]["miss_distance"] == "decreasing"
    assert evo["trends"]["pc_ratio"] == pytest.approx(e2["pc_score"] / e1["pc_score"])
    assert evo["demo_history"] == {"id": s1["history"]["id"], "step": 2, "of": 2}
    assert [o["demo_history"]["step"] for o in evo["observations"]] == [1, 2]

    assert s2["delta"]["available"] is True and s2["delta"]["counts"]["increased"] == 1
    assert _objects_hash() == before  # real catalog never touched


def test_step1_and_plain_seeds_reset_demo_baseline(demo_db):
    from backend.event_history import get_event_evolution

    plain1 = _seed()
    plain2 = _seed()
    # Each rehearsal starts its own track: no chaining of identical rows.
    assert plain2["delta"]["available"] is False and plain2["delta"]["reason"] == "no_previous_run"
    assert len(get_event_evolution(plain2["event_id"])["observations"]) == 1
    assert get_event_evolution(plain2["event_id"])["demo_history"] is None
    assert plain1["history"] is None

    s1 = _seed(1)
    assert s1["delta"]["reason"] == "no_previous_run"
    evo = get_event_evolution(s1["event_id"])
    assert len(evo["observations"]) == 1 and evo["trends"]["risk_tier"] is None

    # Rows are never deleted: all three demo runs keep their observations.
    from backend.db import get_connection
    conn = get_connection()
    try:
        n = conn.execute("SELECT COUNT(*) FROM event_observations WHERE is_demo = 1").fetchone()[0]
    finally:
        conn.close()
    assert n >= 3


def test_step2_pins_encounter_epoch_across_hour_boundary(demo_db, monkeypatch):
    from backend import demo_seed

    s1 = _seed(1)
    t_e = datetime.fromisoformat(s1["encounter_epoch"])
    tca1 = datetime.fromisoformat(_event(s1["event_id"])["tca_timestamp"])
    _shift_clock(monkeypatch, timedelta(hours=1))
    # A fresh schedule an hour later would be a different encounter...
    fresh, _ = demo_seed._encounter_schedule(demo_seed.datetime.now(timezone.utc),
                                             demo_seed.COLLISION_ENCOUNTER_LEAD_HOURS)
    assert abs((fresh - t_e).total_seconds()) >= 3600
    # ...but step 2 reuses step 1's epoch, so it still correlates.
    s2 = _seed(2)
    assert s2["encounter_epoch"] == s1["encounter_epoch"]
    tca2 = datetime.fromisoformat(_event(s2["event_id"])["tca_timestamp"])
    assert abs((tca2 - tca1).total_seconds()) < 5
    assert s2["delta"]["available"] is True and s2["delta"]["counts"]["new"] == 0


def test_step2_refused_without_recent_step1(demo_db, monkeypatch):
    from backend.demo_seed import DemoScenarioError

    with pytest.raises(DemoScenarioError) as exc:
        _seed(2)  # no demo run at all
    assert exc.value.reason == "history_not_started" and exc.value.status_code == 409

    _seed()  # a plain demo is the latest demo run
    with pytest.raises(DemoScenarioError, match="step 1"):
        _seed(2)

    _seed(1)
    _seed(2)
    with pytest.raises(DemoScenarioError):
        _seed(2)  # step 2 again: latest demo run is step 2, not step 1

    _seed(1)
    _shift_clock(monkeypatch, timedelta(hours=6))  # designed encounter now < 1 h ahead
    with pytest.raises(DemoScenarioError, match="too old") as exc:
        _seed(2)
    assert exc.value.reason == "history_not_started"


def test_history_api_contract(demo_db, monkeypatch):
    from backend import demo_seed, main

    monkeypatch.setattr("backend.config.API_KEY", "")
    real = demo_seed.seed_event

    def seed(**kwargs):
        return real(asset_hint="TEST-ASSET", debris_hint="TEST-DEBRIS", foreign_hint="TEST-FOREIGN", **kwargs)

    monkeypatch.setattr(demo_seed, "seed_event", seed)

    status, _, body = request(main.app, "POST", "/api/demo/seed?mode=collision&history_step=2")
    assert status == 409
    assert body["error"] == "demo_scenario_failed" and body["reason"] == "history_not_started"
    assert isinstance(body["detail"], str) and "step 1" in body["detail"]

    status, _, body = request(main.app, "POST", "/api/demo/seed?mode=proximity&history_step=1")
    assert status == 400
    status, _, _ = request(main.app, "POST", "/api/demo/seed?mode=collision&history_step=3")
    assert status == 422

    status, _, b1 = request(main.app, "POST", "/api/demo/seed?mode=collision&history_step=1")
    assert status == 200 and b1["history"]["step"] == 1
    status, _, b2 = request(main.app, "POST", "/api/demo/seed?mode=collision&history_step=2")
    assert status == 200 and b2["history"] == {**b1["history"], "step": 2}

    status, _, evo = request(main.app, "GET", f"/api/events/{b2['event_id']}/evolution")
    assert status == 200 and evo["demo_history"]["step"] == 2
    status, _, prov = request(main.app, "GET", f"/api/events/{b2['event_id']}/provenance")
    assert status == 200
    assert json.dumps(prov).count('"history"') >= 1  # disclosed in the demo scenario block
    status, _, delta = request(main.app, "GET", "/api/screening/delta?domain=demo")
    item = delta["items"]["increased"][0]
    assert item["pc_ratio"] == pytest.approx(item["current"]["pc_score"] / item["previous"]["pc_score"])

    # Leaving history_step out keeps the plain collision demo (0.02 km design).
    status, _, plain = request(main.app, "POST", "/api/demo/seed?mode=collision")
    assert status == 200 and plain["history"] is None and plain["separation_km"] == 0.02
