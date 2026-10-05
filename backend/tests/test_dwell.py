"""Proximity dwell definition and boundaries (backend/threat.py).

Detection gate: >= PROXIMITY_DWELL_MIN_STEPS (10) consecutive in-watch grid
samples. Reported dwell_minutes: elapsed time (N - 1) * step.
"""

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from backend.config import PROXIMITY_DWELL_MIN_STEPS, PROXIMITY_WATCH_KM
from backend.threat import detect_proximity_operations, dwell_minutes_for_run, risk_tier_for_proximity

STEP = 60
N_GRID = 120
OBJECTS = [
    {"norad_id": "10001", "object_type": "satellite"},
    {"norad_id": "20002", "object_type": "foreign_sat"},
]


def _run_with_in_watch_samples(n_in, offset_km=5.0, start_idx=20):
    """Grid where exactly `n_in` consecutive samples are inside the watch
    radius (at `offset_km`) and every other sample is far outside."""
    times = [datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=i * STEP) for i in range(N_GRID)]
    pos_a = np.tile(np.array([7000.0, 0.0, 0.0]), (N_GRID, 1))
    pos_b = pos_a + np.array([500.0, 0.0, 0.0])
    pos_b[start_idx:start_idx + n_in] = pos_a[start_idx:start_idx + n_in] + np.array([offset_km, 0.0, 0.0])
    return detect_proximity_operations(times, {"10001": pos_a, "20002": pos_b},
                                       step_seconds=STEP, objects=OBJECTS)


def test_documented_thresholds_unchanged():
    assert PROXIMITY_DWELL_MIN_STEPS == 10
    assert PROXIMITY_WATCH_KM == 25.0


@pytest.mark.parametrize("n,expected", [(0, 0.0), (1, 0.0), (2, 1.0), (10, 9.0), (11, 10.0), (4321, 4320.0)])
def test_dwell_is_elapsed_time_between_first_and_last_sample(n, expected):
    assert dwell_minutes_for_run(n, STEP) == expected


def test_one_sample_not_flagged():
    assert _run_with_in_watch_samples(1) == []


def test_two_samples_not_flagged():
    assert _run_with_in_watch_samples(2) == []


def test_just_below_gate_not_flagged():
    assert _run_with_in_watch_samples(PROXIMITY_DWELL_MIN_STEPS - 1) == []


def test_exact_gate_flagged_with_elapsed_dwell():
    [evt] = _run_with_in_watch_samples(PROXIMITY_DWELL_MIN_STEPS)
    assert evt["dwell_minutes"] == pytest.approx(9.0)  # 10 samples span 9 minutes


def test_just_above_gate_flagged():
    [evt] = _run_with_in_watch_samples(PROXIMITY_DWELL_MIN_STEPS + 1)
    assert evt["dwell_minutes"] == pytest.approx(10.0)


def test_high_tier_needs_more_than_60_elapsed_minutes():
    # 61 samples span exactly 60 min -> not > 60 -> Medium; 62 samples -> High.
    [evt61] = _run_with_in_watch_samples(61, offset_km=5.0)
    [evt62] = _run_with_in_watch_samples(62, offset_km=5.0)
    assert evt61["dwell_minutes"] == pytest.approx(60.0)
    assert risk_tier_for_proximity(evt61["dwell_minutes"], evt61["min_distance_km"]) == "Medium"
    assert risk_tier_for_proximity(evt62["dwell_minutes"], evt62["min_distance_km"]) == "High"


def test_risk_tier_thresholds_preserved():
    assert risk_tier_for_proximity(60.0, 5.0) == "Medium"
    assert risk_tier_for_proximity(60.01, 5.0) == "High"
    assert risk_tier_for_proximity(120.0, 10.0) == "Medium"
    assert risk_tier_for_proximity(120.0, 9.99) == "High"


def test_proximity_demo_still_high_tier(demo_db):
    """Documented proximity demo outcome (docs/technical/TESTING_VALIDATION.md): a
    proximity_watch event, tier High."""
    from backend.db import get_event_with_brief
    from backend.demo_seed import seed_event

    result = seed_event(asset_hint="TEST-ASSET", proximity=True, foreign_hint="TEST-FOREIGN")
    event = get_event_with_brief(result["event_id"])
    assert event["event_class"] == "proximity_watch"
    assert event["risk_tier"] == "High"


# Reuse the throwaway-DB demo fixture from the demo reliability tests.
from backend.tests.test_demo_seed import demo_db  # noqa: E402,F401
