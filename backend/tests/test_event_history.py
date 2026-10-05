"""Persistent event observations, prototype correlation, event evolution
and refresh deltas (backend/event_history.py)."""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from backend.event_history import (
    CORRELATION_RULE,
    MISS_STABLE_THRESHOLD_KM,
    MISS_STABLE_THRESHOLD_REL,
    PROXIMITY_TIER_THRESHOLDS,
    correlate,
    miss_distance_trend,
    pair_key,
    risk_tier_trend,
    tca_shift_seconds,
)
from backend.tests._asgi import request

T0 = datetime(2026, 10, 5, 12, 0, 0, tzinfo=timezone.utc)


def _ts(minutes=0.0):
    return (T0 + timedelta(minutes=minutes)).isoformat()


# --- Pure correlation / trend rules ---------------------------------------------

def _prev(pid, a, b, cls, minutes):
    return {"id": pid, "pair_key": pair_key(a, b), "event_class": cls, "tca_timestamp": _ts(minutes)}


def _new(eid, a, b, cls, minutes):
    return {"event_id": eid, "pair_key": pair_key(a, b), "event_class": cls, "tca_timestamp": _ts(minutes)}


def test_correlate_matches_within_window_and_rejects_outside():
    prev = [_prev(1, "1", "2", "collision_risk", 0)]
    assert correlate(prev, [_new(10, "1", "2", "collision_risk", 19.9)]) == [(1, False)]
    assert correlate(prev, [_new(10, "1", "2", "collision_risk", 20.1)]) == [(None, False)]


def test_correlate_unordered_pair_and_class_must_match():
    prev = [_prev(1, "1", "2", "collision_risk", 0)]
    assert correlate(prev, [_new(10, "2", "1", "collision_risk", 1)]) == [(1, False)]
    assert correlate(prev, [_new(10, "1", "2", "proximity_watch", 1)]) == [(None, False)]
    assert correlate(prev, [_new(10, "1", "3", "collision_risk", 1)]) == [(None, False)]


def test_correlate_proximity_window_is_six_hours():
    prev = [_prev(1, "1", "3", "proximity_watch", 0)]
    assert correlate(prev, [_new(10, "1", "3", "proximity_watch", 359)]) == [(1, False)]
    assert correlate(prev, [_new(10, "1", "3", "proximity_watch", 361)]) == [(None, False)]


def test_correlate_multiple_encounters_same_pair_one_to_one_by_nearest_tca():
    # Two encounters ~47 min apart (one per orbital node) in both runs.
    prev = [_prev(1, "1", "2", "collision_risk", 0), _prev(2, "1", "2", "collision_risk", 47)]
    new = [_new(11, "1", "2", "collision_risk", 47.2), _new(10, "1", "2", "collision_risk", 0.1)]
    assert correlate(prev, new) == [(2, False), (1, False)]


def test_correlate_identical_tca_tiebreak_and_ambiguous_flag():
    # Two previous observations equidistant from one new event: the lower
    # previous id wins and the link is flagged ambiguous.
    prev = [_prev(5, "1", "2", "collision_risk", -5), _prev(4, "1", "2", "collision_risk", 5)]
    new = [_new(10, "1", "2", "collision_risk", 0)]
    assert correlate(prev, new) == [(4, True)]
    # Two new events with an identical TCA competing for one previous
    # observation: the lower new event id wins; the other starts a new track.
    prev = [_prev(1, "1", "2", "collision_risk", 0)]
    new = [_new(21, "1", "2", "collision_risk", 3), _new(20, "1", "2", "collision_risk", 3)]
    assert correlate(prev, new) == [(None, False), (1, True)]


def test_miss_distance_trend_uses_abs_floor_and_relative_threshold():
    assert MISS_STABLE_THRESHOLD_KM == 0.05 and MISS_STABLE_THRESHOLD_REL == 0.05
    assert miss_distance_trend(0.5, 0.54) == "stable"        # < 0.05 km floor
    assert miss_distance_trend(0.5, 0.56) == "increasing"
    assert miss_distance_trend(20.0, 20.9) == "stable"       # < 5% of 20 km
    assert miss_distance_trend(20.0, 18.9) == "decreasing"
    assert miss_distance_trend(None, 1.0) is None


def test_risk_tier_trend_and_tca_shift():
    assert risk_tier_trend("Medium", "Critical") == "increased"
    assert risk_tier_trend("High", "Low") == "decreased"
    assert risk_tier_trend("High", "High") == "unchanged"
    assert risk_tier_trend(None, "High") is None
    assert tca_shift_seconds(_ts(0), _ts(1.5)) == 90.0


# --- DB-backed --------------------------------------------------------------

@pytest.fixture
def hist_db(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.db.DB_PATH", tmp_path / "history_test.db")
    from backend.db import init_db, upsert_object
    init_db()
    upsert_object("1", "ASSET-1", "satellite", "India", "Tier1", "l1", "l2")
    upsert_object("2", "DEB-2", "debris", None, None, "l1", "l2")
    upsert_object("3", "FOREIGN-3", "foreign_sat", "X", None, "l1", "l2")
    upsert_object("4", "DEB-4", "debris", None, None, "l1", "l2")
    return tmp_path / "history_test.db"


def _screen(mode, events, record=True):
    """Simulate one screening run: rebuild conjunction_events, then record
    observations exactly as the pipeline does. events: (a, b, cls, tca_min,
    miss_km, tier). Returns (run_id, [event_ids])."""
    from backend.db import clear_events, insert_event, insert_screening_run
    from backend.event_history import record_run_observations

    run_id = insert_screening_run(datetime.now(timezone.utc).isoformat(), mode)
    clear_events()
    obs, ids = [], []
    for a, b, cls, minutes, miss, tier in events:
        pc = {"Low": 1e-7, "Medium": 2e-6, "High": 2e-5, "Critical": 2e-4}[tier]
        eid = insert_event(a, b, cls, _ts(minutes), miss, 7.0, pc, tier, pc * 1e6,
                           is_demo=(mode == "demo"), screening_run_id=run_id)
        ids.append(eid)
        obs.append({"event_id": eid, "object_a_id": a, "object_b_id": b, "event_class": cls,
                    "tca_timestamp": _ts(minutes), "miss_distance_km": miss, "rel_velocity_km_s": 7.0,
                    "pc_score": pc, "risk_tier": tier, "priority_score": pc * 1e6,
                    "dwell_minutes": 30.0 if cls == "proximity_watch" else None,
                    "is_demo": mode == "demo"})
    if record:
        record_run_observations(run_id, mode, obs)
    return run_id, ids


C = "collision_risk"
P = "proximity_watch"


def test_migration_adds_table_and_columns_to_legacy_db(tmp_path, monkeypatch):
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE screening_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL, "
                 "completed_at TEXT, mode TEXT NOT NULL)")
    conn.execute("INSERT INTO screening_runs (started_at, mode) VALUES ('2026-01-01T00:00:00+00:00', 'live')")
    conn.commit()
    conn.close()
    monkeypatch.setattr("backend.db.DB_PATH", db_path)
    from backend.db import get_connection, init_db
    init_db()
    init_db()  # idempotent
    conn = get_connection()
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(screening_runs)")}
        assert {"observations_recorded", "correlation_baseline_run_id"} <= cols
        obs_cols = {r[1] for r in conn.execute("PRAGMA table_info(event_observations)")}
        assert {"screening_run_id", "event_id", "track_id", "ambiguous_match", "dwell_minutes", "is_demo"} <= obs_cols
        legacy = conn.execute("SELECT observations_recorded FROM screening_runs WHERE id = 1").fetchone()[0]
        assert legacy is None
    finally:
        conn.close()


def test_first_run_has_no_previous_run_and_no_runs_before_that(hist_db):
    from backend.event_history import compute_delta
    assert compute_delta()["reason"] == "no_runs"
    run_id, _ = _screen("live", [("1", "2", C, 0, 1.0, "Medium")])
    delta = compute_delta()
    assert delta["available"] is False and delta["reason"] == "no_previous_run"
    assert delta["latest_run"]["id"] == run_id and delta["previous_run"] is None
    assert delta["domain"] == "real"


def test_delta_buckets_between_consecutive_real_runs(hist_db):
    from backend.event_history import compute_delta
    r1, _ = _screen("cached", [
        ("1", "2", C, 0, 1.0, "Medium"),     # -> increased
        ("1", "4", C, 30, 0.8, "High"),      # -> decreased
        ("1", "3", P, 100, 12.0, "Medium"),  # -> unchanged
        ("1", "2", C, 300, 2.0, "Low"),      # -> not present
    ])
    r2, ids2 = _screen("live", [
        ("1", "2", C, 0.05, 0.4, "High"),
        ("1", "4", C, 30.1, 1.5, "Medium"),
        ("1", "3", P, 160, 11.9, "Medium"),
        ("1", "2", C, 600, 3.0, "Low"),      # new (300 min away from anything)
    ])
    delta = compute_delta()
    assert delta["available"] is True and delta["reason"] is None
    assert delta["previous_run"]["id"] == r1 and delta["latest_run"]["id"] == r2
    assert delta["counts"] == {"new": 1, "increased": 1, "decreased": 1, "unchanged": 1, "not_present": 1}
    inc = delta["items"]["increased"][0]
    assert inc["event_id"] == ids2[0] and inc["is_demo"] is False
    assert inc["object_a_name"] == "ASSET-1" and inc["object_b_name"] == "DEB-2"
    assert inc["previous"]["miss_distance_km"] == 1.0 and inc["current"]["miss_distance_km"] == 0.4
    assert inc["previous"]["risk_tier"] == "Medium" and inc["current"]["risk_tier"] == "High"
    assert set(inc["current"]) == {"tca_timestamp", "miss_distance_km", "rel_velocity_km_s", "risk_tier",
                                   "pc_score", "priority_score"}
    assert inc["miss_distance_trend"] == "decreasing" and inc["tca_shift_seconds"] == 3.0
    gone = delta["items"]["not_present"][0]
    assert gone["current"] is None and gone["event_id"] is None and gone["miss_distance_trend"] is None
    new = delta["items"]["new"][0]
    assert new["previous"] is None and new["event_id"] == ids2[3]
    assert delta["items"]["unchanged"][0]["miss_distance_trend"] == "stable"


def test_demo_and_real_runs_never_correlate(hist_db):
    from backend.db import get_screening_run
    from backend.event_history import compute_delta
    r1, _ = _screen("live", [("1", "2", C, 0, 1.0, "Medium")])
    d1, demo_ids = _screen("demo", [("1", "2", C, 0, 0.1, "Critical")])
    assert get_screening_run(d1)["correlation_baseline_run_id"] is None
    r2, _ = _screen("live", [("1", "2", C, 0.1, 1.0, "Medium")])
    assert get_screening_run(r2)["correlation_baseline_run_id"] == r1

    real = compute_delta()
    assert real["domain"] == "real" and real["counts"]["unchanged"] == 1
    # The DEMO run numbered between the two LIVE runs is reported as display
    # metadata only -- it is not part of the LIVE comparison and not "lost".
    assert [(r["id"], r["mode"], r["recorded"]) for r in real["intermediate_runs"]] == [(d1, "demo", True)]
    demo = compute_delta(domain="demo")
    assert demo["domain"] == "demo" and demo["reason"] == "no_previous_run"
    d2, _ = _screen("demo", [("1", "2", C, 0, 0.2, "Critical")])
    demo = compute_delta(domain="demo")
    assert demo["previous_run"]["id"] == d1 and demo["counts"]["unchanged"] == 1
    # The real delta is still available while demo events are current, but
    # its items no longer link to (deleted) current events.
    real = compute_delta(domain="real")
    assert real["latest_run"]["id"] == r2 and real["items"]["unchanged"][0]["event_id"] is None


def test_unrecorded_failed_run_is_skipped_as_baseline(hist_db):
    from backend.db import get_screening_run
    r1, _ = _screen("live", [("1", "2", C, 0, 1.0, "Medium")])
    _screen("live", [("1", "2", C, 0, 1.0, "Medium")], record=False)  # failed before recording
    r3, _ = _screen("live", [("1", "2", C, 0, 1.0, "Medium")])
    assert get_screening_run(r3)["correlation_baseline_run_id"] == r1


def test_observations_survive_event_rebuild(hist_db):
    from backend.db import clear_events, get_connection, list_events
    _screen("live", [("1", "2", C, 0, 1.0, "Medium")])
    _screen("live", [("1", "2", C, 0, 1.0, "Medium")])
    clear_events()
    assert list_events() == []
    conn = get_connection()
    try:
        assert conn.execute("SELECT COUNT(*) FROM event_observations").fetchone()[0] == 2
    finally:
        conn.close()


def test_evolution_endpoint_track_trends_and_contract(hist_db):
    from backend.main import app
    _screen("live", [("1", "2", C, 0, 1.0, "Medium")])
    _screen("live", [("1", "2", C, 0.5, 0.7, "High")])
    r3, ids3 = _screen("live", [("1", "2", C, 1.0, 0.3, "Critical")])

    status, _, body = request(app, "GET", f"/api/events/{ids3[0]}/evolution")
    assert status == 200
    assert body["event_id"] == ids3[0] and body["history_available"] is True
    assert body["correlation_rule"] == CORRELATION_RULE == "pair_class_nearest_tca_v1"
    assert body["correlation_window_seconds"] == 1200 and body["is_demo"] is False
    obs = body["observations"]
    assert [o["miss_distance_km"] for o in obs] == [1.0, 0.7, 0.3]
    assert [o["is_current"] for o in obs] == [False, False, True]
    assert obs[-1]["screening_run_id"] == r3 and obs[-1]["run_mode"] == "live"
    assert all(o["event_class"] == C and o["is_demo"] is False and o["dwell_minutes"] is None for o in obs)
    assert body["track_id"] == obs[0]["observation_id"]
    assert body["trends"] == {"miss_distance": "decreasing", "risk_tier": "increased",
                              "tca_shift_seconds": 30.0, "pc_ratio": pytest.approx(10.0),
                              "miss_stable_threshold_km": 0.05,
                              "miss_stable_threshold_rel": 0.05}


def test_evolution_proximity_window_and_dwell(hist_db):
    from backend.main import app
    _, ids = _screen("live", [("1", "3", P, 0, 12.0, "Medium")])
    status, _, body = request(app, "GET", f"/api/events/{ids[0]}/evolution")
    assert status == 200 and body["correlation_window_seconds"] == 21600
    assert body["observations"][0]["dwell_minutes"] == 30.0
    assert body["trends"]["miss_distance"] is None  # single observation


def test_evolution_fallback_for_unrecorded_event_and_404(hist_db):
    from backend.db import insert_event
    from backend.main import app
    eid = insert_event("1", "2", C, _ts(0), 1.0, 7.0, 2e-6, "Medium", 6.0, screening_run_id=None)
    status, _, body = request(app, "GET", f"/api/events/{eid}/evolution")
    assert status == 200
    assert body["history_available"] is False and body["track_id"] is None
    assert len(body["observations"]) == 1 and body["observations"][0]["is_current"] is True
    assert body["observations"][0]["observation_id"] is None
    assert body["trends"]["risk_tier"] is None
    status, _, _ = request(app, "GET", "/api/events/999999/evolution")
    assert status == 404


def test_delta_endpoint_validation_and_open_with_auth_enabled(hist_db, monkeypatch):
    from backend import config
    from backend.main import app
    monkeypatch.setattr(config, "API_KEY", "s3cret-key")
    status, _, body = request(app, "GET", "/api/screening/delta")
    assert status == 200 and body["available"] is False and body["reason"] == "no_runs"
    status, _, _ = request(app, "GET", "/api/screening/delta?domain=bogus")
    assert status == 422
    _, ids = _screen("demo", [("1", "2", C, 0, 1.0, "Medium")])
    status, _, body = request(app, "GET", "/api/screening/delta?domain=demo")
    assert status == 200 and body["domain"] == "demo" and body["reason"] == "no_previous_run"
    status, _, _ = request(app, "GET", f"/api/events/{ids[0]}/evolution")
    assert status == 200
    # New routes are read-only: no write method is routed.
    status, _, _ = request(app, "POST", "/api/screening/delta", headers={config.API_KEY_HEADER: "s3cret-key"})
    assert status == 405


# --- Provenance risk drivers ----------------------------------------------------

def test_provenance_risk_block_collision_drivers(hist_db):
    from backend.provenance import get_event_provenance
    _, ids = _screen("live", [("1", "2", C, 0, 1.0, "High")])
    risk = get_event_provenance(ids[0])["risk"]
    assert risk["tier"] == "High" and risk["criticality"] == "Tier1"   # existing fields kept
    assert risk["criticality_multiplier"] == 3.0
    assert risk["priority_basis"] == "pc_x_criticality" and risk["event_class"] == C
    assert risk["criticality_effective"] == "Tier1" and risk["criticality_multiplier_effective"] == 3.0
    assert risk["tier_thresholds"] == {"critical": 1e-4, "high": 1e-5, "medium": 1e-6}
    assert risk["dwell_minutes"] is None


def test_provenance_risk_block_proximity_drivers(hist_db):
    from backend.db import upsert_object
    from backend.provenance import get_event_provenance
    upsert_object("1", "ASSET-1", "satellite", "India", None, "l1", "l2")  # NULL criticality
    _, ids = _screen("live", [("1", "3", P, 0, 8.0, "Medium")])
    risk = get_event_provenance(ids[0])["risk"]
    assert risk["priority_basis"] == "proximity_dwell"
    assert risk["criticality_effective"] == "Tier3"
    assert risk["criticality_multiplier_effective"] is None   # not applied to proximity priority
    assert risk["dwell_minutes"] == 30.0
    assert risk["tier_thresholds"] == {"high_dwell_min": 60, "high_max_km": 10}


def test_proximity_tier_thresholds_match_threat_rule():
    from backend.threat import risk_tier_for_proximity
    d, km = PROXIMITY_TIER_THRESHOLDS["high_dwell_min"], PROXIMITY_TIER_THRESHOLDS["high_max_km"]
    assert risk_tier_for_proximity(d + 0.1, km - 0.1) == "High"
    assert risk_tier_for_proximity(d, km - 0.1) == "Medium"
    assert risk_tier_for_proximity(d + 0.1, km) == "Medium"


# --- Real pipeline integration -----------------------------------------------------

from backend.tests.test_demo_seed import demo_db  # noqa: E402,F401  (fixture)


def test_pipeline_records_observations_and_delta_across_runs(demo_db, monkeypatch):
    from backend import pipeline
    from backend.db import get_connection, get_screening_run, list_events
    from backend.demo_seed import seed_event
    from backend.event_history import get_event_evolution

    monkeypatch.setattr(pipeline, "run_ingest", lambda: ({"satellite": 1, "debris": 1, "foreign_sat": 1}, True))

    first = pipeline.run_full_pipeline()
    assert first["delta"]["available"] is False and first["delta"]["reason"] == "no_previous_run"
    second = pipeline.run_full_pipeline()
    assert second["delta"]["available"] is True
    assert second["delta"]["previous_run_id"] == first["screening_run_id"]
    assert get_screening_run(second["screening_run_id"])["observations_recorded"] == 1

    seeded = seed_event(asset_hint="TEST-ASSET", proximity=False, debris_hint="TEST-DEBRIS")
    assert seeded["delta"]["domain"] == "demo" and seeded["delta"]["reason"] == "no_previous_run"
    evo = get_event_evolution(seeded["event_id"])
    assert evo["history_available"] is True and evo["is_demo"] is True
    assert all(o["is_demo"] for o in evo["observations"])

    conn = get_connection()
    try:
        n_obs = conn.execute("SELECT COUNT(*) FROM event_observations WHERE screening_run_id = ?",
                             (seeded["screening_run_id"],)).fetchone()[0]
    finally:
        conn.close()
    assert n_obs == len(list_events())  # one observation per current event
