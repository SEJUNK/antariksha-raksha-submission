"""Operator-managed protected-asset registry (Group A): one-time seeding from
working_set.json, validation, status transitions, governance audit, ingest
reading active DB rows (resolution otherwise unchanged), registry view, and
migration of a legacy DB. Runs against throwaway DBs."""

import json
import sqlite3
from pathlib import Path

import pytest

from backend.tests._auth_helpers import call, login, make_user

pytestmark = pytest.mark.real_auth

REPO = Path(__file__).resolve().parents[2]
TLE = ("1 25544U 98067A   26275.50000000  .00016717  00000-0  10270-3 0  9005",
       "2 25544  51.6416 247.4627 0006703 130.5360 325.0288 15.72125391563537")
WS_GROUP_A = [
    {"name_query": "ASSET-1", "criticality": "Tier1", "note": "Strategic comms"},
    {"name_query": "ASSET-2", "exact_match": "ASSET-2", "criticality": "Tier2", "note": "EO"},
    {"name_query": "ASSET-3", "criticality": "Tier3", "note": "Civil"},
]


def _patch_ws(monkeypatch, path):
    for target in ("backend.config.WORKING_SET_PATH", "backend.ingest.WORKING_SET_PATH",
                   "backend.catalog_view.WORKING_SET_PATH"):
        monkeypatch.setattr(target, path)


@pytest.fixture
def ws(tmp_path, monkeypatch):
    path = tmp_path / "ws.json"
    path.write_text(json.dumps({"group_a": WS_GROUP_A, "group_b_debris_groups": [],
                                "group_c": [{"name_query": "FOREIGN-1"}]}), encoding="utf-8")
    _patch_ws(monkeypatch, path)
    monkeypatch.setattr("backend.config.API_KEY", "")
    monkeypatch.setattr("backend.config.AUTH_REQUIRED", False)
    from backend.db import ensure_protected_assets_seeded, init_db
    init_db()
    ensure_protected_assets_seeded(path)
    return path


@pytest.fixture
def tokens(ws):
    out = {}
    for role in ("VIEWER", "OPERATOR", "ASSET_MANAGER", "ADMINISTRATOR"):
        name = role.lower().replace("_", "-")
        make_user(name, role)
        out[role] = login(name)[3]
    return out


def _assets():
    from backend.db import list_protected_assets
    return {a["name_query"]: a for a in list_protected_assets()}


def _asset_audit():
    from backend.db import list_governance_audit
    return list_governance_audit(limit=1000, target_type="protected_asset")


# --- seeding ------------------------------------------------------------------------

def test_seeded_once_from_working_set_with_system_audit(ws):
    from backend.db import active_group_a_entries, ensure_protected_assets_seeded

    assert active_group_a_entries() == WS_GROUP_A  # same shape and order as working_set entries
    assert all(a["created_by"] == "system" and a["status"] == "active" for a in _assets().values())
    [seed] = [r for r in _asset_audit() if r["action"] == "asset_seed"]
    assert seed["actor_username"] == "system" and seed["details"]["seeded"] == ["ASSET-1", "ASSET-2", "ASSET-3"]
    # Second call (e.g. next startup) is a no-op, also after the working set changes.
    ws.write_text(json.dumps({"group_a": [{"name_query": "OTHER", "criticality": "Tier1"}],
                              "group_b_debris_groups": [], "group_c": []}), encoding="utf-8")
    assert ensure_protected_assets_seeded(ws) == 0
    assert list(_assets()) == ["ASSET-1", "ASSET-2", "ASSET-3"]
    assert len([r for r in _asset_audit() if r["action"] == "asset_seed"]) == 1


def test_not_reseeded_after_assets_are_retired(ws):
    from backend.db import ensure_protected_assets_seeded, set_protected_asset_status

    ids = {n: a["id"] for n, a in _assets().items()}
    set_protected_asset_status(ids["ASSET-1"], "retired", None, None)
    set_protected_asset_status(ids["ASSET-2"], "retired", None, None)
    assert ensure_protected_assets_seeded(ws) == 0
    assert [a["status"] for a in _assets().values()] == ["retired", "retired", "active"]


def test_real_working_set_seed_matches_group_a_exactly(tmp_path, monkeypatch):
    """With the shipped data/working_set.json the DB-driven Group A list is
    identical to the file's group_a -> named-entry resolution is unchanged."""
    from backend.db import active_group_a_entries, ensure_protected_assets_seeded, init_db

    real = REPO / "data" / "working_set.json"
    init_db()
    assert ensure_protected_assets_seeded(real) == len(json.loads(real.read_text(encoding="utf-8"))["group_a"])
    assert active_group_a_entries() == json.loads(real.read_text(encoding="utf-8"))["group_a"]


def test_legacy_database_migrates_and_seeds_without_losing_data(tmp_path, monkeypatch, ws):
    db_file = tmp_path / "legacy.db"
    monkeypatch.setattr("backend.db.DB_PATH", db_file)
    conn = sqlite3.connect(db_file)
    conn.executescript("""
        CREATE TABLE objects (norad_id TEXT PRIMARY KEY, name TEXT NOT NULL, object_type TEXT NOT NULL,
          owner_country TEXT, criticality TEXT, tle_line1 TEXT NOT NULL, tle_line2 TEXT NOT NULL,
          last_updated TEXT NOT NULL);
        INSERT INTO objects VALUES ('9', 'OLD', 'satellite', 'India', 'Tier1', 'l1', 'l2', '2026-01-01');
        CREATE TABLE decision_log (id INTEGER PRIMARY KEY AUTOINCREMENT, event_class TEXT NOT NULL,
          object_a_name TEXT, object_b_name TEXT, risk_tier TEXT, pc_score REAL, miss_distance_km REAL,
          generated_by TEXT, decision TEXT NOT NULL, rejection_reason TEXT, decided_at TEXT NOT NULL);
        INSERT INTO decision_log (event_class, decision, decided_at) VALUES ('collision_risk', 'approved', '2026-01-01');
    """)
    conn.commit()
    conn.close()
    from backend import main
    from backend.db import list_all_decisions, list_objects

    monkeypatch.setattr("backend.config.BOOTSTRAP_ADMIN_USERNAME", "")
    main._startup()
    main._startup()  # idempotent
    assert [o["norad_id"] for o in list_objects()] == ["9"]
    [d] = list_all_decisions()
    assert d["decided_at"] == "2026-01-01" and d["actor_username"] is None
    assert list(_assets()) == ["ASSET-1", "ASSET-2", "ASSET-3"]
    assert len([r for r in _asset_audit() if r["action"] == "asset_seed"]) == 1


# --- endpoints ----------------------------------------------------------------------

def test_create_asset_contract_and_audit(tokens):
    status, _, body = call("POST", "/api/protected-assets", tokens["ASSET_MANAGER"], json_body={
        "name_query": "  NEW-SAT  ", "exact_match": "NEW-SAT", "criticality": "Tier2", "note": "added"})
    assert status == 201 and body["effective"] == "next_successful_refresh"
    asset = body["asset"]
    assert asset["name_query"] == "NEW-SAT" and asset["status"] == "active" and asset["created_by"] == "asset-manager"
    [entry] = [r for r in _asset_audit() if r["action"] == "asset_create"]
    assert entry["actor_username"] == "asset-manager" and entry["actor_role"] == "ASSET_MANAGER"
    assert entry["target_id"] == str(asset["id"])
    assert entry["details"]["before"] is None and entry["details"]["after"]["criticality"] == "Tier2"
    status, _, listing = call("GET", "/api/protected-assets", tokens["VIEWER"])
    assert status == 200 and "NEW-SAT" in [a["name_query"] for a in listing["assets"]]


@pytest.mark.parametrize("body", [
    {"criticality": "Tier1"},
    {"name_query": "X", "criticality": "Tier1"},
    {"name_query": "N" * 65, "criticality": "Tier1"},
    {"name_query": "BAD\u0007NAME", "criticality": "Tier1"},
    {"name_query": "LINE\nBREAK", "criticality": "Tier1"},
    {"name_query": "OK-NAME"},
    {"name_query": "OK-NAME", "criticality": "Tier4"},
    {"name_query": "OK-NAME", "criticality": "Tier1", "exact_match": "E" * 65},
    {"name_query": "OK-NAME", "criticality": "Tier1", "note": "n" * 201},
])
def test_create_asset_validation(tokens, body):
    status, _, resp = call("POST", "/api/protected-assets", tokens["ASSET_MANAGER"], json_body=body)
    assert status == 422 and resp["error"] == "invalid_input" and isinstance(resp["detail"], str)
    assert len(_assets()) == 3


def test_duplicate_name_is_case_insensitive_including_retired(tokens):
    status, _, body = call("POST", "/api/protected-assets", tokens["ASSET_MANAGER"],
                           json_body={"name_query": "asset-1", "criticality": "Tier1"})
    assert status == 409 and body["error"] == "duplicate"
    aid = _assets()["ASSET-3"]["id"]
    assert call("POST", f"/api/protected-assets/{aid}/status", tokens["ASSET_MANAGER"],
                json_body={"status": "retired"})[0] == 200
    assert call("POST", "/api/protected-assets", tokens["ASSET_MANAGER"],
                json_body={"name_query": "ASSET-3", "criticality": "Tier1"})[0] == 409


def test_patch_asset_rules(tokens):
    tok = tokens["ASSET_MANAGER"]
    aid = _assets()["ASSET-1"]["id"]
    status, _, body = call("PATCH", f"/api/protected-assets/{aid}", tok,
                           json_body={"criticality": "Tier3", "note": "downgraded", "exact_match": "ASSET-1"})
    assert status == 200 and body["effective"] == "next_successful_refresh"
    assert body["asset"]["criticality"] == "Tier3" and body["asset"]["updated_by"] == "asset-manager"
    [entry] = [r for r in _asset_audit() if r["action"] == "asset_update"]
    assert entry["details"]["before"]["criticality"] == "Tier1" and entry["details"]["after"]["criticality"] == "Tier3"
    assert entry["details"]["changed"] == ["criticality", "exact_match", "note"]
    status, _, body = call("PATCH", f"/api/protected-assets/{aid}", tok, json_body={"exact_match": None})
    assert status == 200 and body["asset"]["exact_match"] is None
    status, _, body = call("PATCH", f"/api/protected-assets/{aid}", tok, json_body={"name_query": "RENAMED"})
    assert status == 422 and "immutable" in body["detail"]
    assert call("PATCH", f"/api/protected-assets/{aid}", tok, json_body={})[0] == 422
    assert call("PATCH", f"/api/protected-assets/{aid}", tok, json_body={"criticality": "Tier9"})[0] == 422
    assert call("PATCH", "/api/protected-assets/9999", tok, json_body={"note": "x"})[0] == 404


def test_status_transitions_terminal_retire_and_zero_active_refusal(tokens):
    tok = tokens["ASSET_MANAGER"]
    ids = {n: a["id"] for n, a in _assets().items()}

    def set_status(name, status, reason=None):
        return call("POST", f"/api/protected-assets/{ids[name]}/status", tok,
                    json_body={"status": status, "reason": reason})

    s, _, body = set_status("ASSET-1", "suspended", "maintenance")
    assert s == 200 and body["asset"]["status"] == "suspended" and body["effective"] == "next_successful_refresh"
    assert set_status("ASSET-1", "active")[0] == 200
    assert set_status("ASSET-1", "active")[2]["error"] == "invalid_transition"  # active -> active
    assert set_status("ASSET-1", "retired")[0] == 200
    s, _, body = set_status("ASSET-1", "active")
    assert s == 409 and body["error"] == "invalid_transition" and "terminal" in body["detail"]
    assert call("PATCH", f"/api/protected-assets/{ids['ASSET-1']}", tok, json_body={"note": "x"})[0] == 409
    assert set_status("ASSET-2", "suspended")[0] == 200
    assert set_status("ASSET-2", "retired")[0] == 200  # suspended -> retired
    s, _, body = set_status("ASSET-3", "suspended")
    assert s == 409 and body["error"] == "last_active_asset"
    assert set_status("ASSET-3", "retired")[2]["error"] == "last_active_asset"
    assert _assets()["ASSET-3"]["status"] == "active"
    assert set_status("ASSET-3", "deleted")[0] == 422
    entries = [r for r in _asset_audit() if r["action"] == "asset_status"]
    assert entries[-1]["details"] == {"before": {"name_query": "ASSET-1", "exact_match": None, "criticality": "Tier1",
                                                 "note": "Strategic comms", "status": "active"},
                                      "after": {"name_query": "ASSET-1", "exact_match": None, "criticality": "Tier1",
                                                "note": "Strategic comms", "status": "suspended"},
                                      "reason": "maintenance"}


def test_asset_audit_endpoint_visible_to_viewer(tokens):
    aid = _assets()["ASSET-2"]["id"]
    call("POST", f"/api/protected-assets/{aid}/status", tokens["ADMINISTRATOR"], json_body={"status": "suspended"})
    status, _, body = call("GET", "/api/protected-assets/audit", tokens["VIEWER"])
    assert status == 200
    assert [e["action"] for e in body["entries"]] == ["asset_status", "asset_seed"]
    assert body["entries"][0]["actor_role"] == "ADMINISTRATOR"
    assert call("POST", f"/api/protected-assets/{aid}/status", tokens["OPERATOR"],
                json_body={"status": "active"})[0] == 403


def test_asset_changes_never_touch_events_or_observations(tokens):
    from backend.db import get_connection, insert_brief, insert_event, list_events, upsert_object

    upsert_object("1", "ASSET-1", "satellite", "India", "Tier1", TLE[0], TLE[1])
    upsert_object("2", "DEB", "debris", None, None, TLE[0], TLE[1])
    eid = insert_event("1", "2", "collision_risk", "2026-07-21T04:12:00+00:00", 0.05, 7.1, 2e-4, "Critical", 600.0)
    insert_brief(eid, "b", "m", 0.05, "fallback_template")
    conn = get_connection()
    objects_before = [dict(r) for r in conn.execute("SELECT * FROM objects ORDER BY norad_id")]
    conn.close()
    events_before = list_events()
    aid = _assets()["ASSET-1"]["id"]
    tok = tokens["ASSET_MANAGER"]
    call("PATCH", f"/api/protected-assets/{aid}", tok, json_body={"criticality": "Tier3"})
    call("POST", f"/api/protected-assets/{aid}/status", tok, json_body={"status": "suspended"})
    assert list_events() == events_before
    conn = get_connection()
    assert [dict(r) for r in conn.execute("SELECT * FROM objects ORDER BY norad_id")] == objects_before
    conn.close()


# --- registry view ---------------------------------------------------------------------

def test_registry_includes_all_statuses_and_protects_only_active(tokens):
    from backend.db import set_protected_asset_status

    ids = {n: a["id"] for n, a in _assets().items()}
    set_protected_asset_status(ids["ASSET-2"], "suspended", None, {"user_id": 1, "username": "am", "role": "X"})
    set_protected_asset_status(ids["ASSET-3"], "retired", None, None)
    status, _, reg = call("GET", "/api/catalog/registry", tokens["VIEWER"])
    assert status == 200
    by = {a["name_query"]: a for a in reg["assets"]}
    assert list(by) == ["ASSET-1", "ASSET-2", "ASSET-3"]
    a1, a2, a3 = by["ASSET-1"], by["ASSET-2"], by["ASSET-3"]
    assert a1["protected"] is True and a1["protected_basis"] == "operator_configuration" and a1["status"] == "active"
    assert a2["protected"] is False and a2["status"] == "suspended" and a2["updated_by"] == "am"
    assert a3["protected"] is False and a3["status"] == "retired"
    for a in (a1, a2, a3):
        assert {"asset_id", "status", "name_query", "exact_match", "updated_at", "updated_by", "configured_name",
                "data_status"} <= set(a)
    assert a2["exact_match"] == "ASSET-2" and a1["asset_id"] == ids["ASSET-1"]
    assert reg["registry_source"].startswith("Operator configuration")


# --- ingest --------------------------------------------------------------------------------

def test_ingest_reads_active_db_assets_and_excludes_suspended(ws, monkeypatch):
    from backend import ingest
    from backend.db import create_protected_asset, set_protected_asset_status

    ids = {n: a["id"] for n, a in _assets().items()}
    set_protected_asset_status(ids["ASSET-2"], "suspended", None, None)
    create_protected_asset("ASSET-4", None, "Tier1", None, None)
    seen = []

    def fake_resolve(entry, cache_prefix, object_type, extra_fields, fmt="tle"):
        seen.append((cache_prefix, dict(entry), dict(extra_fields)))
        return None, "test: not resolved", False, []

    monkeypatch.setattr(ingest, "_resolve_named_entry", fake_resolve)
    ingest.fetch_tles()
    group_a = [(e, x) for p, e, x in seen if p == "groupA"]
    assert [e for e, _ in group_a] == [WS_GROUP_A[0], WS_GROUP_A[2], {"name_query": "ASSET-4", "criticality": "Tier1"}]
    assert [x["criticality"] for _, x in group_a] == ["Tier1", "Tier3", "Tier1"]
    assert all(x["group"] == "A" and x["owner_country"] == "India" for _, x in group_a)
    assert [e["name_query"] for p, e, _ in seen if p == "groupC"] == ["FOREIGN-1"]  # B/C still from working set
    assert ingest.fetch_tles.group_a_total == 3
    assert [u["name_query"] for u in ingest.fetch_tles.unresolved if u["group"] == "A"] == ["ASSET-1", "ASSET-3",
                                                                                              "ASSET-4"]


def test_ingest_resolution_unchanged_with_db_registry(ws, monkeypatch, tmp_path):
    """End to end with a fake CelesTrak: entries resolve exactly as before
    (exact_match honoured, ambiguous stays unresolved)."""
    from backend import ingest

    def tle_block(name, norad):
        l1 = TLE[0].replace("25544", f"{norad:05d}")
        l2 = TLE[1].replace("25544", f"{norad:05d}")
        return f"{name}\n{l1}\n{l2}\n"

    responses = {
        "ASSET-1": tle_block("ASSET-1", 40001),
        "ASSET-2": tle_block("ASSET-2", 40002) + tle_block("ASSET-2B", 40012),
        "ASSET-3": tle_block("ASSET-3", 40003) + tle_block("ASSET-3 DEB", 40013),
        "FOREIGN-1": tle_block("FOREIGN-1", 40004),
    }

    class _Resp:
        def __init__(self, text):
            self.text = text

        def raise_for_status(self):
            pass

    def fake_get(url, timeout=None):
        name = url.split("NAME=")[1].split("&")[0]
        return _Resp(responses[name])

    monkeypatch.setattr(ingest, "TLE_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(ingest, "CELESTRAK_FORMAT", "tle")
    monkeypatch.setattr(ingest.requests, "get", fake_get)
    objs = {o["name"]: o for o in ingest.fetch_tles()}
    assert set(objs) == {"ASSET-1", "ASSET-2", "FOREIGN-1"}
    assert objs["ASSET-2"]["criticality"] == "Tier2" and objs["ASSET-1"]["group"] == "A"
    assert ingest.fetch_tles.resolution == {"A:ASSET-1": "40001", "A:ASSET-2": "40002", "C:FOREIGN-1": "40004"}
    [u] = ingest.fetch_tles.unresolved
    assert u["name_query"] == "ASSET-3" and u["reason"].startswith("ambiguous")
