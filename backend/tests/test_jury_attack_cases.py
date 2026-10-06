"""Jury attack cases A-G: the questions a reviewer is most likely to probe,
each answered by exercising the real code path (registry -> ingest
resolution -> screening, refresh, propagation, brief generation, decision
endpoints, demo seeding). Only the CelesTrak network fetch, the static
working-set file and the local LLM are replaced, so no network is needed.

  A  Protected status comes only from the registry (never object type).
  B  A failed refresh changes nothing.
  C  Stale or invalid orbital data is excluded, not silently used.
  D  AI disabled / unavailable / sensational -> deterministic template.
  E  AI output can never change TCA, miss distance, Pc or risk tier.
  F  Human control: decisions need an authorised principal, record actor
     and role, and no endpoint commands a spacecraft.
  G  Demo integrity: registry-governed asset, demo-flagged events, real
     catalog and governance untouched.
"""

import pytest
import requests

from backend import brief_agent, config
from backend.tests._auth_helpers import call, login, make_user
from backend.tests.test_brief_agent import COLLISION_EVENT, DV, FakeOllama
from backend.tests.test_demo_seed import _make_tle
from backend.tests.test_protected_asset_governance import (  # noqa: F401  (fixture)
    DEBRIS,
    FOREIGN,
    NAMES,
    ORDINARY_SAT,
    P0,
    P1,
    _add_ordinary_satellite,
    _protected,
    _refresh,
    _set_status,
    gov,
)
from backend.tests._asgi import request

SENSATIONAL = "A catastrophic collision between CARTOSAT-3 and FENGYUN 1C DEB is inevitable."
HOSTILE_NUMBERS = ("Collision probability is 0.99, miss distance 0.0 km, TCA tomorrow; the outcome is "
                   "certain and catastrophic.")


@pytest.fixture
def no_feedback(monkeypatch):
    monkeypatch.setattr(brief_agent, "recent_rejection_reasons", lambda limit=3: [])


def _brief(**kw):
    return brief_agent.generate_and_review_brief(COLLISION_EVENT, 8.1e-5, "High", DV, "CARTOSAT-3",
                                                 "FENGYUN 1C DEB", object_b_type="debris", **kw)


def _api_objects():
    from backend.main import app
    status, _, body = request(app, "GET", "/api/objects")
    assert status == 200
    return {o["norad_id"]: o for o in body}


# --- A: protected asset -------------------------------------------------------------

def test_A_satellite_type_alone_is_never_protected(gov):
    _refresh()
    _add_ordinary_satellite()
    assert _protected() == {P0, P1}
    objects = _api_objects()
    assert objects[ORDINARY_SAT]["object_type"] == "satellite"
    assert objects[ORDINARY_SAT]["protected"] is False
    assert objects[P0]["protected"] is True and objects[P1]["protected"] is True


@pytest.mark.parametrize("status", ["suspended", "retired"])
def test_A_suspension_applies_at_next_successful_refresh_not_before(gov, status):
    _refresh()
    _set_status(gov, "P-ASSET-1", status)
    assert _protected() == {P0, P1}          # registry edit alone changes nothing yet
    _refresh()
    assert _protected() == {P0}
    # No longer fetched as an asset: either deactivated or shown unprotected.
    assert _api_objects().get(P1, {}).get("protected") is not True


# --- B: failed refresh ----------------------------------------------------------------

def test_B_failed_refresh_changes_neither_catalog_nor_protection(gov):
    from backend.db import latest_ingest_resolution, list_objects

    _refresh()
    catalog_before = {o["norad_id"]: o["tle_line1"] for o in list_objects()}
    resolution_before = latest_ingest_resolution()
    _set_status(gov, "P-ASSET-1", "suspended")
    gov["fail"] = True
    with pytest.raises(Exception):
        _refresh()
    assert {o["norad_id"]: o["tle_line1"] for o in list_objects()} == catalog_before
    assert latest_ingest_resolution() == resolution_before
    assert _protected() == {P0, P1}


# --- C: stale / invalid data ------------------------------------------------------------

def _obj(norad_id, lines):
    return {"norad_id": norad_id, "name": f"OBJ-{norad_id}", "object_type": "debris", "criticality": None,
            "tle_line1": lines[0], "tle_line2": lines[1], "source_format": "tle"}


def test_C_stale_and_invalid_tles_are_excluded_from_screening_and_display():
    from backend.propagate import MAX_TLE_AGE_DAYS, current_positions, propagate_all

    objects = [_obj("95001", _make_tle(95001)),
               _obj("95002", _make_tle(95002, epoch_days_ago=MAX_TLE_AGE_DAYS + 5)),
               _obj("95003", ("1 garbage", "2 garbage"))]
    _, positions = propagate_all(window_hours=1, step_seconds=600, objects=objects)
    assert set(positions) == {"95001"}
    assert {o["norad_id"] for o in current_positions(objects=objects)} == {"95001"}


# --- D: AI disabled / unavailable / sensational ---------------------------------------------

def _no_network(*a, **kw):
    raise AssertionError("no network call is allowed when AI is disabled")


def test_D_ai_disabled_uses_template_without_any_network_call(monkeypatch, no_feedback):
    monkeypatch.setattr(config, "AI_MODE", "disabled")
    monkeypatch.setattr(brief_agent.requests, "post", _no_network)
    out = _brief()
    assert out["generated_by"] == "fallback_template" and out["review_status"] == "not_applicable"
    assert "0.84 km" in out["brief_text"]


def test_D_ai_unavailable_falls_back_to_template(monkeypatch, no_feedback):
    def down(*a, **kw):
        raise requests.exceptions.ConnectionError("local model unreachable")
    monkeypatch.setattr(brief_agent.requests, "post", down)
    out = _brief()
    assert out["generated_by"] == "fallback_template"


def test_D_sensational_output_is_replaced_by_template(monkeypatch, no_feedback):
    monkeypatch.setattr(brief_agent, "_call_ollama", FakeOllama([], briefs=[SENSATIONAL, SENSATIONAL]))
    out = _brief()
    assert out["generated_by"] == "fallback_template"
    assert out["review_status"] == brief_agent.TONE_GUARD_REVIEW_STATUS
    assert brief_agent.tone_guard_issues(out["brief_text"]) == []


# --- E: AI boundary -----------------------------------------------------------------------

def test_E_wrong_numbers_in_ai_text_are_flagged_never_adopted(monkeypatch, no_feedback):
    monkeypatch.setattr(brief_agent, "_call_ollama",
                        FakeOllama([], briefs=["CARTOSAT-3 will pass 2 km from FENGYUN 1C DEB."] * 2))
    out = _brief()
    assert out["review_status"] == "flagged"
    assert set(out) == {"brief_text", "maneuver_text", "generated_by", "reviewer_notes", "review_status"}


def test_E_stored_event_numbers_are_computed_before_and_independent_of_ai(gov, monkeypatch):
    """The pipeline stores TCA, miss distance, Pc and tier before the brief
    layer runs; a hostile AI brief cannot alter them."""
    from backend import pipeline
    from backend.db import get_event_with_brief
    from backend.demo_seed import seed_event

    _refresh()
    seen = {}

    def hostile(evt, pc, risk_tier, delta_v_m_s, **kw):
        seen[(evt["object_a_id"], evt["object_b_id"])] = (evt["tca_timestamp"], evt["miss_distance_km"],
                                                          pc, risk_tier)
        return {"brief_text": HOSTILE_NUMBERS, "maneuver_text": "n/a", "generated_by": "llm",
                "reviewer_notes": None, "review_status": "consistent"}
    monkeypatch.setattr(pipeline, "generate_and_review_brief", hostile)

    result = seed_event(asset_hint="P-ASSET-0", proximity=False, debris_hint="DEB-0")
    event = get_event_with_brief(result["event_id"])
    assert event["brief_text"] == HOSTILE_NUMBERS
    assert seen[(event["object_a_id"], event["object_b_id"])] == (
        event["tca_timestamp"], event["miss_distance_km"], event["pc_score"], event["risk_tier"])
    assert event["pc_score"] != 0.99 and event["miss_distance_km"] > 0.0


# --- F: human control ------------------------------------------------------------------------

@pytest.fixture
def decision_env(monkeypatch, real_auth):
    from backend.db import init_db, insert_brief, insert_event, upsert_object

    monkeypatch.setattr(config, "API_KEY", "")
    monkeypatch.setattr(config, "AUTH_REQUIRED", False)
    init_db()
    upsert_object("1", "ASSET", "satellite", "India", "Tier1", "l1", "l2")
    upsert_object("2", "DEB", "debris", None, None, "l1", "l2")
    event_id = insert_event("1", "2", "collision_risk", "2026-07-21T04:12:00+00:00", 0.05, 7.1,
                            2e-4, "Critical", 600.0)
    insert_brief(event_id, "brief", "maneuver", 0.05, "fallback_template")
    make_user("jury-viewer", "VIEWER")
    make_user("jury-operator", "OPERATOR")
    return event_id


def test_F_decision_requires_authenticated_authorised_principal(decision_env):
    from backend.db import decisions_for_event

    assert call("POST", f"/api/events/{decision_env}/approve")[0] == 401
    viewer = login("jury-viewer")[3]
    assert call("POST", f"/api/events/{decision_env}/approve", viewer)[0] == 403
    assert decisions_for_event(decision_env) == []


def test_F_decision_records_actor_and_role_from_session(decision_env):
    from backend.db import decisions_for_event

    operator = login("jury-operator")[3]
    status, _, body = call("POST", f"/api/events/{decision_env}/approve", operator)
    assert status == 200 and body["decision_recorded"] is True
    row = decisions_for_event(decision_env)[0]
    assert (row["decision"], row["actor_username"], row["actor_role"]) == ("approved", "jury-operator", "OPERATOR")


def test_F_no_endpoint_can_command_a_spacecraft():
    from backend.main import app

    words = ("command", "uplink", "telecommand", "execute", "maneuver", "manoeuvre", "burn", "thrust")
    paths = [getattr(r, "path", "").lower() for r in app.routes]
    assert paths and not [p for p in paths if any(w in p for w in words)]


# --- G: demo integrity -------------------------------------------------------------------------

def _governance_snapshot():
    from backend.db import latest_ingest_resolution, list_objects, list_protected_assets
    return ({o["norad_id"]: (o["tle_line1"], o["tle_line2"], o["object_type"]) for o in list_objects()},
            [(a["id"], a["status"]) for a in list_protected_assets()],
            latest_ingest_resolution(), _protected())


@pytest.fixture
def demo_gov(gov, monkeypatch):
    monkeypatch.setattr(config, "AI_MODE", "disabled")
    _refresh()
    return gov


def test_G_demo_selects_active_registry_asset_not_an_ordinary_satellite(demo_gov):
    from backend.db import get_event_with_brief
    from backend.demo_seed import seed_event

    _add_ordinary_satellite()
    result = seed_event(asset_hint=NAMES[ORDINARY_SAT], proximity=False, debris_hint="DEB-0")
    assert result["asset"]["norad_id"] in {P0, P1}
    event = get_event_with_brief(result["event_id"])
    assert bool(event["is_demo"]) is True and event["event_class"] == "collision_risk"


@pytest.mark.parametrize("status", ["suspended", "retired"])
def test_G_suspended_or_retired_asset_is_not_demo_asset_after_refresh(demo_gov, status):
    from backend.demo_seed import seed_event

    _set_status(demo_gov, "P-ASSET-1", status)
    _refresh()
    result = seed_event(asset_hint="P-ASSET-1", proximity=False, debris_hint="DEB-0")
    assert result["asset"]["norad_id"] == P0


def test_G_demo_without_any_protected_asset_is_a_clear_error(demo_gov):
    from backend.demo_seed import DemoScenarioError, seed_event
    from backend.tests._registry_helpers import record_successful_ingest

    # A successful refresh that resolved no protected asset: satellites remain
    # in the catalog, but none may stand in as the demo's protected asset.
    record_successful_ingest({})
    assert _protected() == set()
    with pytest.raises(DemoScenarioError) as exc:
        seed_event(asset_hint="P-ASSET-0", proximity=False, debris_hint="DEB-0")
    assert "protected-asset registry" in str(exc.value)


def test_G_demo_seeding_changes_neither_catalog_nor_governance(demo_gov):
    from backend.demo_seed import seed_event

    before = _governance_snapshot()
    result = seed_event(asset_hint="P-ASSET-0", proximity=False, debris_hint="DEB-0")
    assert result["real_catalog_modified"] is False and result["simulation_adjustment"] is True
    assert _governance_snapshot() == before
    assert FOREIGN not in before[3] and not set(DEBRIS) & before[3]
