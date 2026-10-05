"""Protected-asset registry vs object catalog, and new-to-catalog object
review (backend/catalog_view.py, /api/catalog/*)."""

import json
import sqlite3

import pytest

from backend.tests._asgi import request

TLE = ("1 25544U 98067A   26275.50000000  .00016717  00000-0  10270-3 0  9005",
       "2 25544  51.6416 247.4627 0006703 130.5360 325.0288 15.72125391563537")


def _obj(norad_id, name, object_type="debris", criticality=None, owner=None):
    return {"norad_id": norad_id, "name": name, "object_type": object_type, "owner_country": owner,
            "criticality": criticality, "tle_line1": TLE[0], "tle_line2": TLE[1], "source_format": "tle"}


@pytest.fixture
def cat_db(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.db.DB_PATH", tmp_path / "catalog.db")
    monkeypatch.setattr("backend.config.API_KEY", "")
    monkeypatch.setattr("backend.config.AUTH_REQUIRED", False)
    ws = tmp_path / "working_set.json"
    ws.write_text(json.dumps({"group_a": [
        {"name_query": "ASSET-1", "criticality": "Tier1", "note": "Strategic comms"},
        {"name_query": "ASSET-2", "criticality": "Tier3", "note": "Civil EO"},
    ], "group_b_debris_groups": [], "group_c": []}), encoding="utf-8")
    monkeypatch.setattr("backend.catalog_view.WORKING_SET_PATH", ws)
    from backend.db import init_db
    init_db()
    return tmp_path


def test_first_population_is_baseline_not_new(cat_db):
    from backend.catalog_view import new_object_review
    from backend.db import reconcile_catalog

    reconcile_catalog([_obj("1", "ASSET-1", "satellite", "Tier1", "India"), _obj("2", "DEB-2")])
    review = new_object_review()
    assert review["baseline"] is not None
    assert review["objects"] == [] and review["counts"]["total"] == 0


def test_object_entering_later_is_new_and_not_retired_when_missing(cat_db):
    from backend.catalog_view import new_object_review
    from backend.db import reconcile_catalog

    base = [_obj("1", "ASSET-1", "satellite", "Tier1", "India"), _obj("2", "DEB-2")]
    reconcile_catalog(base)
    reconcile_catalog(base + [_obj("3", "DEB-3")])
    new = new_object_review()["objects"]
    assert [o["norad_id"] for o in new] == ["3"]
    assert new[0]["review_status"] == "not_reviewed" and new[0]["protected"] is False
    assert new[0]["orbital_data"] == "available" and new[0]["source_format"] == "tle"
    # Dropping out of the next ingest makes it inactive -- still listed, never "retired".
    reconcile_catalog(base)
    again = new_object_review()["objects"]
    assert [o["norad_id"] for o in again] == ["3"] and again[0]["active"] is False


def test_first_seen_is_preserved_across_upserts(cat_db):
    from backend.db import list_objects, reconcile_catalog

    base = [_obj("1", "ASSET-1", "satellite", "Tier1", "India")]
    reconcile_catalog(base + [_obj("3", "DEB-3")])
    first = {o["norad_id"]: o["first_seen"] for o in list_objects()}
    reconcile_catalog(base + [_obj("3", "DEB-3 RENAMED")])
    after = {o["norad_id"]: o["first_seen"] for o in list_objects()}
    assert first == after


def test_existing_database_migrates_with_all_objects_as_baseline(tmp_path, monkeypatch):
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE objects (norad_id TEXT PRIMARY KEY, name TEXT NOT NULL, object_type TEXT NOT NULL, "
                 "owner_country TEXT, criticality TEXT, tle_line1 TEXT NOT NULL, tle_line2 TEXT NOT NULL, "
                 "last_updated TEXT NOT NULL)")
    conn.execute("INSERT INTO objects VALUES ('9','OLD','debris',NULL,NULL,?,?,'2026-01-01T00:00:00+00:00')", TLE)
    conn.commit()
    conn.close()
    monkeypatch.setattr("backend.db.DB_PATH", db)
    from backend.db import init_db, list_new_objects, list_objects
    init_db()
    init_db()  # idempotent
    assert list_objects()[0]["first_seen"] is not None
    assert list_new_objects() == []


def test_review_records_audit_and_changes_nothing_else(cat_db):
    from backend.db import list_objects, reconcile_catalog
    from backend.main import app

    base = [_obj("1", "ASSET-1", "satellite", "Tier1", "India")]
    reconcile_catalog(base)
    reconcile_catalog(base + [_obj("3", "DEB-3")])
    before = list_objects()
    status, _, body = request(app, "POST", "/api/catalog/new-objects/3/review", json_body={"note": "  checked\nTLE  "})
    assert status == 200 and body["norad_id"] == "3" and body["note"] == "checked TLE"
    status, _, review = request(app, "GET", "/api/catalog/new-objects")
    assert status == 200 and review["objects"][0]["review_status"] == "reviewed"
    assert review["counts"]["not_reviewed"] == 0
    assert list_objects() == before  # no classification / protected-status change
    # Baseline objects are not reviewable as "new".
    status, _, _ = request(app, "POST", "/api/catalog/new-objects/1/review")
    assert status == 404


def test_review_requires_api_key_when_auth_enabled(cat_db, monkeypatch):
    from backend.db import reconcile_catalog
    from backend.main import app

    reconcile_catalog([_obj("1", "ASSET-1", "satellite", "Tier1", "India")])
    reconcile_catalog([_obj("1", "ASSET-1", "satellite", "Tier1", "India"), _obj("3", "DEB-3")])
    monkeypatch.setattr("backend.config.API_KEY", "k")
    status, _, _ = request(app, "POST", "/api/catalog/new-objects/3/review")
    assert status == 401
    status, _, _ = request(app, "GET", "/api/catalog/new-objects")
    assert status == 200


def test_registry_lists_configured_assets_separately_from_catalog(cat_db, monkeypatch):
    from backend.catalog_view import protected_asset_registry
    from backend.db import reconcile_catalog

    reconcile_catalog([_obj("1", "ASSET-1", "satellite", "Tier1", "India"), _obj("2", "DEB-2"),
                       _obj("5", "OTHER-SAT", "foreign_sat", None, "foreign")])
    monkeypatch.setattr("backend.catalog_view._resolution_and_unresolved",
                        lambda: ({"A:ASSET-1": "1"}, {"A:ASSET-2": {"group": "A", "name_query": "ASSET-2",
                                                                    "reason": "ambiguous: 3 matches"}}))
    reg = protected_asset_registry()
    assert reg["registry_source"].startswith("Operator configuration")
    by_name = {a["configured_name"]: a for a in reg["assets"]}
    assert set(by_name) == {"ASSET-1", "ASSET-2"}  # only configured Group A entries
    a1 = by_name["ASSET-1"]
    assert a1["norad_id"] == "1" and a1["criticality"] == "Tier1" and a1["protected"] is True
    assert a1["protected_basis"] == "operator_configuration" and a1["data_status"] in ("current", "stale")
    a2 = by_name["ASSET-2"]
    assert a2["data_status"] == "unresolved" and a2["unresolved_reason"].startswith("ambiguous")
    # The public catalog summary counts every object, protected or not.
    assert reg["catalog"]["active_objects"] == 3
    assert reg["catalog"]["by_type"] == {"satellite": 1, "debris": 1, "foreign_sat": 1}
