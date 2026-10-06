"""LIVE/DEMO isolation through the HTTP API, end to end:

live refresh -> demo history step 1 -> step 2 -> evolution -> demo delta ->
exit demo (refresh) -> live refresh -> real delta -> live evolution.

Real screening/risk pipeline on a synthetic-TLE DB; only ingest (network) is
stubbed (it records a live ingest_runs row) and Ollama is unreachable. The
catalog includes a co-orbital foreign satellite ~15 km from the asset so every
run (live and demo) has a real proximity event to track.
"""

import hashlib
import json
import math

import pytest

from backend.tests._asgi import request
from backend.tests.test_demo_seed import _make_tle, demo_db  # noqa: F401  (fixture)


@pytest.fixture
def iso_api(demo_db, monkeypatch):
    from backend import demo_seed, main, pipeline
    from backend.db import insert_ingest_run, upsert_object

    # Co-orbital wingman 15 km along-track (inside 25 km proximity watch,
    # outside the 10 km collision threshold).
    offset_deg = math.degrees(15.0 / 6900.0)
    l1, l2 = _make_tle(90004, mo_deg=offset_deg)
    upsert_object("90004", "TEST-WINGMAN", "foreign_sat", "foreign", None, l1, l2)

    def fake_ingest():
        insert_ingest_run(source="stub", used_cache=False, object_count=4,
                          counts={"A": 1, "B": 1, "C": 2}, source_format="tle",
                          resolution={"A:TEST-ASSET-SAT": "90001"})  # as a real ingest records it
        return {"A": 1, "B": 1, "C": 2}, False

    monkeypatch.setattr(pipeline, "run_ingest", fake_ingest)
    monkeypatch.setattr("backend.config.API_KEY", "")
    real = demo_seed.seed_event
    monkeypatch.setattr(demo_seed, "seed_event", lambda **kw: real(
        asset_hint="TEST-ASSET", debris_hint="TEST-DEBRIS", foreign_hint="TEST-FOREIGN", **kw))

    def call(method, path):
        status, _, body = request(main.app, method, path)
        assert status == 200, (path, status, body)
        return body
    return call


def _objects_hash():
    from backend.db import get_connection
    conn = get_connection()
    try:
        rows = conn.execute("SELECT norad_id, tle_line1, tle_line2, omm_json, active FROM objects "
                            "ORDER BY norad_id").fetchall()
    finally:
        conn.close()
    return hashlib.sha256(json.dumps([tuple(r) for r in rows]).encode()).hexdigest()


def _scalar(sql):
    from backend.db import get_connection
    conn = get_connection()
    try:
        return conn.execute(sql).fetchone()[0]
    finally:
        conn.close()


def _proximity_event(events):
    return next(e for e in events if e["event_class"] == "proximity_watch")


def test_live_demo_live_sequence_never_mixes_domains(iso_api):
    call = iso_api
    before = _objects_hash()

    live1 = call("POST", "/api/refresh")
    assert live1["mode"] == "live"
    live_prox_id = _proximity_event(call("GET", "/api/events"))["id"]

    d1 = call("POST", "/api/demo/seed?mode=collision&history_step=1")
    d2 = call("POST", "/api/demo/seed?mode=collision&history_step=2")
    assert d1["mode"] == d2["mode"] == "demo"
    assert call("GET", "/api/status")["mode"] == "demo"
    events = call("GET", "/api/events")
    assert events and all(e["is_demo"] for e in events)

    evo = call("GET", f"/api/events/{d2['event_id']}/evolution")
    assert evo["is_demo"] is True and len(evo["observations"]) == 2
    assert {o["run_mode"] for o in evo["observations"]} == {"demo"}
    assert all(o["is_demo"] for o in evo["observations"])
    # The real proximity pair screened inside the demo run is a demo observation
    # on a demo-only track (step 1 reset), never the live track.
    demo_prox = call("GET", f"/api/events/{_proximity_event(events)['id']}/evolution")
    assert {o["run_mode"] for o in demo_prox["observations"]} == {"demo"}

    ddelta = call("GET", "/api/screening/delta?domain=demo")
    assert ddelta["domain"] == "demo"
    assert ddelta["latest_run"]["mode"] == ddelta["previous_run"]["mode"] == "demo"
    assert ddelta["counts"]["increased"] == 1
    assert sum(1 for o in call("GET", "/api/objects") if o.get("demo_adjusted")) == 1
    assert _objects_hash() == before

    exit_run = call("POST", "/api/refresh")  # "exit demo"
    assert exit_run["mode"] == "live" and exit_run["delta"]["domain"] == "real"
    events = call("GET", "/api/events")
    assert events and not any(e["is_demo"] for e in events)
    assert not any(o.get("demo_adjusted") for o in call("GET", "/api/objects"))
    from backend.db import list_demo_overrides
    assert list_demo_overrides() == []

    live3 = call("POST", "/api/refresh")
    rdelta = call("GET", "/api/screening/delta?domain=real")
    assert rdelta["domain"] == "real"
    assert rdelta["latest_run"]["id"] == live3["screening_run_id"]
    assert rdelta["previous_run"]["id"] == exit_run["screening_run_id"]
    assert rdelta["latest_run"]["mode"] == rdelta["previous_run"]["mode"] == "live"
    assert all(not it["is_demo"] for bucket in rdelta["items"].values() for it in bucket)
    assert call("GET", "/api/screening/delta")["domain"] == "real"

    # Live evolution: the proximity track spans the three live runs only.
    now_prox = _proximity_event(call("GET", "/api/events"))
    live_evo = call("GET", f"/api/events/{now_prox['id']}/evolution")
    assert live_evo["is_demo"] is False and live_evo["demo_history"] is None
    assert {o["run_mode"] for o in live_evo["observations"]} == {"live"}
    assert len(live_evo["observations"]) == 3
    first_live = call("GET", f"/api/events/{live_prox_id}/evolution")
    assert first_live["track_id"] == live_evo["track_id"]

    # Database-level invariants.
    assert _scalar("SELECT COUNT(*) FROM event_observations o JOIN event_observations p "
                   "ON p.id = o.prev_observation_id WHERE o.is_demo != p.is_demo") == 0
    assert _scalar("SELECT COUNT(*) FROM event_observations o JOIN screening_runs r "
                   "ON r.id = o.screening_run_id WHERE (r.mode = 'demo') != (o.is_demo = 1)") == 0
    assert _scalar("SELECT COUNT(*) FROM screening_runs WHERE mode != 'demo' "
                   "AND demo_scenario_json IS NOT NULL") == 0
    assert _scalar("SELECT COUNT(*) FROM conjunction_events WHERE is_demo = 1") == 0
    assert _objects_hash() == before
