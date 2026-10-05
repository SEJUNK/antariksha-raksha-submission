"""72 h screening-window boundary (backend/propagate.py::_time_grid)."""

import math
from datetime import datetime, timedelta, timezone

from sgp4.api import WGS72, Satrec, jday
from sgp4.exporter import export_tle

from backend.config import PROPAGATION_STEP_SECONDS, PROPAGATION_WINDOW_HOURS
from backend.propagate import _time_grid, propagate_all

START = datetime(2026, 10, 3, 6, 0, 0, tzinfo=timezone.utc)


def test_configured_horizon_unchanged():
    assert PROPAGATION_WINDOW_HOURS == 72
    assert PROPAGATION_STEP_SECONDS == 60


def test_grid_is_inclusive_of_start_and_horizon():
    times = _time_grid(PROPAGATION_WINDOW_HOURS, PROPAGATION_STEP_SECONDS, start=START)
    horizon = START + timedelta(hours=PROPAGATION_WINDOW_HOURS)
    assert len(times) == 72 * 60 + 1 == 4321
    assert times[0] == START
    assert times[-1] == horizon
    assert all(t <= horizon for t in times)
    assert len(set(times)) == len(times)
    assert all((b - a) == timedelta(seconds=PROPAGATION_STEP_SECONDS) for a, b in zip(times, times[1:]))


def test_partial_final_step_never_exceeds_horizon():
    # 1 h at 7 min: 8 whole steps (56 min); no sample past the 60 min horizon.
    times = _time_grid(1, 420, start=START)
    assert len(times) == 9
    assert times[-1] == START + timedelta(minutes=56)


def test_default_start_is_now():
    before = datetime.now(timezone.utc)
    times = _time_grid(1, 60)
    after = datetime.now(timezone.utc)
    assert before <= times[0] <= after
    assert times[-1] - times[0] == timedelta(hours=1)


def _fresh_tle(satnum):
    now = datetime.now(timezone.utc)
    jd, fr = jday(now.year, now.month, now.day, now.hour, now.minute, now.second)
    sat = Satrec()
    sat.sgp4init(WGS72, "i", satnum, (jd + fr) - 2433281.5, 0.0001, 0.0, 0.0, 0.001,
                 math.radians(20), math.radians(51.6), math.radians(30), 15.5 * 2 * math.pi / 1440.0,
                 math.radians(10))
    return export_tle(sat)


def test_propagate_all_positions_cover_full_inclusive_window():
    l1, l2 = _fresh_tle(90011)
    obj = {"norad_id": "90011", "name": "T", "tle_line1": l1, "tle_line2": l2}
    start = datetime.now(timezone.utc).replace(microsecond=0)
    times, positions = propagate_all(objects=[obj], start=start)
    assert len(times) == 4321
    assert times[-1] == start + timedelta(hours=72)
    assert positions["90011"].shape == (4321, 3)
