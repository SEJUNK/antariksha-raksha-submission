"""Screening horizon provenance: the horizon/step shown for an event (and on
the status payload) come from the screening run that produced it, not from
today's config; config is only a fallback for legacy rows."""

from unittest.mock import patch

import pytest

from backend.config import PROPAGATION_STEP_SECONDS, PROPAGATION_WINDOW_HOURS
from backend.provenance import get_event_provenance, get_live_data_status
from backend.tests.test_provenance import OBJECT_A, OBJECT_B, _collision_event


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.db.DB_PATH", tmp_path / "horizon_test.db")
    from backend.db import init_db
    init_db()


def _run(window_hours, step_seconds, started_at="2026-07-20T00:00:00+00:00"):
    from backend.db import insert_screening_run
    return insert_screening_run(started_at, "live", data_source="CelesTrak", object_count=2,
                                window_hours=window_hours, step_seconds=step_seconds,
                                candidate_pairs=0, collision_events=0, proximity_events=0)


def _prov(event):
    with patch("backend.provenance.get_event_with_brief", return_value=event), \
         patch("backend.provenance.list_objects", return_value=[OBJECT_A, OBJECT_B]), \
         patch("backend.provenance.tle_age_days_for", return_value=0.8):
        return get_event_provenance(event["id"])


def _status():
    with patch("backend.provenance.list_objects", return_value=[OBJECT_A, OBJECT_B]), \
         patch("backend.provenance.list_events", return_value=[]), \
         patch("backend.provenance.tle_age_days_for", return_value=1.0):
        return get_live_data_status()


def test_config_is_72h_60s():
    # Guards the assumptions below (config values are frozen).
    assert (PROPAGATION_WINDOW_HOURS, PROPAGATION_STEP_SECONDS) == (72, 60)


def test_event_provenance_shows_its_run_values_for_normal_run():
    run_id = _run(PROPAGATION_WINDOW_HOURS, PROPAGATION_STEP_SECONDS)
    prop = _prov({**_collision_event(), "screening_run_id": run_id})["propagation"]
    assert prop["horizon_hours"] == 72
    assert prop["step_seconds"] == 60
    assert prop["horizon_source"] == "screening_run"
    assert prop["screening_run_id"] == run_id


def test_historical_event_keeps_its_own_run_window_not_config():
    old = _run(24, 30, "2026-07-01T00:00:00+00:00")
    _run(72, 60)  # a later run under today's config
    prop = _prov({**_collision_event(), "screening_run_id": old})["propagation"]
    assert prop["horizon_hours"] == 24
    assert prop["step_seconds"] == 30
    assert prop["horizon_source"] == "screening_run"
    assert prop["screening_run_id"] == old


def test_legacy_event_without_run_uses_config_fallback():
    prop = _prov(_collision_event())["propagation"]  # no screening_run_id
    assert prop["horizon_hours"] == PROPAGATION_WINDOW_HOURS
    assert prop["step_seconds"] == PROPAGATION_STEP_SECONDS
    assert prop["horizon_source"] == "config_fallback"
    assert prop["screening_run_id"] is None


def test_event_whose_run_lacks_window_values_uses_config_fallback():
    run_id = _run(None, None)
    prop = _prov({**_collision_event(), "screening_run_id": run_id})["propagation"]
    assert prop["horizon_hours"] == PROPAGATION_WINDOW_HOURS
    assert prop["horizon_source"] == "config_fallback"
    assert prop["screening_run_id"] == run_id


def test_event_pointing_to_missing_run_uses_config_fallback():
    prop = _prov({**_collision_event(), "screening_run_id": 9999})["propagation"]
    assert prop["horizon_source"] == "config_fallback"
    assert prop["screening_run_id"] is None


def test_status_uses_latest_run_values_and_exposes_configured():
    _run(72, 60, "2026-07-01T00:00:00+00:00")
    latest = _run(24, 30)
    s = _status()
    assert s["latest_run"]["id"] == latest
    assert s["screening_window_hours"] == 24
    assert s["screening_step_seconds"] == 30
    assert s["screening_horizon_source"] == "screening_run"
    assert s["configured_window_hours"] == PROPAGATION_WINDOW_HOURS
    assert s["configured_step_seconds"] == PROPAGATION_STEP_SECONDS
    # Existing fields the frontend reads still agree.
    assert s["latest_run"]["window_hours"] == 24
    assert s["latest_run"]["step_seconds"] == 30


def test_status_shows_72h_runtime_value_for_normal_run():
    _run(PROPAGATION_WINDOW_HOURS, PROPAGATION_STEP_SECONDS)
    s = _status()
    assert s["screening_window_hours"] == 72
    assert s["screening_step_seconds"] == 60
    assert s["screening_horizon_source"] == "screening_run"


def test_status_without_runs_falls_back_to_config():
    s = _status()
    assert s["latest_run"] is None
    assert s["screening_window_hours"] == PROPAGATION_WINDOW_HOURS
    assert s["screening_step_seconds"] == PROPAGATION_STEP_SECONDS
    assert s["screening_horizon_source"] == "config_fallback"
