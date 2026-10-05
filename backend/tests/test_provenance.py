import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from backend.config import (
    PC_N_SAMPLES,
    PROPAGATION_STEP_SECONDS,
    PROPAGATION_WINDOW_HOURS,
    SCREENING_THRESHOLD_KM,
)
from backend.provenance import get_event_provenance, get_live_data_status


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    """Run-provenance / decision / override lookups read the DB; point them
    at an empty throwaway SQLite file (never the user's data/antariksha.db)."""
    monkeypatch.setattr("backend.db.DB_PATH", tmp_path / "prov_test.db")
    from backend.db import init_db
    init_db()
    return tmp_path / "prov_test.db"

OBJECT_A = {
    "norad_id": "10001", "name": "CARTOSAT-3", "object_type": "satellite",
    "owner_country": "India", "criticality": "Tier1",
    "tle_line1": "1 10001U 20001A   26001.00000000  .00000000  00000-0  00000-0 0  9999",
    "tle_line2": "2 10001  98.0000 100.0000 0001000  90.0000 270.0000 14.50000000    01",
    "last_updated": "2026-01-01T00:00:00+00:00",
}
OBJECT_B = {
    "norad_id": "30003", "name": "FENGYUN 1C DEB", "object_type": "debris",
    "owner_country": None, "criticality": None,
    "tle_line1": "1 30003U 99999A   26001.00000000  .00000000  00000-0  00000-0 0  9999",
    "tle_line2": "2 30003  98.5000 110.0000 0002000  80.0000 280.0000 14.60000000    02",
    "last_updated": "2026-01-02T00:00:00+00:00",
}


def _collision_event(is_demo=0, generated_by="llm"):
    return {
        "id": 1, "object_a_id": "10001", "object_b_id": "30003",
        "event_class": "collision_risk", "tca_timestamp": "2026-07-21T04:12:00+00:00",
        "miss_distance_km": 0.84, "rel_velocity_km_s": 14.2, "pc_score": 8.1e-5,
        "risk_tier": "High", "priority_score": 243.0, "is_demo": is_demo,
        "created_at": "2026-07-20T00:00:00+00:00",
        "brief_text": "brief", "maneuver_text": "maneuver", "delta_v_ms": 0.05,
        "status": "pending", "reviewer_notes": None, "generated_by": generated_by,
    }


def _proximity_event(is_demo=0):
    return {
        "id": 2, "object_a_id": "10001", "object_b_id": "30003",
        "event_class": "proximity_watch", "tca_timestamp": "2026-07-21T04:12:00+00:00",
        "miss_distance_km": 12.0, "rel_velocity_km_s": None, "pc_score": 0.0,
        "risk_tier": "Medium", "priority_score": 0.05, "is_demo": is_demo,
        "created_at": "2026-07-20T00:00:00+00:00",
        "brief_text": "brief", "maneuver_text": "maneuver", "delta_v_ms": None,
        "status": "pending", "reviewer_notes": None, "generated_by": "llm",
    }


def test_provenance_retrieved_for_valid_collision_event():
    with patch("backend.provenance.get_event_with_brief", return_value=_collision_event()), \
         patch("backend.provenance.list_objects", return_value=[OBJECT_A, OBJECT_B]), \
         patch("backend.provenance.tle_age_days_for", return_value=0.8):
        prov = get_event_provenance(1)

    assert prov is not None
    # Object metadata correctly associated with the event.
    assert prov["data"]["object_a"]["norad_id"] == "10001"
    assert prov["data"]["object_a"]["name"] == "CARTOSAT-3"
    assert prov["data"]["object_a"]["tle_age_days"] == 0.8
    assert prov["data"]["object_b"]["norad_id"] == "30003"

    # Propagation config comes from actual configuration, not hardcoded.
    assert prov["propagation"]["horizon_hours"] == PROPAGATION_WINDOW_HOURS
    assert prov["propagation"]["step_seconds"] == PROPAGATION_STEP_SECONDS

    # Analysis values match the event, screening threshold from config.
    assert prov["analysis"]["screening_threshold_km"] == SCREENING_THRESHOLD_KM
    assert prov["analysis"]["tca"] == "2026-07-21T04:12:00+00:00"
    assert prov["analysis"]["miss_distance_km"] == 0.84
    assert prov["analysis"]["relative_velocity_km_s"] == 14.2

    # Probability configuration comes from actual configuration.
    assert prov["probability"]["samples"] is None
    assert "analytic encounter-plane" in prov["probability"]["method"].lower()
    assert prov["probability"]["deterministic"] is True
    assert "NOT an operational covariance-based Pc" in prov["probability"]["label"]

    # Technical-accuracy rule: must not claim full covariance analysis.
    assert "NOT full covariance" in prov["probability"]["uncertainty_model"]
    assert "covariance" not in prov["probability"]["method"].lower()

    # Risk info matches the event.
    assert prov["risk"]["tier"] == "High"
    assert prov["risk"]["criticality"] == "Tier1"
    assert prov["risk"]["priority_score"] == 243.0

    # AI model info reflects actual configuration when the LLM path was used.
    assert prov["ai"]["used"] is True
    assert prov["ai"]["model"]
    assert prov["ai"]["involved_in_numerical_calculation"] is False

    # Demo status correctly represented.
    assert prov["provenance"]["is_demo"] is False


def test_proximity_event_has_no_collision_probability():
    with patch("backend.provenance.get_event_with_brief", return_value=_proximity_event()), \
         patch("backend.provenance.list_objects", return_value=[OBJECT_A, OBJECT_B]), \
         patch("backend.provenance.tle_age_days_for", return_value=1.0):
        prov = get_event_provenance(2)

    assert prov["analysis"]["relative_velocity_km_s"] is None
    assert prov["probability"]["samples"] is None
    assert "Not computed" in prov["probability"]["method"]


def test_demo_status_true_when_event_flagged_demo():
    with patch("backend.provenance.get_event_with_brief", return_value=_collision_event(is_demo=1)), \
         patch("backend.provenance.list_objects", return_value=[OBJECT_A, OBJECT_B]), \
         patch("backend.provenance.tle_age_days_for", return_value=0.8):
        prov = get_event_provenance(1)

    assert prov["provenance"]["is_demo"] is True


def test_ai_fallback_template_reflected_when_llm_not_used():
    with patch("backend.provenance.get_event_with_brief", return_value=_collision_event(generated_by="fallback_template")), \
         patch("backend.provenance.list_objects", return_value=[OBJECT_A, OBJECT_B]), \
         patch("backend.provenance.tle_age_days_for", return_value=0.8):
        prov = get_event_provenance(1)

    assert prov["ai"]["used"] is False
    assert prov["ai"]["model"] is None
    assert prov["ai"]["involved_in_numerical_calculation"] is False


def test_missing_object_metadata_does_not_crash():
    """If an object referenced by the event is no longer in the objects
    table (e.g. decayed/removed), provenance must degrade gracefully."""
    with patch("backend.provenance.get_event_with_brief", return_value=_collision_event()), \
         patch("backend.provenance.list_objects", return_value=[]), \
         patch("backend.provenance.tle_age_days_for", return_value=0.8):
        prov = get_event_provenance(1)

    assert prov["data"]["object_a"] is None
    assert prov["data"]["object_b"] is None
    assert prov["risk"]["criticality"] is None


def test_event_not_found_returns_none():
    with patch("backend.provenance.get_event_with_brief", return_value=None):
        assert get_event_provenance(999) is None


def test_live_data_status_reports_source_freshness_and_counts():
    with patch("backend.provenance.list_objects", return_value=[OBJECT_A, OBJECT_B]), \
         patch("backend.provenance.list_events", return_value=[_collision_event(), _proximity_event()]), \
         patch("backend.provenance.tle_age_days_for", side_effect=[1.0, 3.0]):
        status = get_live_data_status()

    assert "CelesTrak" in status["source"]
    # Most recent of the two objects' last_updated timestamps.
    assert status["timestamp"] == "2026-01-02T00:00:00+00:00"
    assert status["objects_ingested"] == {"satellite": 1, "debris": 1, "foreign_sat": 0}
    assert status["object_count"] == 2
    assert status["tle_age_days"] == {"min": 1.0, "max": 3.0, "avg": 2.0}
    assert status["screening_window_hours"] == PROPAGATION_WINDOW_HOURS
    assert status["conjunction_candidates"] == 1
    assert status["proximity_candidates"] == 1


def test_live_data_status_handles_no_objects_without_crashing():
    with patch("backend.provenance.list_objects", return_value=[]), \
         patch("backend.provenance.list_events", return_value=[]):
        status = get_live_data_status()

    assert status["timestamp"] is None
    assert status["object_count"] == 0
    assert status["tle_age_days"] == {"min": None, "max": None, "avg": None}
    assert status["conjunction_candidates"] == 0
