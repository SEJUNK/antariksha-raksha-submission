"""Extended event provenance (run provenance, AI disclosures, decision
history, demo overrides) and dashboard status mode/freshness rules."""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from backend.provenance import get_event_provenance, get_live_data_status
from backend.tests.test_provenance import OBJECT_A, OBJECT_B, _collision_event, _proximity_event


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.db.DB_PATH", tmp_path / "status_test.db")
    from backend.db import init_db
    init_db()
    return tmp_path / "status_test.db"


def _patched_provenance(event, objects=(OBJECT_A, OBJECT_B)):
    with patch("backend.provenance.get_event_with_brief", return_value=event), \
         patch("backend.provenance.list_objects", return_value=list(objects)), \
         patch("backend.provenance.tle_age_days_for", return_value=0.8):
        return get_event_provenance(event["id"])


# --- Extended event provenance -------------------------------------------------

def test_collision_provenance_reports_refined_tca_and_stored_pc_parameters():
    from backend.risk_score import PC_METHOD

    evt = {**_collision_event(), "pc_samples": None, "pc_method": PC_METHOD, "pc_sigma_km": 0.21,
           "pc_hard_body_radius_km": 0.02, "tca_refinement": "Bounded minimisation ... SGP4",
           "review_status": "consistent"}
    prov = _patched_provenance(evt)

    assert prov["analysis"]["tca_refinement"] == "Bounded minimisation ... SGP4"
    assert prov["analysis"]["state_evaluated_at"] == "TCA (refined)"
    p = prov["probability"]
    assert p["method"] == PC_METHOD
    assert p["deterministic"] is True
    assert p["samples"] is None
    assert p["pc_resolution"] is None
    assert p["sigma_km"] == 0.21
    assert abs(p["relative_sigma_km"] - 0.21 * 2 ** 0.5) < 1e-12
    assert p["hard_body_radius_km"] == 0.02
    assert p["state_evaluated_at"] == "TCA (refined)"
    assert p["simplified"] is True
    assert "NOT an operational covariance-based Pc" in p["label"]
    joined = " ".join(p["limitations"]).lower()
    assert "not propagated covariance" in joined
    assert "short-encounter" in joined
    assert "covariance" not in p["method"].lower()


def test_legacy_monte_carlo_event_is_described_as_monte_carlo():
    """Events scored before the analytic indicator (pc_samples set, no
    pc_method) must not be relabelled as analytic."""
    evt = {**_collision_event(), "pc_samples": 5000, "pc_method": None, "pc_sigma_km": 0.21,
           "pc_hard_body_radius_km": 0.02}
    p = _patched_provenance(evt)["probability"]
    assert "Monte Carlo" in p["method"]
    assert p["deterministic"] is False
    assert p["samples"] == 5000
    assert p["pc_resolution"] == 1 / 5000
    assert "resolution" in " ".join(p["limitations"]).lower()


def test_proximity_provenance_state_is_grid_minimum():
    prov = _patched_provenance(_proximity_event())
    assert prov["analysis"]["state_evaluated_at"] == "grid time of minimum separation"
    assert prov["probability"]["pc_resolution"] is None


def test_ai_provenance_labels_and_disclosures():
    prov = _patched_provenance({**_collision_event(), "review_status": "flagged"})
    ai = prov["ai"]
    assert ai["generated_by"] == "llm"
    assert ai["generated_by_label"].startswith("LLM (local Ollama")
    assert ai["review_status"] == "flagged"
    assert "not independent validation" in ai["review_description"]
    assert "Deterministic fact check" in ai["review_description"]
    assert "no model weights are modified" in ai["feedback_mechanism"]
    assert ai["ai_does"] and ai["ai_does_not"]

    fallback = _patched_provenance(_collision_event(generated_by="fallback_template"))
    assert fallback["ai"]["generated_by_label"] == "Deterministic template"


def test_provenance_mode_real_vs_demo():
    assert _patched_provenance(_collision_event())["provenance"]["mode"] == "REAL"
    demo = _patched_provenance(_collision_event(is_demo=1))
    assert demo["provenance"]["mode"] == "DEMO"
    assert demo["provenance"]["is_demo"] is True


def test_demo_event_shows_adjusted_object_from_override():
    from backend.db import set_demo_override

    set_demo_override("30003", OBJECT_A["tle_line1"], OBJECT_A["tle_line2"], "collision_demo", "10001")
    prov = _patched_provenance(_collision_event(is_demo=1))
    b = prov["data"]["object_b"]
    assert b["demo_adjusted"] is True
    assert b["derived_from_norad_id"] == "10001"
    assert b["demo_scenario"] == "collision_demo"
    assert prov["data"]["object_a"]["demo_adjusted"] is False

    # A REAL event never reports override data, even if one is lingering.
    real = _patched_provenance(_collision_event(is_demo=0))
    assert real["data"]["object_b"]["demo_adjusted"] is False


def test_provenance_includes_screening_run_and_decision_history():
    from backend.db import insert_decision, insert_ingest_run, insert_screening_run

    ingest_id = insert_ingest_run("CelesTrak", used_cache=False, object_count=2, counts={"A": 1, "B": 1, "C": 0})
    run_id = insert_screening_run(
        "2026-07-20T00:00:00+00:00", "live", data_source="CelesTrak", ingest_run_id=ingest_id,
        object_count=2, counts_by_type_json=json.dumps({"satellite": 1, "debris": 1}),
        tle_age_min_days=0.5, tle_age_max_days=1.5, tle_age_avg_days=1.0,
        window_hours=72, step_seconds=60, candidate_pairs=1, collision_events=1, proximity_events=0,
    )
    insert_decision("collision_risk", "A", "B", "High", 8.1e-5, 0.84, "llm", "approved", event_id=1)
    insert_decision("collision_risk", "A", "B", "High", 8.1e-5, 0.84, "llm", "dismissed",
                    rejection_reason="duplicate", event_id=1)
    insert_decision("collision_risk", "A", "B", "High", 8.1e-5, 0.84, "llm", "approved", event_id=99)

    prov = _patched_provenance({**_collision_event(), "screening_run_id": run_id, "status": "dismissed"})
    run = prov["data"]["screening_run"]
    assert run["id"] == run_id
    assert run["mode"] == "live"
    assert run["ingest_used_cache"] is False
    assert run["ingest_completed_at"]
    assert run["counts_by_type"] == {"satellite": 1, "debris": 1}
    assert run["tle_age_days"] == {"min": 0.5, "max": 1.5, "avg": 1.0}
    assert run["candidate_pairs"] == 1

    hd = prov["human_decision"]
    assert hd["status"] == "dismissed"
    # Newest first, and only this event's decisions.
    assert [d["decision"] for d in hd["history"]] == ["dismissed", "approved"]


# --- Status mode / freshness ---------------------------------------------------

def _status():
    with patch("backend.provenance.list_objects", return_value=[OBJECT_A, OBJECT_B]), \
         patch("backend.provenance.list_events", return_value=[]), \
         patch("backend.provenance.tle_age_days_for", side_effect=[1.0, 45.0]):
        return get_live_data_status()


def _run(mode, ingest_id=None, demo_scenario=None):
    from backend.db import insert_screening_run
    return insert_screening_run(
        datetime.now(timezone.utc).isoformat(), mode, ingest_run_id=ingest_id,
        demo_scenario_json=json.dumps(demo_scenario) if demo_scenario else None,
    )


def _ingest(used_cache=False, hours_ago=None):
    from backend.db import get_connection, insert_ingest_run
    run_id = insert_ingest_run("CelesTrak", used_cache=used_cache, object_count=2, counts={})
    if hours_ago is not None:
        conn = get_connection()
        conn.execute("UPDATE ingest_runs SET completed_at = ? WHERE id = ?",
                     ((datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat(), run_id))
        conn.commit()
        conn.close()
    return run_id


def test_status_not_screened_without_runs():
    status = _status()
    assert status["mode"] == "not_screened"
    assert status["mode_label"] == "NOT SCREENED"
    assert status["latest_run"] is None
    assert status["last_refresh_at"] is None
    # Excluded-stale count comes from the same single age pass (45 d > 30 d).
    assert status["objects_excluded_stale"] == 1
    assert status["max_tle_age_days"] == 30
    assert any("excluded" in w for w in status["warnings"])


def test_status_never_live_without_recorded_ingest():
    _run("cached")
    status = _status()
    assert status["mode"] == "stale"
    assert status["mode_label"] == "STALE DATA"


def test_status_live_after_fresh_network_ingest():
    _run("live", _ingest())
    status = _status()
    assert status["mode"] == "live"
    assert status["mode_label"] == "LIVE DATA"
    assert status["last_refresh_used_cache"] is False
    # Catalog timestamp comes from the ingest record, not objects.last_updated.
    assert status["timestamp"] == status["last_refresh_at"]
    assert status["latest_run"]["mode"] == "live"


def test_status_cached_when_ingest_fell_back_to_cache():
    _run("cached", _ingest(used_cache=True))
    status = _status()
    assert status["mode"] == "cached"
    assert status["mode_label"] == "CACHED DATA"
    assert status["last_refresh_used_cache"] is True


def test_status_stale_when_refresh_older_than_policy():
    from backend.config import CATALOG_REFRESH_STALE_HOURS
    _run("live", _ingest(hours_ago=CATALOG_REFRESH_STALE_HOURS + 1))
    status = _status()
    assert status["mode"] == "stale"
    assert status["catalog_refresh_stale_hours"] == CATALOG_REFRESH_STALE_HOURS


def test_status_demo_when_latest_run_is_demo():
    ingest_id = _ingest()
    _run("live", ingest_id)
    _run("demo", ingest_id, demo_scenario={"scenario": "collision_demo"})
    status = _status()
    assert status["mode"] == "demo"
    assert status["mode_label"] == "DEMO MODE"
    assert status["demo_scenario"] == {"scenario": "collision_demo"}
    assert any("Demo mode" in w for w in status["warnings"])
