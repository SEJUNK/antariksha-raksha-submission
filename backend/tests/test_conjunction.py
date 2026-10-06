from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import numpy as np
import pytest

from backend.conjunction import find_close_approaches

STEP_SECONDS = 60


@pytest.fixture(autouse=True)
def _registry_group_a(request, monkeypatch):
    """In these synthetic grids "10001" is the object resolved to an active
    protected-asset registry entry (Group A comes from the registry, not from
    object_type)."""
    if "demo_db" in request.fixturenames:
        return  # full pipeline tests use the real registry resolution
    monkeypatch.setattr("backend.conjunction.resolved_protected_norad_ids", lambda: {"10001"})


def _make_times(n):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [start + timedelta(seconds=i * STEP_SECONDS) for i in range(n)]


def _fake_objects(a_id="10001", b_id="30003"):
    return [
        {"norad_id": a_id, "object_type": "satellite"},
        {"norad_id": b_id, "object_type": "debris"},
    ]


def test_close_approach_detected_when_paths_cross():
    n = 20
    times = _make_times(n)
    pos_a = np.tile(np.array([7000.0, 0.0, 0.0]), (n, 1))
    pos_b = pos_a.copy()
    for i in range(n):
        # Debris crosses within 1 km at the midpoint, otherwise far away.
        offset = abs(i - n // 2) * 50.0 + 0.5
        pos_b[i] = pos_a[i] + np.array([offset, 0.0, 0.0])

    positions_grid = {"10001": pos_a, "30003": pos_b}

    with patch("backend.conjunction.list_objects", return_value=_fake_objects()):
        events = find_close_approaches(times, positions_grid, threshold_km=10.0, step_seconds=STEP_SECONDS)

    assert len(events) == 1
    evt = events[0]
    assert evt["object_a_id"] == "10001"
    assert evt["object_b_id"] == "30003"
    assert evt["miss_distance_km"] < 10.0


def test_no_event_when_objects_stay_far_apart():
    n = 20
    times = _make_times(n)
    pos_a = np.tile(np.array([7000.0, 0.0, 0.0]), (n, 1))
    pos_b = pos_a + np.array([500.0, 0.0, 0.0])  # constant 500 km separation

    positions_grid = {"10001": pos_a, "30003": pos_b}

    with patch("backend.conjunction.list_objects", return_value=_fake_objects()):
        events = find_close_approaches(times, positions_grid, threshold_km=10.0, step_seconds=STEP_SECONDS)

    assert events == []


def test_only_group_a_pairs_are_screened():
    """Two non-Indian objects passing close to each other should NOT be
    flagged -- conjunction.py only screens Indian-asset-vs-other pairs."""
    n = 20
    times = _make_times(n)
    pos_b = np.tile(np.array([7000.0, 0.0, 0.0]), (n, 1))
    pos_c = pos_b + np.array([0.1, 0.0, 0.0])  # 100 m apart, both non-Indian

    positions_grid = {"30003": pos_b, "30004": pos_c}
    objects = [
        {"norad_id": "30003", "object_type": "debris"},
        {"norad_id": "30004", "object_type": "foreign_sat"},
    ]

    with patch("backend.conjunction.list_objects", return_value=objects):
        events = find_close_approaches(times, positions_grid, threshold_km=10.0, step_seconds=STEP_SECONDS)

    assert events == []




# --- Refined TCA: single source of truth --------------------------------------
#
# Known linear relative motion: A moves along +y at 7.5 km/s; B is offset by
# 0.3 km radially and drifts past A at 0.2 km/s along-track, so
#   r_rel(t) = r_a - r_b = [0.3, -0.2 * (t - T_STAR), 0]
# The analytic TCA is T_STAR (deliberately between grid samples and far from
# the window midpoint), miss distance 0.3 km, relative speed 0.2 km/s.
T_STAR_S = 623.7
MISS_KM = 0.3
REL_SPEED_KM_S = 0.2
N_LINEAR = 40


def _linear_state_a(t_s):
    return np.array([7000.0, 7.5 * t_s, 0.0]), np.array([0.0, 7.5, 0.0])


def _linear_state_b(t_s):
    pos = np.array([7000.0 - MISS_KM, 7.5 * t_s + REL_SPEED_KM_S * (t_s - T_STAR_S), 0.0])
    return pos, np.array([0.0, 7.5 + REL_SPEED_KM_S, 0.0])


def _linear_grid():
    times = _make_times(N_LINEAR)
    secs = [i * STEP_SECONDS for i in range(N_LINEAR)]
    pos_a = np.array([_linear_state_a(t)[0] for t in secs])
    pos_b = np.array([_linear_state_b(t)[0] for t in secs])
    return times, {"10001": pos_a, "30003": pos_b}


def _screen(times, grid, **kwargs):
    with patch("backend.conjunction.list_objects", return_value=_fake_objects()):
        return find_close_approaches(times, grid, threshold_km=10.0, step_seconds=STEP_SECONDS, **kwargs)


def test_refined_tca_is_analytic_argmin_not_grid_point_or_midpoint():
    times, grid = _linear_grid()
    events = _screen(times, grid)
    assert len(events) == 1
    evt = events[0]

    tca = datetime.fromisoformat(evt["tca_timestamp"])
    expected = times[0] + timedelta(seconds=T_STAR_S)
    assert abs((tca - expected).total_seconds()) < 1e-3
    # Between grid samples, and not the window midpoint.
    assert tca not in times
    assert abs((tca - times[N_LINEAR // 2]).total_seconds()) > STEP_SECONDS
    # tz-aware UTC.
    assert tca.utcoffset() == timedelta(0)


def test_miss_distance_and_rel_velocity_evaluated_at_refined_tca():
    times, grid = _linear_grid()
    evt = _screen(times, grid)[0]

    assert abs(evt["miss_distance_km"] - MISS_KM) < 1e-6
    assert abs(evt["rel_velocity_km_s"] - REL_SPEED_KM_S) < 1e-9

    pa_expected, _ = _linear_state_a(T_STAR_S)
    pb_expected, _ = _linear_state_b(T_STAR_S)
    assert np.allclose(evt["pos_a_tca_km"], pa_expected, atol=1e-4)
    assert np.allclose(evt["pos_b_tca_km"], pb_expected, atol=1e-4)
    # The reported miss distance is exactly the separation of the reported states.
    sep = np.linalg.norm(np.array(evt["pos_a_tca_km"]) - np.array(evt["pos_b_tca_km"]))
    assert abs(sep - evt["miss_distance_km"]) < 1e-12


def test_refined_miss_never_exceeds_grid_miss():
    times, grid = _linear_grid()
    evt = _screen(times, grid)[0]
    grid_dist = np.linalg.norm(grid["10001"] - grid["30003"], axis=1)
    assert evt["grid_miss_distance_km"] == grid_dist.min()
    assert evt["miss_distance_km"] <= evt["grid_miss_distance_km"]
    # For this geometry the grid sample is ~4.7 km off; refinement recovers 0.3 km.
    assert evt["grid_miss_distance_km"] > 4.0


def test_refined_miss_not_worse_than_grid_for_kinked_geometry():
    """Piecewise-linear (non-smooth) separation: interpolation is inexact
    here, but the s=0 candidate guarantees refined miss <= grid miss."""
    n = 20
    times = _make_times(n)
    pos_a = np.tile(np.array([7000.0, 0.0, 0.0]), (n, 1))
    pos_b = pos_a.copy()
    mid = n // 2
    for i in range(n):
        offset = (mid - i) * 80.0 + 0.5 if i <= mid else (i - mid) * 50.0 + 0.5
        pos_b[i] = pos_a[i] + np.array([offset, 0.0, 0.0])
    evt = _screen(times, {"10001": pos_a, "30003": pos_b})[0]
    assert evt["miss_distance_km"] <= 0.5 + 1e-12
    tca = datetime.fromisoformat(evt["tca_timestamp"])
    assert abs((tca - times[mid]).total_seconds()) <= STEP_SECONDS


def test_supplied_state_functions_are_used_for_refinement():
    times, grid = _linear_grid()
    t0 = times[0]
    calls = {"n": 0}

    def wrap(fn):
        def state(when):
            calls["n"] += 1
            return fn((when - t0).total_seconds())
        return state

    state_fns = {"10001": wrap(_linear_state_a), "30003": wrap(_linear_state_b)}
    evt = _screen(times, grid, state_fns=state_fns)[0]

    assert calls["n"] > 0
    assert "SGP4" in evt["tca_refinement"]
    assert abs(evt["miss_distance_km"] - MISS_KM) < 1e-6
    assert abs(evt["rel_velocity_km_s"] - REL_SPEED_KM_S) < 1e-12


def test_minimum_at_window_start_refines_one_sided():
    """Objects already receding at the first grid sample: TCA must not be
    extrapolated before the screening window."""
    n = 10
    times = _make_times(n)
    pos_a = np.tile(np.array([7000.0, 0.0, 0.0]), (n, 1))
    pos_b = pos_a + np.array([[1.0 + 0.5 * i, 0.0, 0.0] for i in range(n)])
    evt = _screen(times, {"10001": pos_a, "30003": pos_b})[0]
    assert datetime.fromisoformat(evt["tca_timestamp"]) >= times[0]
    assert abs(evt["miss_distance_km"] - 1.0) < 1e-6


def test_candidate_pair_count_reported():
    times, grid = _linear_grid()
    stats = {}
    _screen(times, grid, stats=stats)
    assert stats["candidate_pairs"] == 1


def test_objects_param_overrides_catalog_lookup():
    times, grid = _linear_grid()
    with patch("backend.conjunction.list_objects", side_effect=AssertionError("must not be called")):
        events = find_close_approaches(times, grid, threshold_km=10.0, step_seconds=STEP_SECONDS,
                                       objects=_fake_objects())
    assert len(events) == 1


# --- Fast crossings: screening must not depend on a grid sample landing ------
# --- inside the threshold ------------------------------------------------------

def _crossing_grid(n, t_star_s, miss_km, speed_km_s):
    """A at rest; B crosses at speed_km_s with closest approach miss_km at t_star_s."""
    times = _make_times(n)
    secs = np.arange(n) * STEP_SECONDS
    pos_a = np.tile(np.array([7000.0, 0.0, 0.0]), (n, 1))
    pos_b = pos_a + np.column_stack([np.full(n, miss_km), speed_km_s * (secs - t_star_s), np.zeros(n)])
    return times, {"10001": pos_a, "30003": pos_b}


def test_fast_crossing_detected_although_no_grid_sample_within_threshold():
    """10 km/s crossing, true miss 1 km, TCA exactly between two samples:
    the closest grid sample is ~300 km away, yet the encounter is real."""
    times, grid = _crossing_grid(40, t_star_s=630.0, miss_km=1.0, speed_km_s=10.0)
    assert np.linalg.norm(grid["10001"] - grid["30003"], axis=1).min() > 250.0

    stats = {}
    events = _screen(times, grid, stats=stats)
    assert len(events) == 1
    evt = events[0]
    tca = datetime.fromisoformat(evt["tca_timestamp"])
    assert abs((tca - (times[0] + timedelta(seconds=630.0))).total_seconds()) < 1e-3
    assert abs(evt["miss_distance_km"] - 1.0) < 1e-6
    assert abs(evt["rel_velocity_km_s"] - 10.0) < 1e-9
    assert evt["grid_miss_distance_km"] > 250.0
    assert stats["candidate_encounters"] == 1 and stats["encounters"] == 1


def test_fast_crossing_detected_with_supplied_state_functions():
    t0 = _make_times(1)[0]

    def state_a(when):
        return np.array([7000.0, 0.0, 0.0]), np.zeros(3)

    def state_b(when):
        s = (when - t0).total_seconds()
        return np.array([7001.0, 10.0 * (s - 630.0), 0.0]), np.array([0.0, 10.0, 0.0])

    times, grid = _crossing_grid(40, t_star_s=630.0, miss_km=1.0, speed_km_s=10.0)
    stats = {}
    events = _screen(times, grid, state_fns={"10001": state_a, "30003": state_b}, stats=stats)
    assert len(events) == 1
    assert "SGP4" in events[0]["tca_refinement"]
    assert abs(events[0]["miss_distance_km"] - 1.0) < 1e-6
    assert stats["sgp4_refinements"] == 1


def test_fast_crossing_with_large_true_miss_not_reported():
    times, grid = _crossing_grid(40, t_star_s=630.0, miss_km=50.0, speed_km_s=10.0)
    stats = {}
    assert _screen(times, grid, stats=stats) == []
    # It is examined (grid pad admits it) but rejected on the refined miss.
    assert stats["candidate_encounters"] == 1


def test_hopeless_candidates_skip_sgp4_refinement():
    """Interpolated miss far above threshold -> no propagator refinement."""
    def boom(when):
        raise AssertionError("SGP4 refinement must not run for a hopeless candidate")

    times, grid = _crossing_grid(40, t_star_s=630.0, miss_km=50.0, speed_km_s=10.0)
    stats = {}
    assert _screen(times, grid, state_fns={"10001": boom, "30003": boom}, stats=stats) == []
    assert stats["sgp4_refinements"] == 0


def test_two_separate_encounters_in_window_are_both_reported():
    n = 120
    times = _make_times(n)
    secs = np.arange(n) * STEP_SECONDS
    pos_a = np.tile(np.array([7000.0, 0.0, 0.0]), (n, 1))
    # Two passes (t=1230 s, miss 2 km; t=5430 s, miss 4 km), far apart in between.
    y = np.where(secs < 3300, 8.0 * (secs - 1230.0), 8.0 * (secs - 5430.0))
    x = np.where(secs < 3300, 2.0, 4.0)
    pos_b = pos_a + np.column_stack([x, y, np.zeros(n)])
    events = _screen(times, {"10001": pos_a, "30003": pos_b})
    assert sorted(round(e["miss_distance_km"], 6) for e in events) == [2.0, 4.0]


def test_slow_coorbital_drift_gives_one_encounter_not_one_per_noise_minimum():
    n = 200
    times = _make_times(n)
    rng = np.random.default_rng(0)
    pos_a = np.tile(np.array([7000.0, 0.0, 0.0]), (n, 1))
    pos_b = pos_a + np.column_stack([np.full(n, 5.0) + rng.normal(0, 0.001, n), np.zeros(n), np.zeros(n)])
    events = _screen(times, {"10001": pos_a, "30003": pos_b})
    assert len(events) == 1
    assert events[0]["miss_distance_km"] < 5.0
