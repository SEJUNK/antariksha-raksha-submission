"""Pairwise close-approach screening (blueprint Section 6.3)."""

import logging
from datetime import timedelta

import numpy as np
from scipy.optimize import minimize_scalar

from backend.config import SCREENING_THRESHOLD_KM
from backend.db import list_objects

logger = logging.getLogger(__name__)

# Bounded-minimiser tolerance, in grid steps (1e-6 * 60 s = 60 us) -- far
# below any physically meaningful TCA uncertainty for TLE-derived states.
_REFINE_XATOL_STEPS = 1e-6


def _grid_state_fn(times, positions, center_idx, step_seconds):
    """State function built from propagation-grid samples, used when no
    propagator-backed state function is supplied (e.g. unit tests with
    synthetic grids). 3-point quadratic Lagrange interpolation on the fixed
    stencil (center-1, center, center+1): position from the interpolant,
    velocity from its time derivative. Fixing the stencil keeps the
    separation function smooth over the whole +/-1 step refinement interval,
    and is exact for linear or quadratic motion."""
    n = len(positions)
    c = min(max(center_idx, 1), n - 2)
    p0, p1, p2 = positions[c - 1], positions[c], positions[c + 1]
    t_c = times[c]

    def state(when):
        u = (when - t_c).total_seconds() / step_seconds
        pos = (u * (u - 1) / 2) * p0 + (1 - u * u) * p1 + (u * (u + 1) / 2) * p2
        vel = (((2 * u - 1) / 2) * p0 - 2 * u * p1 + ((2 * u + 1) / 2) * p2) / step_seconds
        return np.asarray(pos, dtype=float), np.asarray(vel, dtype=float)

    return state


def refine_tca(t_grid, step_seconds, state_a, state_b, allow_before=True, allow_after=True):
    """Single authoritative TCA refinement: bounded scalar minimisation of
    |r_a(t) - r_b(t)| over t_grid + s*step, s in [-1, 1] (one-sided at the
    window edges). s=0 (the grid minimum) is always evaluated too and the
    better of the two is kept, so the refined miss distance can never exceed
    the grid miss distance.

    Returns (tca_datetime, miss_km, rel_velocity_km_s, pos_a, pos_b,
    rel_velocity_vec), all evaluated at the same refined instant."""
    def at(s):
        return t_grid + timedelta(seconds=float(s) * step_seconds)

    def sep(s):
        pa, _ = state_a(at(s))
        pb, _ = state_b(at(s))
        return float(np.linalg.norm(pa - pb))

    lo = -1.0 if allow_before else 0.0
    hi = 1.0 if allow_after else 0.0

    candidates = [t_grid]
    if hi > lo:
        res = minimize_scalar(sep, bounds=(lo, hi), method="bounded",
                              options={"xatol": _REFINE_XATOL_STEPS})
        candidates.append(at(res.x))

    best = None
    for when in candidates:
        # timedelta has microsecond resolution; states are re-evaluated at
        # the exact datetime that will be reported, so every derived number
        # refers to the same instant.
        pa, va = state_a(when)
        pb, vb = state_b(when)
        miss = float(np.linalg.norm(pa - pb))
        if best is None or miss < best[1]:
            best = (when, miss, float(np.linalg.norm(va - vb)), pa, pb, va - vb)
    return best


# Curvature safety margin added to the coarse candidate filter: the
# |v_rel| * step / 2 pad is exact for straight-line relative motion; relative
# acceleration over half a step (gravity-gradient level for nearby objects,
# well under 0.2 km for 30 s at LEO) and the finite-difference v_rel estimate
# are covered by this margin.
CANDIDATE_MARGIN_KM = 1.0
# Margin for promoting a grid-interpolated miss to the SGP4 refinement: the
# 3-point quadratic interpolation error at a 60 s step is bounded by
# |jerk| h^3 / 27 ~ 0.13 km per object for LEO (jerk ~ a * n ~ 1e-5 km/s^3),
# so ~0.26 km relative; 0.5 km keeps every possibly-qualifying encounter.
INTERP_PROMOTE_MARGIN_KM = 0.5


def _encounter_candidates(dist, rel_speed, threshold_km, step_seconds):
    """Grid indices of candidate encounters for one pair.

    The nearest grid sample to a true closest approach lies within half a
    step of it, so for straight-line relative motion its separation is at
    most miss + |v_rel| * step / 2. Samples are therefore kept while
    dist < threshold + |v_rel| * step / 2 + margin, and each contiguous run
    of kept samples ("close-approach episode") contributes its minimum-
    distance sample as one candidate -- a fast crossing gives one short
    episode per pass; a slow, co-orbital drift gives one long episode instead
    of hundreds of noise-level local minima."""
    keep = dist < threshold_km + rel_speed * step_seconds / 2.0 + CANDIDATE_MARGIN_KM
    if not keep.any():
        return []
    edges = np.flatnonzero(np.diff(np.concatenate(([0], keep.astype(np.int8), [0]))))
    return [start + int(np.argmin(dist[start:end])) for start, end in zip(edges[::2], edges[1::2])]


def find_close_approaches(times, positions_grid, threshold_km=SCREENING_THRESHOLD_KM, step_seconds=None,
                          state_fns=None, objects=None, stats=None):
    """Screen pairs where object A in Indian assets (Group A) against every
    other tracked object. Intentional O(|A| x |others|) scope, not full O(n^2)
    -- we protect Indian assets, we don't do global screening.

    1. Coarse: candidate encounters from the 60 s grid (see
       _encounter_candidates) -- padded by the grid-sampling distance so a
       fast crossing whose true miss is under threshold is never dropped just
       because no grid sample landed within threshold_km.
    2. Prefilter: refine_tca() on a quadratic interpolation of the grid;
       only encounters with interpolated miss < threshold + 0.5 km continue.
    3. Fine: refine_tca() with state_fns[norad_id](datetime) -> (pos_km,
       vel_km_s) when supplied (production: SGP4, see
       propagate.make_state_functions); otherwise the interpolated result is
       final.
    4. The unchanged threshold_km is applied to the REFINED miss distance.

    A pair can yield several encounters in the window (one per qualifying
    close-approach episode); each is returned as its own event.

    `objects` defaults to list_objects(). If `stats` is a dict, it receives
    candidate_pairs, candidate_encounters, interp_refinements,
    sgp4_refinements and encounters.

    Returns list[dict] with object_a_id, object_b_id, tca_timestamp,
    miss_distance_km, rel_velocity_km_s, pos_a_tca_km, pos_b_tca_km,
    grid_miss_distance_km, tca_refinement -- all evaluated at the refined TCA.
    """
    if step_seconds is None:
        step_seconds = (times[1] - times[0]).total_seconds() if len(times) > 1 else 60

    if objects is None:
        objects = list_objects()
    objects_by_id = {o["norad_id"]: o for o in objects}
    group_a_ids = [nid for nid, o in objects_by_id.items() if o["object_type"] == "satellite" and nid in positions_grid]
    other_ids = [nid for nid in positions_grid if nid not in group_a_ids]

    refinement_base = (f"Bounded minimisation of |r_rel| within +/-1 grid step ({step_seconds:g} s) "
                       "of the grid minimum; states from ")

    events = []
    counts = {"candidate_pairs": 0, "candidate_encounters": 0, "interp_refinements": 0, "sgp4_refinements": 0}
    for a_id in group_a_ids:
        pos_a = positions_grid[a_id]
        for b_id in other_ids:
            pos_b = positions_grid[b_id]
            n = min(len(pos_a), len(pos_b), len(times))
            if n < 3:
                continue
            counts["candidate_pairs"] += 1
            rel = pos_a[:n] - pos_b[:n]
            dist = np.linalg.norm(rel, axis=1)
            rel_speed = np.linalg.norm(np.gradient(rel, step_seconds, axis=0), axis=1)
            candidates = _encounter_candidates(dist, rel_speed, threshold_km, step_seconds)
            counts["candidate_encounters"] += len(candidates)

            use_sgp4 = state_fns is not None and a_id in state_fns and b_id in state_fns
            for idx in candidates:
                edges = {"allow_before": idx > 0, "allow_after": idx < n - 1}
                counts["interp_refinements"] += 1
                result = refine_tca(
                    times[idx], step_seconds,
                    _grid_state_fn(times, pos_a[:n], idx, step_seconds),
                    _grid_state_fn(times, pos_b[:n], idx, step_seconds),
                    **edges,
                )
                refinement = refinement_base + "quadratic interpolation of the propagation grid"
                if use_sgp4:
                    if result[1] >= threshold_km + INTERP_PROMOTE_MARGIN_KM:
                        continue
                    counts["sgp4_refinements"] += 1
                    result = refine_tca(times[idx], step_seconds, state_fns[a_id], state_fns[b_id], **edges)
                    refinement = refinement_base + "SGP4 (Skyfield, GCRS)"

                tca, miss, rel_v, pa, pb, rel_v_vec = result
                if miss >= threshold_km:
                    continue
                events.append({
                    "object_a_id": a_id,
                    "object_b_id": b_id,
                    "tca_timestamp": tca.isoformat(),
                    "miss_distance_km": miss,
                    "rel_velocity_km_s": rel_v,
                    "pos_a_tca_km": [float(x) for x in pa],
                    "pos_b_tca_km": [float(x) for x in pb],
                    # Defines the encounter plane for the analytic Pc indicator.
                    "rel_velocity_vec_km_s": [float(x) for x in rel_v_vec],
                    "grid_miss_distance_km": float(dist[idx]),
                    "tca_refinement": refinement,
                })

    if stats is not None:
        stats.update(counts, encounters=len(events))
    return events

if __name__ == "__main__":
    from backend.propagate import make_state_functions, propagate_all

    objs = list_objects()
    times, positions = propagate_all(objects=objs)
    events = find_close_approaches(times, positions, state_fns=make_state_functions(objs, positions.keys()),
                                   objects=objs)
    if not events:
        print("No close approaches found in this window. If a demo event is needed, "
              "run `python -m scripts.seed_demo_event` rather than inflating the threshold.")
    else:
        for e in events:
            print(f"{e['object_a_id']} <-> {e['object_b_id']}: "
                  f"miss={e['miss_distance_km']:.3f} km at {e['tca_timestamp']}, "
                  f"rel_v={e['rel_velocity_km_s']:.3f} km/s")
