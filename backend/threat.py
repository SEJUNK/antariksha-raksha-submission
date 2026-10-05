"""Proximity-pattern classification (blueprint Section 6.5).

Purpose: flag sustained proximity patterns near protected assets by other
active satellites (Group C) -- e.g. a long co-orbital dwell -- as
proximity_watch events, distinct from debris collision risk.

Ethics/framing note (also in README): a proximity_watch flag is an anomaly
worth human review, not an accusation of hostile intent. Objects are selected
by orbital similarity, and brief language stays factual (Section 8 prompt
template enforces this) -- distances and durations only, never intent.
"""

import numpy as np

from backend.config import PROXIMITY_DWELL_MIN_STEPS, PROXIMITY_WATCH_KM
from backend.db import list_objects


def _geometry_from_range_rate(pos_a, pos_b, step_seconds, mid_idx):
    """closing / station-keeping / receding from the sign of range-rate at
    the window midpoint."""
    if mid_idx <= 0 or mid_idx >= len(pos_a) - 1:
        mid_idx = len(pos_a) // 2
    dist_before = np.linalg.norm(pos_a[mid_idx - 1] - pos_b[mid_idx - 1])
    dist_after = np.linalg.norm(pos_a[mid_idx + 1] - pos_b[mid_idx + 1])
    rate = (dist_after - dist_before) / (2 * step_seconds)
    if rate < -1e-6:
        return "closing"
    if rate > 1e-6:
        return "receding"
    return "station-keeping"


def dwell_minutes_for_run(n_samples, step_seconds):
    """Dwell = elapsed physical time between the first and last consecutive
    in-watch grid samples, i.e. (N - 1) * step. One sample is an instant
    (0 min); two consecutive samples span one step. (Counting N * step would
    overstate the measured dwell by one step: the object is only *observed*
    inside the watch radius from the first to the last sample.)"""
    if n_samples <= 1:
        return 0.0
    return (n_samples - 1) * step_seconds / 60.0


def detect_proximity_operations(times, positions_grid, step_seconds=None, objects=None):
    """For each (Indian asset, Group C satellite) pair, count consecutive
    time steps where separation < PROXIMITY_WATCH_KM. If the longest such
    dwell run >= PROXIMITY_DWELL_MIN_STEPS, emit a proximity_watch event.

    Two distinct quantities (documented rule: "within 25 km for at least 10
    consecutive steps"):
      - detection gate: the run must contain >= PROXIMITY_DWELL_MIN_STEPS
        consecutive in-watch grid *samples* (sample count, unchanged);
      - reported dwell_minutes: elapsed time spanned by that run,
        (N - 1) * step (dwell_minutes_for_run). At a 60 s step the minimum
        qualifying run of 10 samples therefore reports 9.0 minutes.

    Debris and Indian-Indian pairs are excluded -- this module is only about
    active foreign objects.

    Returns list[dict] with object_a_id, object_b_id, tca_timestamp (grid
    time of minimum separation within the dwell segment -- the same instant
    as min_distance_km), min_distance_km, dwell_minutes, geometry (range-rate
    sign at the dwell midpoint). `objects` defaults to list_objects().
    """
    if step_seconds is None:
        step_seconds = (times[1] - times[0]).total_seconds() if len(times) > 1 else 60

    if objects is None:
        objects = list_objects()
    objects_by_id = {o["norad_id"]: o for o in objects}
    indian_ids = [nid for nid, o in objects_by_id.items() if o["object_type"] == "satellite" and nid in positions_grid]
    foreign_ids = [nid for nid, o in objects_by_id.items() if o["object_type"] == "foreign_sat" and nid in positions_grid]

    events = []
    for a_id in indian_ids:
        pos_a = positions_grid[a_id]
        for c_id in foreign_ids:
            pos_b = positions_grid[c_id]
            n = min(len(pos_a), len(pos_b))
            if n < 3:
                continue
            dist = np.linalg.norm(pos_a[:n] - pos_b[:n], axis=1)
            within = dist < PROXIMITY_WATCH_KM

            best_run_len = 0
            best_run_start = 0
            run_len = 0
            run_start = 0
            for i, flag in enumerate(within):
                if flag:
                    if run_len == 0:
                        run_start = i
                    run_len += 1
                    if run_len > best_run_len:
                        best_run_len = run_len
                        best_run_start = run_start
                else:
                    run_len = 0

            if best_run_len < PROXIMITY_DWELL_MIN_STEPS:
                continue

            run_end = best_run_start + best_run_len
            segment = dist[best_run_start:run_end]
            min_idx_in_segment = int(np.argmin(segment))
            min_dist = float(segment[min_idx_in_segment])
            min_idx = best_run_start + min_idx_in_segment
            mid_idx = best_run_start + best_run_len // 2
            dwell_minutes = dwell_minutes_for_run(best_run_len, step_seconds)
            geometry = _geometry_from_range_rate(pos_a[:n], pos_b[:n], step_seconds, mid_idx)

            events.append({
                "object_a_id": a_id,
                "object_b_id": c_id,
                "tca_timestamp": times[min_idx].isoformat(),
                "min_distance_km": min_dist,
                "dwell_minutes": dwell_minutes,
                "geometry": geometry,
            })

    return events


def risk_tier_for_proximity(dwell_minutes, min_distance_km):
    """High if the elapsed dwell exceeds 60 min and the minimum separation is
    under 10 km, else Medium. dwell_minutes is elapsed time (see
    dwell_minutes_for_run), so High needs > 60 min actually observed in the
    watch radius -- at a 60 s step that is at least 62 consecutive samples."""
    if dwell_minutes > 60 and min_distance_km < 10:
        return "High"
    return "Medium"


if __name__ == "__main__":
    from backend.propagate import propagate_all

    times, positions = propagate_all()
    events = detect_proximity_operations(times, positions)
    if not events:
        print("No proximity_watch events found in real data. Use "
              "`python -m scripts.seed_demo_event --proximity` to demo this module.")
    else:
        for e in events:
            print(f"{e['object_a_id']} <-> {e['object_b_id']}: "
                  f"min_dist={e['min_distance_km']:.2f} km, dwell={e['dwell_minutes']:.1f} min, "
                  f"geometry={e['geometry']}")
