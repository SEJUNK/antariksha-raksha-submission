"""Schema migration and run-provenance / decision helpers."""

import sqlite3

import pytest


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "db_test.db"
    monkeypatch.setattr("backend.db.DB_PATH", path)
    return path


# Original (pre-provenance) schema, as shipped before these columns existed.
_OLD_SCHEMA = """
CREATE TABLE objects (norad_id TEXT PRIMARY KEY, name TEXT NOT NULL, object_type TEXT NOT NULL,
  owner_country TEXT, criticality TEXT, tle_line1 TEXT NOT NULL, tle_line2 TEXT NOT NULL,
  last_updated TEXT NOT NULL);
CREATE TABLE conjunction_events (id INTEGER PRIMARY KEY AUTOINCREMENT, object_a_id TEXT NOT NULL,
  object_b_id TEXT NOT NULL, event_class TEXT NOT NULL, tca_timestamp TEXT NOT NULL,
  miss_distance_km REAL NOT NULL, rel_velocity_km_s REAL, pc_score REAL NOT NULL,
  risk_tier TEXT NOT NULL, priority_score REAL NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE mission_briefs (event_id INTEGER PRIMARY KEY, brief_text TEXT NOT NULL,
  maneuver_text TEXT NOT NULL, delta_v_ms REAL, status TEXT NOT NULL DEFAULT 'pending',
  reviewer_notes TEXT, generated_by TEXT NOT NULL DEFAULT 'llm');
CREATE TABLE decision_log (id INTEGER PRIMARY KEY AUTOINCREMENT, event_class TEXT NOT NULL,
  object_a_name TEXT, object_b_name TEXT, risk_tier TEXT, pc_score REAL, miss_distance_km REAL,
  generated_by TEXT, decision TEXT NOT NULL, rejection_reason TEXT, decided_at TEXT NOT NULL);
INSERT INTO decision_log (event_class, decision, decided_at) VALUES ('collision_risk', 'approved', '2026-01-01');
"""


def _columns(path, table):
    conn = sqlite3.connect(path)
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


def test_init_db_migrates_old_schema_and_keeps_rows(db_path):
    conn = sqlite3.connect(db_path)
    conn.executescript(_OLD_SCHEMA)
    conn.close()

    from backend.db import init_db, list_all_decisions
    init_db()
    init_db()  # idempotent

    assert {"is_demo", "screening_run_id", "pc_samples", "pc_sigma_km",
            "pc_hard_body_radius_km", "tca_refinement"} <= _columns(db_path, "conjunction_events")
    assert "review_status" in _columns(db_path, "mission_briefs")
    assert {"event_id", "is_demo", "screening_run_id", "tca_timestamp"} <= _columns(db_path, "decision_log")
    for table in ("demo_overrides", "ingest_runs", "screening_runs"):
        assert _columns(db_path, table)

    old = list_all_decisions()
    assert len(old) == 1 and old[0]["event_id"] is None


def test_screening_run_mode_is_constrained(db_path):
    from backend.db import init_db, insert_screening_run
    init_db()
    with pytest.raises(sqlite3.IntegrityError):
        insert_screening_run("2026-01-01T00:00:00+00:00", "bogus")
    with pytest.raises(ValueError):
        insert_screening_run("2026-01-01T00:00:00+00:00", "live", not_a_column=1)


def test_brief_review_status_round_trip(db_path):
    from backend.db import get_event_with_brief, init_db, insert_brief, insert_event, upsert_object
    init_db()
    upsert_object("1", "A", "satellite", "India", "Tier1", "l1", "l2")
    upsert_object("2", "B", "debris", None, None, "l1", "l2")
    eid = insert_event("1", "2", "collision_risk", "2026-01-01T00:00:00+00:00", 0.1, 7.0, 1e-4, "High", 1.0,
                       screening_run_id=5, pc_samples=5000, pc_sigma_km=0.1, pc_hard_body_radius_km=0.02,
                       tca_refinement="refined")
    insert_brief(eid, "brief", "maneuver", 0.05, "llm", reviewer_notes="ok", review_status="consistent")
    evt = get_event_with_brief(eid)
    assert evt["review_status"] == "consistent"
    assert evt["screening_run_id"] == 5
    assert evt["pc_samples"] == 5000
    assert evt["tca_refinement"] == "refined"


def test_demo_override_replaces_previous_and_clears(db_path):
    from backend.db import (
        clear_demo_overrides, init_db, list_demo_overrides, list_objects_with_demo_overrides,
        set_demo_override, upsert_object,
    )
    init_db()
    upsert_object("1", "A", "satellite", "India", "Tier1", "a1", "a2")
    upsert_object("2", "B", "debris", None, None, "b1", "b2")
    upsert_object("3", "C", "foreign_sat", "foreign", None, "c1", "c2")

    set_demo_override("2", "x1", "x2", "collision_demo", "1")
    set_demo_override("3", "y1", "y2", "proximity_demo", "1")
    assert [o["norad_id"] for o in list_demo_overrides()] == ["3"]

    objs = {o["norad_id"]: o for o in list_objects_with_demo_overrides()}
    assert objs["3"]["tle_line1"] == "y1" and objs["3"]["demo_adjusted"] is True
    assert objs["2"]["tle_line1"] == "b1" and "demo_adjusted" not in objs["2"]

    clear_demo_overrides()
    assert list_demo_overrides() == []


def test_run_ingest_records_one_ingest_run(db_path, monkeypatch):
    """run_ingest itself (also used by `python -m backend.ingest`) persists
    the ingest_runs row; used_cache reflects any group's cache fallback."""
    import json

    from backend import ingest
    from backend.db import latest_ingest_run, list_objects

    tles = [
        {"norad_id": "1", "name": "A", "tle_line1": "l1", "tle_line2": "l2", "object_type": "satellite",
         "criticality": "Tier1", "owner_country": "India", "group": "A"},
        {"norad_id": "2", "name": "B", "tle_line1": "l1", "tle_line2": "l2", "object_type": "debris",
         "criticality": None, "owner_country": None, "group": "B"},
    ]

    def fake_fetch():
        ingest.fetch_tles.using_cache = True
        return tles

    monkeypatch.setattr(ingest, "fetch_tles", fake_fetch)
    counts, using_cache = ingest.run_ingest()

    run = latest_ingest_run()
    assert using_cache is True
    assert run["used_cache"] == 1
    assert run["object_count"] == 2
    assert json.loads(run["counts_json"]) == counts == {"A": 1, "B": 1, "C": 0}
    assert "CelesTrak" in run["source"]
    assert len(list_objects()) == 2


def test_decision_stats_recent_rejections_include_is_demo(db_path):
    from backend.db import decision_stats, init_db, insert_decision
    init_db()
    insert_decision("collision_risk", "A", "B", "High", 1e-4, 0.1, "llm", "dismissed",
                    rejection_reason="demo noise", is_demo=True)
    insert_decision("collision_risk", "A", "B", "High", 1e-4, 0.1, "llm", "dismissed",
                    rejection_reason="real", is_demo=False)
    recent = decision_stats()["recent_rejections"]
    assert [(r["rejection_reason"], r["is_demo"]) for r in recent] == [("real", 0), ("demo noise", 1)]
