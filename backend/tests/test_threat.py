from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import numpy as np

from backend.threat import detect_proximity_operations

STEP_SECONDS = 60


def _make_times(n):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [start + timedelta(seconds=i * STEP_SECONDS) for i in range(n)]


def _fake_objects(a_id="10001", c_id="20002"):
    return [
        {"norad_id": a_id, "object_type": "satellite"},
        {"norad_id": c_id, "object_type": "foreign_sat"},
    ]


def test_dwell_event_detected_for_sustained_close_approach():
    n = 30
    times = _make_times(n)
    # Object A on a fixed track; object B co-orbits within 5 km for steps 5-24
    # (20 consecutive steps >= PROXIMITY_DWELL_MIN_STEPS=10), then diverges.
    pos_a = np.tile(np.array([7000.0, 0.0, 0.0]), (n, 1))
    pos_b = pos_a.copy()
    for i in range(n):
        if 5 <= i < 25:
            pos_b[i] = pos_a[i] + np.array([5.0, 0.0, 0.0])
        else:
            pos_b[i] = pos_a[i] + np.array([500.0, 0.0, 0.0])

    positions_grid = {a_id_: pos for a_id_, pos in zip(["10001", "20002"], [pos_a, pos_b])}

    with patch("backend.threat.list_objects", return_value=_fake_objects()):
        events = detect_proximity_operations(times, positions_grid, step_seconds=STEP_SECONDS)

    assert len(events) == 1
    evt = events[0]
    assert evt["object_a_id"] == "10001"
    assert evt["object_b_id"] == "20002"
    assert evt["dwell_minutes"] >= 10.0
    assert evt["min_distance_km"] < PROXIMITY_WATCH_KM_TEST_VALUE


PROXIMITY_WATCH_KM_TEST_VALUE = 25.0


def test_fast_fly_through_not_flagged():
    n = 30
    times = _make_times(n)
    pos_a = np.tile(np.array([7000.0, 0.0, 0.0]), (n, 1))
    pos_b = pos_a.copy()
    for i in range(n):
        if i in (14, 15):  # only 2 consecutive steps under threshold
            pos_b[i] = pos_a[i] + np.array([5.0, 0.0, 0.0])
        else:
            pos_b[i] = pos_a[i] + np.array([500.0, 0.0, 0.0])

    positions_grid = {"10001": pos_a, "20002": pos_b}

    with patch("backend.threat.list_objects", return_value=_fake_objects()):
        events = detect_proximity_operations(times, positions_grid, step_seconds=STEP_SECONDS)

    assert events == []


def test_proximity_tca_is_time_of_minimum_separation_not_dwell_midpoint():
    """Dwell segment spans steps 5-24 (midpoint 15) but the closest point is
    at step 8 -- the reported TCA must match min_distance_km's instant."""
    n = 30
    times = _make_times(n)
    pos_a = np.tile(np.array([7000.0, 0.0, 0.0]), (n, 1))
    pos_b = pos_a.copy()
    for i in range(n):
        if 5 <= i < 25:
            pos_b[i] = pos_a[i] + np.array([2.0 + abs(i - 8) * 0.5, 0.0, 0.0])
        else:
            pos_b[i] = pos_a[i] + np.array([500.0, 0.0, 0.0])

    events = detect_proximity_operations(times, {"10001": pos_a, "20002": pos_b},
                                         step_seconds=STEP_SECONDS, objects=_fake_objects())
    assert len(events) == 1
    evt = events[0]
    assert evt["min_distance_km"] == 2.0
    assert evt["tca_timestamp"] == times[8].isoformat()
    assert evt["tca_timestamp"] != times[15].isoformat()
    # Geometry classification still produced.
    assert evt["geometry"] in ("closing", "receding", "station-keeping")
