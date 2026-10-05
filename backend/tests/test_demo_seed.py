"""Demo-scenario reliability tests (collision_demo / proximity_demo).

Exercises the REAL screening/risk/brief pipeline (backend.pipeline) against
synthetic-but-valid TLEs in a throwaway SQLite DB -- no network, no Ollama
(brief generation falls back to the deterministic template when Ollama is
unreachable, which it always is in this test environment).
"""

import json
import math
from datetime import datetime, timezone

import pytest
import requests
from sgp4.api import WGS72, Satrec, jday
from sgp4.exporter import export_tle


def _make_tle(satnum, epoch_days_ago=0.0, inclo_deg=51.6, raan_deg=10.0,
              argp_deg=20.0, mo_deg=30.0, mean_motion_rev_per_day=15.5,
              ecco=0.001, bstar=0.0001):
    """Build a valid, propagatable synthetic TLE pair via sgp4init + export_tle
    -- same mechanism backend/demo_seed.py itself uses to create its seeded
    object, so this matches how the real code generates orbits."""
    now = datetime.now(timezone.utc)
    jd, fr = jday(now.year, now.month, now.day, now.hour, now.minute, now.second)
    epoch = (jd + fr) - 2433281.5 - epoch_days_ago
    no_kozai = mean_motion_rev_per_day * 2 * math.pi / 1440.0
    sat = Satrec()
    sat.sgp4init(
        WGS72, "i", satnum, epoch, bstar, 0.0, 0.0, ecco,
        math.radians(argp_deg), math.radians(inclo_deg), math.radians(mo_deg),
        no_kozai, math.radians(raan_deg),
    )
    return export_tle(sat)


@pytest.fixture
def demo_db(tmp_path, monkeypatch):
    """Point the whole backend at a throwaway SQLite DB for this test, and
    seed it with one Tier1 Indian asset, one debris object, and one foreign
    satellite -- all with fresh (today's) TLE epochs.

    backend.db does `from backend.config import DB_PATH`, which binds its own
    module-level name at import time -- patching backend.config.DB_PATH
    afterwards would NOT affect backend.db's already-bound name, so the
    patch target must be backend.db.DB_PATH itself."""
    db_path = tmp_path / "demo_test.db"
    monkeypatch.setattr("backend.db.DB_PATH", db_path)

    # Fail fast instead of waiting out OLLAMA_TIMEOUT_SECONDS per call --
    # brief generation must fall back to the deterministic template either
    # way, but a test run shouldn't depend on how quickly a real connection
    # to a nonexistent/unresponsive localhost:11434 actually fails.
    def _no_ollama(*args, **kwargs):
        raise requests.exceptions.ConnectionError("Ollama unavailable in tests")
    monkeypatch.setattr("backend.brief_agent.requests.post", _no_ollama)

    from backend.db import init_db, upsert_object
    init_db()

    asset_l1, asset_l2 = _make_tle(90001, mo_deg=0.0)
    debris_l1, debris_l2 = _make_tle(90002, mo_deg=90.0)
    foreign_l1, foreign_l2 = _make_tle(90003, mo_deg=180.0)

    upsert_object("90001", "TEST-ASSET-SAT", "satellite", "India", "Tier1", asset_l1, asset_l2)
    upsert_object("90002", "TEST-DEBRIS-FRAG", "debris", None, None, debris_l1, debris_l2)
    upsert_object("90003", "TEST-FOREIGN-SAT", "foreign_sat", "foreign", None, foreign_l1, foreign_l2)

    return tmp_path / "demo_test.db"


def test_collision_demo_produces_demo_flagged_collision_event(demo_db):
    from backend.db import get_event_with_brief
    from backend.demo_seed import seed_event

    result = seed_event(asset_hint="TEST-ASSET", proximity=False, debris_hint="TEST-DEBRIS")

    assert result["simulation_adjustment"] is True
    assert result["scenario"] == "collision_demo"
    assert result["expected_event_class"] == "collision_risk"

    event = get_event_with_brief(result["event_id"])
    assert event is not None
    assert event["event_class"] == "collision_risk"
    # Produced via the real screening pipeline, correctly tagged as demo.
    assert bool(event["is_demo"]) is True
    assert event["brief_text"]  # brief generation ran (template fallback is fine)


def test_proximity_demo_produces_demo_flagged_proximity_event(demo_db):
    from backend.db import get_event_with_brief
    from backend.demo_seed import seed_event

    result = seed_event(asset_hint="TEST-ASSET", proximity=True, foreign_hint="TEST-FOREIGN")

    assert result["scenario"] == "proximity_demo"
    assert result["expected_event_class"] == "proximity_watch"

    event = get_event_with_brief(result["event_id"])
    assert event is not None
    assert event["event_class"] == "proximity_watch"
    assert bool(event["is_demo"]) is True
    assert event["brief_text"]


def test_simulation_adjustment_is_explicitly_represented(demo_db):
    from backend.demo_seed import seed_event

    result = seed_event(asset_hint="TEST-ASSET", proximity=False, debris_hint="TEST-DEBRIS")

    assert result["asset"]["norad_id"] == "90001"
    assert result["adjusted_object"]["norad_id"] == "90002"
    assert result["separation_km"] > 0
    assert "new_tle" in result and len(result["new_tle"]) == 2


def test_demo_collision_pc_is_deterministic_across_runs(demo_db):
    """The seeded collision geometry is identical on each run and the
    analytic Pc indicator is deterministic, so the risk tier shown to the
    jury doesn't flicker between an identical back-to-back re-trigger. Pc
    can differ in the ~6th significant figure only because sigma grows with
    time-to-TCA and the second run starts milliseconds later."""
    from backend.db import get_event_with_brief
    from backend.demo_seed import seed_event

    result1 = seed_event(asset_hint="TEST-ASSET", proximity=False, debris_hint="TEST-DEBRIS")
    event1 = get_event_with_brief(result1["event_id"])

    result2 = seed_event(asset_hint="TEST-ASSET", proximity=False, debris_hint="TEST-DEBRIS")
    event2 = get_event_with_brief(result2["event_id"])

    assert event1["pc_score"] > 0.0
    assert abs(event1["pc_score"] - event2["pc_score"]) <= 1e-4 * event1["pc_score"]
    assert event1["risk_tier"] == event2["risk_tier"]


def _objects_snapshot():
    from backend.db import get_connection
    conn = get_connection()
    try:
        return [tuple(r) for r in conn.execute("SELECT * FROM objects ORDER BY norad_id").fetchall()]
    finally:
        conn.close()


def test_demo_never_modifies_real_objects_table(demo_db):
    """The perturbed TLE lives only in demo_overrides; the real catalog rows
    (including last_updated) are byte-identical after a demo."""
    from backend.db import list_demo_overrides, list_objects_with_demo_overrides
    from backend.demo_seed import seed_event

    before = _objects_snapshot()
    result = seed_event(asset_hint="TEST-ASSET", proximity=False, debris_hint="TEST-DEBRIS")
    assert _objects_snapshot() == before
    assert result["real_catalog_modified"] is False

    overrides = list_demo_overrides()
    assert len(overrides) == 1
    assert overrides[0]["norad_id"] == "90002"
    assert overrides[0]["scenario"] == "collision_demo"
    assert overrides[0]["derived_from_norad_id"] == "90001"
    assert [overrides[0]["tle_line1"], overrides[0]["tle_line2"]] == result["new_tle"]

    adjusted = {o["norad_id"]: o for o in list_objects_with_demo_overrides()}
    assert adjusted["90002"]["demo_adjusted"] is True
    assert adjusted["90002"]["tle_line1"] == result["new_tle"][0]
    assert "demo_adjusted" not in adjusted["90001"]


def test_normal_screening_after_demo_ignores_and_clears_override(demo_db):
    """Regression for the demo-leak bug: previously the demo overwrote the
    real objects row, so a later normal screening reported the fake
    conjunction as REAL (is_demo=0). Now a normal screening clears the
    override, screens only real TLEs, and marks nothing as demo."""
    from backend.db import latest_screening_run, list_demo_overrides, list_events
    from backend.demo_seed import seed_event
    from backend.pipeline import run_screening_and_briefs

    before = _objects_snapshot()
    seed_event(asset_hint="TEST-ASSET", proximity=False, debris_hint="TEST-DEBRIS")
    assert any(e["is_demo"] for e in list_events())

    summary = run_screening_and_briefs()

    assert list_demo_overrides() == []
    assert _objects_snapshot() == before
    events = list_events()
    assert all(bool(e["is_demo"]) is False for e in events)
    # The real fixture orbits are 90 deg apart in phase: no real conjunction.
    assert not any({e["object_a_id"], e["object_b_id"]} == {"90001", "90002"} for e in events)
    assert summary["mode"] in ("live", "cached")
    assert latest_screening_run()["mode"] != "demo"


def test_demo_collision_event_carries_refined_tca_provenance(demo_db):
    from datetime import datetime

    from backend.config import PC_N_SAMPLES
    from backend.db import get_event_with_brief, get_screening_run
    from backend.demo_seed import seed_event

    result = seed_event(asset_hint="TEST-ASSET", proximity=False, debris_hint="TEST-DEBRIS")
    event = get_event_with_brief(result["event_id"])

    assert event["pc_samples"] is None  # analytic indicator: no sampling
    assert "analytic encounter-plane" in event["pc_method"].lower()
    assert event["pc_sigma_km"] > 0
    assert event["pc_hard_body_radius_km"] > 0
    assert "SGP4" in event["tca_refinement"]
    assert datetime.fromisoformat(event["tca_timestamp"]).tzinfo is not None
    # ~20 m seeded along-track offset; refined miss distance reflects it.
    assert event["miss_distance_km"] < 0.1

    run = get_screening_run(event["screening_run_id"])
    assert run["mode"] == "demo"
    assert run["collision_events"] >= 1
    assert run["candidate_pairs"] == 2  # 1 asset x (1 debris + 1 foreign)
    assert json.loads(run["demo_scenario_json"])["scenario"] == "collision_demo"


def test_collision_demo_is_genuine_crossing_captured_by_screening(demo_db):
    """The seeded geometry is a real crossing ~6 h ahead: screening's refined
    TCA must land on the designed encounter (well inside +/-1 grid step),
    with miss distance and relative velocity consistent with the design --
    not a co-orbital formation with an arbitrary 'TCA'."""
    from datetime import timedelta

    from backend.db import get_event_with_brief, list_events
    from backend.demo_seed import COLLISION_ENCOUNTER_LEAD_HOURS, COLLISION_PLANE_OFFSET_DEG

    from backend.demo_seed import seed_event

    start = datetime.now(timezone.utc)
    result = seed_event(asset_hint="TEST-ASSET", proximity=False, debris_hint="TEST-DEBRIS")
    event = get_event_with_brief(result["event_id"])

    assert result["geometry"] == "crossing"
    # Next whole UTC hour at least the configured lead time ahead.
    assert COLLISION_ENCOUNTER_LEAD_HOURS <= result["encounter_lead_hours"] < COLLISION_ENCOUNTER_LEAD_HOURS + 1
    assert abs(result["plane_offset_deg"] - COLLISION_PLANE_OFFSET_DEG) < 0.01

    tca = datetime.fromisoformat(event["tca_timestamp"])
    design_tca = datetime.fromisoformat(result["design_tca"])
    expected = start + timedelta(hours=result["encounter_lead_hours"])
    assert abs((tca - expected).total_seconds()) < 60
    secs_into_hour = tca.minute * 60 + tca.second
    assert min(secs_into_hour, 3600 - secs_into_hour) < 60  # designed for a whole UTC hour
    assert abs((tca - design_tca).total_seconds()) < 0.01

    # Miss distance: tens of metres, matching the design check of the exported TLE.
    assert 0.005 < event["miss_distance_km"] < 0.05
    assert abs(event["miss_distance_km"] - result["design_miss_distance_km"]) < 1e-4
    # Genuine crossing speed, below the grid-aliasing limit for the 10 km threshold.
    assert 0.1 < event["rel_velocity_km_s"] < 0.33
    assert abs(event["rel_velocity_km_s"] - result["design_rel_velocity_km_s"]) < 1e-3

    # Unique encounter for the pair (periods differ, so no repeat each orbit).
    pair = [e for e in list_events() if {e["object_a_id"], e["object_b_id"]} == {"90001", "90002"}]
    assert len(pair) == 1


def test_screening_run_records_catalog_provenance(demo_db):
    from backend.db import insert_ingest_run, latest_screening_run
    from backend.pipeline import run_screening_and_briefs

    ingest_id = insert_ingest_run("CelesTrak", used_cache=True, object_count=3, counts={"A": 1, "B": 1, "C": 1})
    run_screening_and_briefs()
    run = latest_screening_run()

    assert run["mode"] == "cached"  # latest ingest fell back to cache
    assert run["ingest_run_id"] == ingest_id
    assert run["object_count"] == 3
    assert run["objects_screened"] == 3
    assert run["objects_skipped_stale"] == 0
    assert json.loads(run["counts_by_type_json"]) == {"satellite": 1, "debris": 1, "foreign_sat": 1}
    assert run["tle_age_max_days"] < 1.0
    assert run["completed_at"] is not None
    assert run["demo_scenario_json"] is None


def test_demo_scenario_failure_on_missing_target_type(tmp_path, monkeypatch):
    """No object of the required type exists at all (e.g. no foreign
    satellites ingested) -- seed_event must raise a clean RuntimeError, not
    some opaque lookup exception."""
    monkeypatch.setattr("backend.db.DB_PATH", tmp_path / "demo_test_missing.db")
    from backend.db import init_db, upsert_object
    from backend.demo_seed import seed_event

    init_db()
    asset_l1, asset_l2 = _make_tle(90001, mo_deg=0.0)
    debris_l1, debris_l2 = _make_tle(90002, mo_deg=90.0)
    upsert_object("90001", "TEST-ASSET-SAT", "satellite", "India", "Tier1", asset_l1, asset_l2)
    upsert_object("90002", "TEST-DEBRIS-FRAG", "debris", None, None, debris_l1, debris_l2)
    # No foreign_sat object in this DB at all.

    with pytest.raises(RuntimeError):
        seed_event(asset_hint="TEST-ASSET", proximity=True)


def test_demo_scenario_failure_when_asset_tle_too_stale_to_propagate(demo_db, monkeypatch):
    """If the protected asset's TLE is older than propagate_all's staleness
    cutoff, it's silently dropped from the propagation grid -- screening then
    can't produce any event for it at all. seed_event must surface this as a
    clear DemoScenarioError rather than returning a "success" with no event,
    or crashing when it later tries to look up a nonexistent event_id."""
    from backend.db import upsert_object
    from backend.demo_seed import DemoScenarioError, seed_event
    from backend.propagate import MAX_TLE_AGE_DAYS

    stale_l1, stale_l2 = _make_tle(90001, epoch_days_ago=MAX_TLE_AGE_DAYS + 5, mo_deg=0.0)
    upsert_object("90001", "TEST-ASSET-SAT", "satellite", "India", "Tier1", stale_l1, stale_l2)

    with pytest.raises(DemoScenarioError):
        seed_event(asset_hint="TEST-ASSET", proximity=False, debris_hint="TEST-DEBRIS")
