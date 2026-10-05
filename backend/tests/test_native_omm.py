"""Native OMM support: OMM elements are stored and propagated as OMM (no
generated TLE), 6-digit catalog numbers are kept, TLE behaviour is
bit-identical, and the element format is exposed in provenance. Local
fixtures only -- no network."""

import json
import math
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest
from sgp4.api import WGS72, Satrec, jday
from sgp4.exporter import export_tle
from skyfield.api import EarthSatellite

from backend.orbital_formats import (
    omm_record,
    parse_omm_records,
    parse_tle_text,
    satrec_for,
    validate_omm,
)
from backend.propagate import _TS, satellite_for

FIXTURES = Path(__file__).resolve().parent / "fixtures"
TLE_TEXT = (FIXTURES / "celestrak_sample.tle").read_text(encoding="utf-8")
OMM_TEXT = (FIXTURES / "celestrak_sample_omm.json").read_text(encoding="utf-8")


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.db.DB_PATH", tmp_path / "omm_test.db")
    from backend.db import init_db
    init_db()
    return tmp_path / "omm_test.db"


def _fresh_omm(norad, name, mean_anomaly=0.0):
    """A current-epoch OMM record (so propagate_all's stale filter keeps it)."""
    epoch = datetime.now(timezone.utc).replace(microsecond=0)
    return {
        "OBJECT_NAME": name, "OBJECT_ID": "2026-001A", "EPOCH": epoch.strftime("%Y-%m-%dT%H:%M:%S.%f"),
        "MEAN_MOTION": 14.8, "ECCENTRICITY": 0.0012, "INCLINATION": 97.4, "RA_OF_ASC_NODE": 120.0,
        "ARG_OF_PERICENTER": 90.0, "MEAN_ANOMALY": mean_anomaly, "EPHEMERIS_TYPE": 0,
        "CLASSIFICATION_TYPE": "U", "NORAD_CAT_ID": norad, "ELEMENT_SET_NO": 999, "REV_AT_EPOCH": 10,
        "BSTAR": 0.0001, "MEAN_MOTION_DOT": 0.00001, "MEAN_MOTION_DDOT": 0,
    }


def _fresh_tle(satnum, mo_deg=0.0):
    now = datetime.now(timezone.utc)
    jd, fr = jday(now.year, now.month, now.day, now.hour, now.minute, now.second)
    sat = Satrec()
    sat.sgp4init(WGS72, "i", satnum, (jd + fr) - 2433281.5, 0.0001, 0.0, 0.0, 0.001,
                 math.radians(20), math.radians(97.4), math.radians(mo_deg), 14.8 * 2 * math.pi / 1440.0,
                 math.radians(10))
    return export_tle(sat)


def _catalog_obj(record, group="B"):
    types = {"A": ("satellite", "India", "Tier2"), "B": ("debris", None, None), "C": ("foreign_sat", "foreign", None)}
    object_type, owner, crit = types[group]
    return {**record, "object_type": object_type, "owner_country": owner, "criticality": crit, "group": group}


def _tle_record(satnum, name, mo_deg=0.0):
    l1, l2 = _fresh_tle(satnum, mo_deg)
    [rec] = parse_tle_text(f"{name}\n{l1}\n{l2}\n")
    return rec


# --- keys / records ------------------------------------------------------------

def test_five_digit_tle_object_key_and_storage(db):
    from backend.db import list_objects, reconcile_catalog
    from backend.propagate import propagate_all

    rec = _tle_record(5, "VANGUARD-LIKE")
    assert rec["norad_id"] == "00005" and rec["source_format"] == "tle"
    reconcile_catalog([_catalog_obj(rec)])
    [row] = list_objects()
    assert row["norad_id"] == "00005"
    assert row["source_format"] == "tle" and row["omm_json"] is None
    assert row["tle_line1"] == rec["tle_line1"] and row["tle_line2"] == rec["tle_line2"]
    _, positions = propagate_all(window_hours=1, objects=[row])
    assert positions["00005"].shape == (61, 3)


def test_omm_and_tle_keys_identical_for_five_digit_ids():
    assert omm_record(_fresh_omm(5, "X"))["norad_id"] == "00005"
    assert omm_record(_fresh_omm(44804, "X"))["norad_id"] == "44804"
    assert omm_record(_fresh_omm(270000, "X"))["norad_id"] == "270000"


def test_omm_object_stored_natively_and_propagated(db):
    from backend.db import list_objects, reconcile_catalog
    from backend.propagate import current_positions, propagate_all, tle_age_days_for

    rec = omm_record(_fresh_omm(44000, "OMM-SAT"))
    reconcile_catalog([_catalog_obj(rec, "C")])
    [row] = list_objects()
    assert row["source_format"] == "omm"
    assert row["tle_line1"] == "" and row["tle_line2"] == ""  # never a generated TLE
    assert json.loads(row["omm_json"])["NORAD_CAT_ID"] == 44000
    _, positions = propagate_all(window_hours=1, objects=[row])
    alt = np.linalg.norm(positions["44000"], axis=1) - 6378.137
    assert 500 < alt.min() < alt.max() < 1000
    assert [p["norad_id"] for p in current_positions([row])] == ["44000"]
    assert 0 <= tle_age_days_for(row) < 0.1


def test_six_digit_omm_object_end_to_end(db):
    """A catalog number beyond the TLE field is kept as a string key and
    flows through storage, propagation, positions, tracks and screening."""
    from backend.conjunction import find_close_approaches
    from backend.db import list_objects, reconcile_catalog
    from backend.propagate import (
        current_positions,
        make_state_functions,
        position_tracks,
        propagate_all,
        separation_profile,
    )

    six = omm_record(_fresh_omm(270000, "SIX-DIGIT DEB"))
    # Group A asset 1 km along-track from it (0.0084 deg mean anomaly ~ 1 km).
    asset = omm_record(_fresh_omm(44001, "ASSET", mean_anomaly=0.0084))
    reconcile_catalog([_catalog_obj(six, "B"), _catalog_obj(asset, "A")])
    rows = list_objects()
    assert {r["norad_id"] for r in rows} == {"270000", "44001"}

    times, positions = propagate_all(window_hours=1, objects=rows)
    assert set(positions) == {"270000", "44001"}
    assert {p["norad_id"] for p in current_positions(rows)} == {"270000", "44001"}
    assert {t["norad_id"] for t in position_tracks(window_minutes=10, objects=rows)} == {"270000", "44001"}
    prof = separation_profile("44001", "270000", times[10].isoformat(), objects=rows)
    assert prof and min(prof["sep_km"]) < 5
    events = find_close_approaches(times, positions, threshold_km=10.0, step_seconds=60,
                                   state_fns=make_state_functions(rows), objects=rows)
    assert any({e["object_a_id"], e["object_b_id"]} == {"44001", "270000"} for e in events)


# --- equivalence / bit-identity -------------------------------------------------

def test_tle_and_omm_equivalent_propagation_within_tolerance():
    tle = {r["norad_id"]: r for r in parse_tle_text(TLE_TEXT)}
    omm_recs = {r["norad_id"]: r for r in parse_omm_records(OMM_TEXT)[0]}
    for nid, t in tle.items():
        a, b = satellite_for(t), satellite_for(omm_recs[nid])
        assert b.epoch.tt == pytest.approx(a.epoch.tt, abs=1e-8)
        times = _TS.tt_jd(a.epoch.tt + np.linspace(0.0, 3.0, 37))
        pa, pb = a.at(times), b.at(times)
        assert np.abs(pa.position.km - pb.position.km).max() < 1e-5, nid       # < 1 cm
        assert np.abs(pa.velocity.km_per_s - pb.velocity.km_per_s).max() < 1e-8, nid


def test_satellite_for_tle_is_bit_identical_to_legacy_constructor():
    """The TLE path must give exactly the pre-OMM results: same constructor,
    identical arrays (not merely close)."""
    for t in parse_tle_text(TLE_TEXT):
        legacy = EarthSatellite(t["tle_line1"], t["tle_line2"], t["name"], _TS)
        new = satellite_for(t)
        times = _TS.tt_jd(legacy.epoch.tt + np.linspace(-1.0, 3.0, 97))
        assert np.array_equal(legacy.at(times).position.km, new.at(times).position.km)
        assert np.array_equal(legacy.at(times).velocity.km_per_s, new.at(times).velocity.km_per_s)
        assert legacy.epoch.tt == new.epoch.tt
        # A DB row without source_format (pre-migration shape) is TLE.
        row = {k: v for k, v in t.items() if k not in ("source_format", "omm")}
        assert np.array_equal(satellite_for(row).at(times).position.km, legacy.at(times).position.km)


def test_satrec_for_matches_twoline2rv_for_tle():
    for t in parse_tle_text(TLE_TEXT):
        ref = Satrec.twoline2rv(t["tle_line1"], t["tle_line2"])
        got = satrec_for(t)
        assert got.sgp4(ref.jdsatepoch, ref.jdsatepochF + 0.5) == ref.sgp4(ref.jdsatepoch, ref.jdsatepochF + 0.5)


# --- validation -------------------------------------------------------------------

@pytest.mark.parametrize("change,reason", [
    ({"MEAN_MOTION": None}, "missing required fields"),
    ({"ECCENTRICITY": 1.2}, "ECCENTRICITY"),
    ({"MEAN_MOTION": -1.0}, "MEAN_MOTION"),
    ({"INCLINATION": 200.0}, "INCLINATION"),
    ({"NORAD_CAT_ID": "abc"}, "not an integer"),
    ({"NORAD_CAT_ID": 0}, "outside"),
    ({"BSTAR": "x"}, "not numeric"),
    ({"EPOCH": "yesterday"}, "initialise"),
])
def test_invalid_omm_rejected_with_clear_reason(change, reason):
    rec = {**_fresh_omm(44002, "BAD"), **change}
    good = _fresh_omm(44003, "GOOD")
    records, rejected = parse_omm_records(json.dumps([rec, good]))
    assert [r["norad_id"] for r in records] == ["44003"]
    assert len(rejected) == 1 and reason in rejected[0]["reason"]
    assert rejected[0]["name"] == "BAD"
    with pytest.raises(ValueError, match=reason):
        validate_omm(rec)


def test_ingest_omm_mode_keeps_six_digit_and_reports_rejections(monkeypatch, tmp_path, db):
    from backend import ingest
    from backend.db import latest_ingest_run, list_objects

    bad = {**_fresh_omm(44009, "BROKEN"), "ECCENTRICITY": 3.0}
    payload = json.loads(OMM_TEXT) + [bad]
    ws = {"group_a": [{"name_query": "CARTOSAT-3", "exact_match": "CARTOSAT-3", "criticality": "Tier2"}],
          "group_b_debris_groups": [{"celestrak_group": "test", "max_count": 50}], "group_c": []}
    ws_path = tmp_path / "ws.json"
    ws_path.write_text(json.dumps(ws), encoding="utf-8")
    monkeypatch.setattr(ingest, "WORKING_SET_PATH", ws_path)
    monkeypatch.setattr(ingest, "TLE_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(ingest, "CELESTRAK_FORMAT", "omm")

    class _Resp:
        text = json.dumps(payload)

        def raise_for_status(self):
            pass

    monkeypatch.setattr(ingest.requests, "get", lambda url, timeout=None: _Resp())
    ingest.run_ingest()
    rows = {o["norad_id"]: o for o in list_objects()}
    assert "270000" in rows and rows["270000"]["source_format"] == "omm"
    run = latest_ingest_run()
    assert run["source_format"] == "omm"
    rejected = json.loads(run["rejected_records_json"])
    assert any(r["name"] == "BROKEN" and "ECCENTRICITY" in r["reason"] for r in rejected)


# --- schema / storage -------------------------------------------------------------

def test_legacy_objects_table_migrates_additively(tmp_path, monkeypatch):
    path = tmp_path / "legacy.db"
    monkeypatch.setattr("backend.db.DB_PATH", path)
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE objects (norad_id TEXT PRIMARY KEY, name TEXT NOT NULL, object_type TEXT NOT NULL,
          owner_country TEXT, criticality TEXT, tle_line1 TEXT NOT NULL, tle_line2 TEXT NOT NULL,
          last_updated TEXT NOT NULL);
        CREATE TABLE ingest_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, completed_at TEXT NOT NULL,
          source TEXT, used_cache INTEGER NOT NULL DEFAULT 0, object_count INTEGER, counts_json TEXT);
        INSERT INTO objects VALUES ('25544', 'ISS', 'foreign_sat', NULL, NULL, 'l1', 'l2', '2026-01-01');
        INSERT INTO ingest_runs (completed_at, used_cache) VALUES ('2026-01-01', 0);
    """)
    conn.commit()
    conn.close()

    from backend.db import init_db, latest_ingest_run, list_objects, upsert_object
    init_db()
    init_db()  # idempotent
    [row] = list_objects()
    assert row["source_format"] == "tle" and row["omm_json"] is None and row["tle_line1"] == "l1"
    assert latest_ingest_run()["id"] == 1  # legacy row (status NULL) counts as ok
    upsert_object("270000", "SIX", "debris", None, None, None, None, source_format="omm",
                  omm=validate_omm(_fresh_omm(270000, "SIX")))
    assert {o["norad_id"]: o["source_format"] for o in list_objects()} == {"25544": "tle", "270000": "omm"}


@pytest.mark.parametrize("obj,match", [
    ({"source_format": "omm", "omm": None, "tle_line1": None, "tle_line2": None}, "OMM elements"),
    ({"source_format": "tle", "tle_line1": "", "tle_line2": ""}, "tle_line1"),
    ({"source_format": "xml", "tle_line1": "a", "tle_line2": "b"}, "source_format"),
])
def test_reconcile_rejects_inconsistent_rows_without_changes(db, obj, match):
    from backend.db import list_objects, reconcile_catalog

    good = _catalog_obj(_tle_record(90001, "GOOD"))
    reconcile_catalog([good])
    bad = {**_catalog_obj(_tle_record(90002, "BAD")), **obj}
    with pytest.raises(ValueError, match=match):
        reconcile_catalog([good, bad])
    assert [o["norad_id"] for o in list_objects()] == ["90001"]


# --- provenance ---------------------------------------------------------------------

def test_source_format_in_object_and_run_provenance(db):
    from backend.db import insert_ingest_run, insert_screening_run, get_screening_run, list_objects, reconcile_catalog
    from backend.provenance import _object_provenance, get_live_data_status, screening_run_summary

    reconcile_catalog([_catalog_obj(_tle_record(90005, "TLE-OBJ"), "A"),
                       _catalog_obj(omm_record(_fresh_omm(270001, "OMM-OBJ")), "B")])
    rows = {o["norad_id"]: o for o in list_objects()}
    assert _object_provenance(rows["90005"])["source_format"] == "tle"
    prov = _object_provenance(rows["270001"])
    assert prov["source_format"] == "omm" and prov["norad_id"] == "270001"
    assert prov["tle_age_days"] is not None and prov["tle_age_days"] < 0.1

    ingest_id = insert_ingest_run("CelesTrak", used_cache=False, object_count=2, counts={"A": 1, "B": 1, "C": 0},
                                  source_format="omm", resolved_count=1, unresolved=[], resolution={})
    run_id = insert_screening_run(datetime.now(timezone.utc).isoformat(), "live", ingest_run_id=ingest_id)
    assert screening_run_summary(get_screening_run(run_id))["ingest_source_format"] == "omm"
    status = get_live_data_status()
    assert status["catalog_source_formats"] == {"tle": 1, "omm": 1}
    assert status["ingest"]["source_format"] == "omm"


# --- demo -----------------------------------------------------------------------------

def test_demo_override_replaces_omm_elements_with_disclosed_tle():
    from backend.db import apply_demo_overrides

    obj = _catalog_obj(omm_record(_fresh_omm(44010, "OMM-DEB")))
    obj["omm_json"] = json.dumps(obj.pop("omm"))
    l1, l2 = _fresh_tle(44010, mo_deg=45.0)
    [adj] = apply_demo_overrides([obj], [{"norad_id": "44010", "tle_line1": l1, "tle_line2": l2,
                                          "scenario": "collision_demo", "derived_from_norad_id": "1"}])
    assert adj["source_format"] == "tle" and adj["omm_json"] is None and adj["demo_adjusted"]
    ref = EarthSatellite(l1, l2, "", _TS)
    t = _TS.tt_jd(ref.epoch.tt + 0.1)
    assert np.array_equal(satellite_for(adj).at(t).position.km, ref.at(t).position.km)


def test_demo_pick_skips_six_digit_objects():
    from backend.demo_seed import DemoScenarioError, _parse_tle, _pick

    six = {**omm_record(_fresh_omm(270002, "FENGYUN SIX")), "object_type": "debris"}
    five = {**omm_record(_fresh_omm(44011, "FENGYUN FIVE")), "object_type": "debris"}
    assert _pick([six, five], "debris", "FENGYUN")["norad_id"] == "44011"
    assert _parse_tle(five).satnum == 44011  # OMM-ingested demo object parses via satrec_for
    with pytest.raises(DemoScenarioError):
        _pick([six], "debris", "FENGYUN")
