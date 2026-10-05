from datetime import datetime, timedelta, timezone

from backend.propagate import propagate_object

# Real ISS TLE (hardcoded so this test needs no network/DB), LEO ~400 km.
ISS_LINE1 = "1 25544U 98067A   24001.50000000  .00016717  00000-0  10270-3 0  9994"
ISS_LINE2 = "2 25544  51.6416 339.5943 0007270  92.8340  45.6046 15.49560725 25544"


def test_propagate_object_returns_expected_shape():
    times = [datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=m) for m in (0, 30, 60)]
    positions = propagate_object(ISS_LINE1, ISS_LINE2, times)
    assert positions.shape == (3, 3)


def test_propagate_object_altitude_is_plausible_leo():
    import numpy as np

    times = [datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=m) for m in (0, 30, 60)]
    positions = propagate_object(ISS_LINE1, ISS_LINE2, times)
    earth_radius_km = 6371.0
    for pos in positions:
        radius_km = float(np.linalg.norm(pos))
        altitude_km = radius_km - earth_radius_km
        assert 300 <= altitude_km <= 2000


def test_state_functions_match_grid_frame_and_velocity():
    """make_state_functions must return the same GCRS positions as the
    propagation grid (propagate_object) and a consistent velocity."""
    import numpy as np

    from backend.propagate import make_state_functions

    obj = {"norad_id": "25544", "name": "ISS", "tle_line1": ISS_LINE1, "tle_line2": ISS_LINE2}
    state = make_state_functions([obj])["25544"]
    t = datetime(2024, 1, 1, 0, 30, tzinfo=timezone.utc)

    pos, vel = state(t)
    grid_pos = propagate_object(ISS_LINE1, ISS_LINE2, [t])[0]
    assert np.allclose(pos, grid_pos, atol=1e-9)
    assert 7.0 < np.linalg.norm(vel) < 8.0  # LEO orbital speed, km/s

    # Velocity agrees with a central difference of positions (+/-0.5 s).
    p_plus, _ = state(t + timedelta(seconds=0.5))
    p_minus, _ = state(t - timedelta(seconds=0.5))
    assert np.allclose(vel, p_plus - p_minus, atol=1e-4)
