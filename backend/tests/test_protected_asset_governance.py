"""Protected-asset governance: protected status and Group A screening scope
come from the operator-managed protected-asset registry, as resolved by the
latest SUCCESSFUL ingest -- never from object_type, ownership or nationality.

The real ingest path runs (registry -> active entries -> fetch_tles name
resolution -> run_ingest -> ingest_runs.resolution); only the CelesTrak
network fetch and the static working-set file are replaced by local data,
so these tests need no network access.
"""

import math
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import numpy as np
import pytest
from sgp4.api import WGS72, Satrec, jday
from sgp4.exporter import export_tle

ACTOR = {"user_id": None, "username": "test-asset-manager", "role": "ASSET_MANAGER"}

P0, P1 = "91100", "91101"          # resolved to registry entries P-ASSET-0 / P-ASSET-1
FOREIGN = "93100"                  # Group C (foreign_sat)
DEBRIS = ["92100", "92101", "92102", "92103"]
ORDINARY_SAT = "94100"             # object_type "satellite", never in the registry

NAMES = {P0: "P-ASSET-0", P1: "P-ASSET-1", FOREIGN: "F-SAT-0", **{d: f"DEB-{i}" for i, d in enumerate(DEBRIS)},
         ORDINARY_SAT: "ORD-SAT"}


def _tle(satnum):
    now = datetime.now(timezone.utc)
    jd, fr = jday(now.year, now.month, now.day, now.hour, now.minute, now.second)
    sat = Satrec()
    sat.sgp4init(WGS72, "i", int(satnum), (jd + fr) - 2433281.5, 0.0001, 0.0, 0.0, 0.001, math.radians(20),
                 math.radians(51.6), math.radians(int(satnum) % 360), 15.5 * 2 * math.pi / 1440.0, math.radians(10))
    return export_tle(sat)


def _tle_text(norad_ids):
    out = []
    for nid in norad_ids:
        l1, l2 = _tle(nid)
        out += [NAMES[nid], l1, l2]
    return "\n".join(out) + ("\n" if out else "")


@pytest.fixture
def gov(tmp_path, monkeypatch):
    """Throwaway DB with two ACTIVE registry entries and a local stand-in for
    CelesTrak. `gov["missing"]` holds NAME queries that resolve to nothing."""
    monkeypatch.setattr("backend.db.DB_PATH", tmp_path / "governance_test.db")
    from backend import ingest
    from backend.db import create_protected_asset, init_db

    init_db()
    ids = {}
    for name in ("P-ASSET-0", "P-ASSET-1"):
        ids[name] = create_protected_asset(name, None, "Tier1", None, ACTOR)["id"]

    state = {"missing": set(), "fail": False, "asset_ids": ids, "extra_group": []}
    by_name = {v: k for k, v in NAMES.items()}

    def fake_fetch(url, cache_key, suffix=".tle", fmt=None):
        if state["fail"]:
            raise RuntimeError("network unavailable (test)")
        q = parse_qs(urlparse(url).query)
        if "NAME" in q:
            name = q["NAME"][0]
            return ("" if name in state["missing"] else _tle_text([by_name[name]])), False
        return _tle_text(DEBRIS + state["extra_group"]), False

    monkeypatch.setattr(ingest, "CELESTRAK_FORMAT", "tle")
    monkeypatch.setattr(ingest, "_fetch_with_cache", fake_fetch)
    monkeypatch.setattr(ingest, "_load_working_set", lambda: {
        "group_a": [], "group_c": [{"name_query": "F-SAT-0"}],
        "group_b_debris_groups": [{"celestrak_group": "test-debris", "max_count": 10}]})
    return state


def _refresh():
    from backend.ingest import run_ingest
    return run_ingest()


def _set_status(state, name, status):
    from backend.db import set_protected_asset_status
    set_protected_asset_status(state["asset_ids"][name], status, f"test: {status}", ACTOR)


def _add_ordinary_satellite():
    """A satellite-typed object that no registry entry represents."""
    from backend.db import upsert_object
    l1, l2 = _tle(ORDINARY_SAT)
    upsert_object(ORDINARY_SAT, NAMES[ORDINARY_SAT], "satellite", "India", "Tier1", l1, l2)


# --- Synthetic screening geometry ---------------------------------------------
# Each pair below passes within ~0.5 km at the window midpoint; pairs are kept
# 1000 km apart from each other, so only these four close approaches exist.
PAIRS = [(P0, DEBRIS[0]), (P1, FOREIGN), (ORDINARY_SAT, DEBRIS[1]), (DEBRIS[2], DEBRIS[3])]


def _grid(n=20):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    times = [start + timedelta(seconds=60 * i) for i in range(n)]
    grid = {}
    for k, (a, b) in enumerate(PAIRS):
        base = np.array([7000.0 + 1000.0 * k, 0.0, 0.0])
        pa = np.tile(base, (n, 1))
        grid[a] = pa
        grid[b] = pa + np.array([[abs(i - n // 2) * 50.0 + 0.5, 0.0, 0.0] for i in range(n)])
    return times, grid


def _screened_pairs():
    """Pairs screened as Group A x others. Every catalog object (including
    inactive ones, i.e. whatever object_type they carry) is offered to the
    screener, so membership can only come from the registry resolution."""
    from backend.conjunction import find_close_approaches
    from backend.db import list_objects
    times, grid = _grid()
    events = find_close_approaches(times, grid, threshold_km=10.0, step_seconds=60,
                                   objects=list_objects(include_inactive=True))
    return {frozenset((e["object_a_id"], e["object_b_id"])) for e in events}, {e["object_a_id"] for e in events}


def _protected():
    from backend.db import resolved_protected_norad_ids
    return resolved_protected_norad_ids()


# --- Conjunction Group A -------------------------------------------------------

def test_active_registry_assets_are_group_a_and_screen_against_debris_and_foreign(gov):
    _refresh()
    _add_ordinary_satellite()
    assert _protected() == {P0, P1}
    pairs, group_a = _screened_pairs()
    assert pairs == {frozenset((P0, DEBRIS[0])), frozenset((P1, FOREIGN))}
    assert group_a == {P0, P1}


def test_object_type_is_not_protected_status(gov):
    """A satellite-typed object outside the registry is not Group A, and two
    non-Group-A objects passing very close never form a Group A pair."""
    _refresh()
    _add_ordinary_satellite()
    from backend.db import list_objects
    types = {o["norad_id"]: o["object_type"] for o in list_objects(include_inactive=True)}
    assert types[ORDINARY_SAT] == "satellite"
    pairs, group_a = _screened_pairs()
    assert ORDINARY_SAT not in group_a and ORDINARY_SAT not in _protected()
    assert frozenset((ORDINARY_SAT, DEBRIS[1])) not in pairs
    assert frozenset((DEBRIS[2], DEBRIS[3])) not in pairs
    assert FOREIGN not in _protected() and not set(DEBRIS) & _protected()


def test_group_a_follows_the_entry_not_the_object_type(gov):
    """Group A membership is read from the registry resolution: the resolved
    object of an active entry is screened even if its stored object_type is
    not 'satellite'."""
    _refresh()
    from backend.db import get_connection
    conn = get_connection()
    conn.execute("UPDATE objects SET object_type = 'debris' WHERE norad_id = ?", (P0,))
    conn.commit()
    conn.close()
    pairs, _ = _screened_pairs()
    assert frozenset((P0, DEBRIS[0])) in pairs


@pytest.mark.parametrize("status", ["suspended", "retired"])
def test_suspended_or_retired_asset_leaves_group_a_at_next_successful_refresh(gov, status):
    _refresh()
    _set_status(gov, "P-ASSET-1", status)
    # Registry change alone: the established catalog keeps its scope.
    assert P1 in _protected()
    assert frozenset((P1, FOREIGN)) in _screened_pairs()[0]
    _refresh()
    # The next successful refresh resolves only active entries.
    assert _protected() == {P0}
    pairs, group_a = _screened_pairs()
    assert P1 not in group_a and frozenset((P1, FOREIGN)) not in pairs
    assert frozenset((P0, DEBRIS[0])) in pairs


def test_reactivated_asset_returns_to_group_a_after_next_refresh(gov):
    _refresh()
    _set_status(gov, "P-ASSET-1", "suspended")
    _refresh()
    assert P1 not in _protected()
    _set_status(gov, "P-ASSET-1", "active")
    assert P1 not in _protected()          # not until the next successful refresh
    _refresh()
    assert P1 in _protected()
    assert frozenset((P1, FOREIGN)) in _screened_pairs()[0]


def test_failed_refresh_does_not_change_group_a(gov):
    _refresh()
    _set_status(gov, "P-ASSET-1", "suspended")
    gov["fail"] = True
    with pytest.raises(Exception):
        _refresh()
    assert _protected() == {P0, P1}        # last SUCCESSFUL refresh still governs


def test_unresolved_active_entry_keeps_its_previous_object(gov):
    _refresh()
    gov["missing"].add("P-ASSET-0")        # name query now resolves to nothing
    _refresh()
    from backend.db import latest_ingest_run
    import json
    unresolved = json.loads(latest_ingest_run()["unresolved_json"])
    assert any(u["name_query"] == "P-ASSET-0" and u["kept_norad_id"] == P0 for u in unresolved)
    assert P0 in _protected()
    assert frozenset((P0, DEBRIS[0])) in _screened_pairs()[0]


def test_registry_changes_do_not_rewrite_existing_events(gov):
    _refresh()
    from backend.db import insert_event, list_events
    insert_event(P1, FOREIGN, "collision_risk", "2026-01-01T00:10:00+00:00", 0.5, 7.5, 1e-4, "High", 3.0)
    before = list_events()
    _set_status(gov, "P-ASSET-1", "suspended")
    _refresh()
    assert list_events() == before


# --- Catalog view: new-object review ------------------------------------------

@pytest.fixture
def cat_gov(gov):
    """Establish the catalog baseline before the governed objects arrive, so
    they appear in the new-to-local-catalog review."""
    from backend.db import upsert_object
    l1, l2 = _tle("95000")
    NAMES["95000"] = "BASELINE-OBJ"
    upsert_object("95000", "BASELINE-OBJ", "debris", None, None, l1, l2)
    gov["extra_group"].append("95000")      # still published, so ingest keeps it active
    _refresh()
    _add_ordinary_satellite()
    return gov


def _review_flags():
    from backend.catalog_view import new_object_review
    return {o["norad_id"]: (o["protected"], o["object_type"]) for o in new_object_review()["objects"]}


def test_new_object_review_protected_comes_from_registry(cat_gov):
    flags = _review_flags()
    assert flags[P0] == (True, "satellite") and flags[P1] == (True, "satellite")
    assert flags[ORDINARY_SAT] == (False, "satellite")
    assert flags[FOREIGN][0] is False
    assert all(flags[d][0] is False for d in DEBRIS)


@pytest.mark.parametrize("status", ["suspended", "retired"])
def test_new_object_review_suspended_or_retired_not_protected_after_refresh(cat_gov, status):
    _set_status(cat_gov, "P-ASSET-1", status)
    assert _review_flags()[P1][0] is True   # before the next successful refresh
    _refresh()
    assert _review_flags()[P1][0] is False
    assert _review_flags()[P0][0] is True


def test_review_acknowledgement_changes_neither_protection_nor_classification(cat_gov):
    from backend.db import insert_object_review, list_protected_assets
    registry_before = list_protected_assets()
    before = _review_flags()
    insert_object_review(ORDINARY_SAT, note="checked", actor=ACTOR)
    insert_object_review(P0, note="checked", actor=ACTOR)
    after = _review_flags()
    assert after == before
    assert list_protected_assets() == registry_before


# --- Proximity watch uses the same registry-derived asset set ------------------

def test_proximity_watch_assets_come_from_registry_not_object_type(gov):
    """A satellite-typed object outside the registry is not treated as a
    protected asset by the proximity watch either."""
    from backend.db import list_objects
    from backend.threat import detect_proximity_operations
    _refresh()
    _add_ordinary_satellite()
    n = 40
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    times = [start + timedelta(seconds=60 * i) for i in range(n)]
    base = np.tile(np.array([7000.0, 0.0, 0.0]), (n, 1))
    # The foreign satellite dwells 5 km from the ordinary satellite for the whole window.
    grid = {ORDINARY_SAT: base, FOREIGN: base + np.array([5.0, 0.0, 0.0])}
    assert detect_proximity_operations(times, grid, objects=list_objects(include_inactive=True)) == []
    grid = {P1: base, FOREIGN: base + np.array([5.0, 0.0, 0.0])}
    events = detect_proximity_operations(times, grid, objects=list_objects(include_inactive=True))
    assert [(e["object_a_id"], e["object_b_id"]) for e in events] == [(P1, FOREIGN)]


def test_api_objects_protected_flag_is_registry_derived(gov):
    from backend.main import app
    from backend.tests._asgi import request
    _refresh()
    _add_ordinary_satellite()
    status, _, body = request(app, "GET", "/api/objects")
    assert status == 200
    flags = {o["norad_id"]: o["protected"] for o in body}
    assert flags[P0] is True and flags[P1] is True
    assert flags[ORDINARY_SAT] is False and flags[FOREIGN] is False
    assert not any(flags[d] for d in DEBRIS if d in flags)
