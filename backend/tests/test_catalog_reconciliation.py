"""Catalog reconciliation: a successful ingest makes exactly the ingested set
active; a failed ingest changes nothing; history is never deleted."""

import math
import sqlite3
from datetime import datetime, timezone

import pytest
from sgp4.api import WGS72, Satrec, jday
from sgp4.exporter import export_tle


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "catalog_test.db"
    monkeypatch.setattr("backend.db.DB_PATH", path)
    return path


def _fresh_tle(satnum, mo_deg):
    now = datetime.now(timezone.utc)
    jd, fr = jday(now.year, now.month, now.day, now.hour, now.minute, now.second)
    sat = Satrec()
    sat.sgp4init(WGS72, "i", satnum, (jd + fr) - 2433281.5, 0.0001, 0.0, 0.0, 0.001,
                 math.radians(20), math.radians(51.6), math.radians(mo_deg), 15.5 * 2 * math.pi / 1440.0,
                 math.radians(10))
    return export_tle(sat)


def _obj(norad_id, group="B", name=None):
    l1, l2 = _fresh_tle(int(norad_id), mo_deg=int(norad_id) % 360)
    types = {"A": ("satellite", "India", "Tier1"), "B": ("debris", None, None), "C": ("foreign_sat", "foreign", None)}
    object_type, owner, crit = types[group]
    return {"norad_id": norad_id, "name": name or f"OBJ-{norad_id}", "tle_line1": l1, "tle_line2": l2,
            "object_type": object_type, "criticality": crit, "owner_country": owner, "group": group,
            "source_format": "tle"}


def _ingest(monkeypatch, objects, using_cache=False):
    from backend import ingest

    def fake_fetch():
        ingest.fetch_tles.using_cache = using_cache
        return [dict(o) for o in objects]

    monkeypatch.setattr(ingest, "fetch_tles", fake_fetch)
    return ingest.run_ingest()


def _active_ids():
    from backend.db import list_objects
    return {o["norad_id"] for o in list_objects()}


def _all_rows():
    from backend.db import list_objects
    return {o["norad_id"]: o for o in list_objects(include_inactive=True)}


A1, D1, D2, C1 = _obj("90001", "A"), _obj("90002", "B"), _obj("90003", "B"), _obj("90004", "C")


def test_ingested_objects_are_active(db_path, monkeypatch):
    _ingest(monkeypatch, [A1, D1, D2, C1])
    assert _active_ids() == {"90001", "90002", "90003", "90004"}
    assert all(r["active"] == 1 for r in _all_rows().values())


def test_object_missing_from_next_ingest_becomes_inactive_not_deleted(db_path, monkeypatch):
    _ingest(monkeypatch, [A1, D1, D2, C1])
    _ingest(monkeypatch, [A1, D1, C1])
    assert _active_ids() == {"90001", "90002", "90004"}
    rows = _all_rows()
    assert rows["90003"]["active"] == 0
    assert rows["90003"]["name"] == "OBJ-90003"  # history still resolves


def test_inactive_object_excluded_from_screening_inputs(db_path, monkeypatch):
    """Everything the pipeline screens/draws comes from list_objects() /
    list_objects_with_demo_overrides() / propagate_all() defaults."""
    from backend.db import list_objects_with_demo_overrides
    from backend.propagate import current_positions, propagate_all

    _ingest(monkeypatch, [A1, D1, D2, C1])
    _ingest(monkeypatch, [A1, D1, C1])

    assert "90003" not in {o["norad_id"] for o in list_objects_with_demo_overrides()}
    assert "90003" in {o["norad_id"] for o in list_objects_with_demo_overrides(include_inactive=True)}
    _, positions = propagate_all(window_hours=1)
    assert set(positions) == {"90001", "90002", "90004"}
    assert {p["norad_id"] for p in current_positions()} == {"90001", "90002", "90004"}


def test_reappearing_object_becomes_active_again(db_path, monkeypatch):
    _ingest(monkeypatch, [A1, D1, D2, C1])
    _ingest(monkeypatch, [A1, D1, C1])
    renamed = dict(D2, name="OBJ-90003-RENAMED")
    _ingest(monkeypatch, [A1, D1, renamed, C1])
    rows = _all_rows()
    assert rows["90003"]["active"] == 1
    assert rows["90003"]["name"] == "OBJ-90003-RENAMED"
    assert _active_ids() == {"90001", "90002", "90003", "90004"}


def _snapshot():
    return {nid: (r["active"], r["tle_line1"], r["last_updated"]) for nid, r in _all_rows().items()}


def test_failed_ingest_exception_leaves_catalog_unchanged(db_path, monkeypatch):
    from backend import ingest
    from backend.db import latest_ingest_run

    _ingest(monkeypatch, [A1, D1, D2, C1])
    before, run_before = _snapshot(), latest_ingest_run()

    def failing_fetch():
        raise RuntimeError("Could not fetch 'groupA_X' from CelesTrak and no cache exists")

    monkeypatch.setattr(ingest, "fetch_tles", failing_fetch)
    with pytest.raises(RuntimeError):
        ingest.run_ingest()
    assert _snapshot() == before
    assert latest_ingest_run()["id"] == run_before["id"]  # no ingest_runs row for a failure


def test_failed_ingest_no_data_leaves_catalog_unchanged(db_path, monkeypatch):
    from backend.db import latest_ingest_run

    _ingest(monkeypatch, [A1, D1, D2, C1])
    before, run_before = _snapshot(), latest_ingest_run()
    with pytest.raises(RuntimeError, match="no objects"):
        _ingest(monkeypatch, [])
    assert _snapshot() == before
    assert latest_ingest_run()["id"] == run_before["id"]


def test_cache_backed_ingest_reconciles_and_is_recorded_as_cached(db_path, monkeypatch):
    from backend.db import latest_ingest_run

    _ingest(monkeypatch, [A1, D1, D2, C1])
    _, using_cache = _ingest(monkeypatch, [A1, D1, C1], using_cache=True)
    assert using_cache is True
    assert latest_ingest_run()["used_cache"] == 1
    assert _active_ids() == {"90001", "90002", "90004"}


def test_reconcile_is_atomic(db_path, monkeypatch):
    """A bad row mid-batch rolls back the whole reconciliation: no partial
    upserts and nothing deactivated."""
    from backend.db import reconcile_catalog

    _ingest(monkeypatch, [A1, D1, D2, C1])
    before = _snapshot()
    bad = dict(_obj("90005"), object_type="not-a-type")  # violates CHECK constraint
    with pytest.raises(sqlite3.IntegrityError):
        reconcile_catalog([A1, bad])
    assert _snapshot() == before


def test_reconcile_refuses_empty_set(db_path, monkeypatch):
    from backend.db import reconcile_catalog

    _ingest(monkeypatch, [A1, D1])
    with pytest.raises(ValueError):
        reconcile_catalog([])
    assert _active_ids() == {"90001", "90002"}


def test_existing_db_migrates_with_all_objects_active(db_path):
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE objects (norad_id TEXT PRIMARY KEY, name TEXT NOT NULL, object_type TEXT NOT NULL,
          owner_country TEXT, criticality TEXT, tle_line1 TEXT NOT NULL, tle_line2 TEXT NOT NULL,
          last_updated TEXT NOT NULL);
        INSERT INTO objects VALUES ('1', 'OLD', 'debris', NULL, NULL, 'l1', 'l2', '2026-01-01');
    """)
    conn.commit()
    conn.close()

    from backend.db import init_db, list_objects
    init_db()
    init_db()  # idempotent
    [row] = list_objects()
    assert row["norad_id"] == "1" and row["active"] == 1
