"""Persistent event observations, event evolution and refresh deltas.

conjunction_events is rebuilt on every screening run. This module keeps one
event_observations row per event per recorded screening run (numeric values
only, never AI text) so the UI can show how an event changed across runs and
what changed between the latest run and the one before it.

Correlation rule ``pair_class_nearest_tca_v1`` (prototype)
-----------------------------------------------------------
Event correlation is a prototype matching rule, not an authoritative
operational event identity.

* Baseline: the most recent earlier screening run of the SAME domain whose
  observations were recorded. Domains: "real" = live/cached runs, "demo" =
  demo runs. Demo and real observations never correlate, and failed or
  pre-feature runs (observations_recorded NULL) are never a baseline.
* Candidates: same unordered object pair, same event_class, and
  |TCA difference| within the class window:
    - collision_risk: 20 min. Two crossing LEO orbits can produce encounters
      at either orbital node, roughly half an orbital period (~45-50 min)
      apart, so the window must stay below ~23 min for nearest-TCA matching to
      be unambiguous; TCA drift between TLE updates is normally seconds.
    - proximity_watch: 6 h. Screening emits at most one proximity event per
      pair per run, and its TCA (grid minimum inside a long, flat co-orbital
      dwell) can move a lot inside the dwell segment.
* Assignment: global greedy one-to-one by smallest |TCA difference|, ties
  broken by lower previous observation id, then lower new event id.
  ``ambiguous_match`` = 1 when the new event or its matched previous
  observation had more than one candidate within the window.
* A matched observation continues the previous observation's track; an
  unmatched one starts a new track whose track_id is its own observation id.

Trends compare the two most recent observations of a track, from stored
values only. Miss distance is "stable" when |change| <= max(0.05 km, 5% of
the previous value): 50 m is far below TLE/SGP4 position accuracy, and the
relative term keeps proximity distances (up to 25 km) from flipping on noise.
"""

import json
from datetime import datetime, timezone

from backend.db import get_connection, list_objects

CORRELATION_RULE = "pair_class_nearest_tca_v1"
CORRELATION_WINDOW_SECONDS = {
    "collision_risk": 20 * 60,
    "proximity_watch": 6 * 3600,
}
MISS_STABLE_THRESHOLD_KM = 0.05
MISS_STABLE_THRESHOLD_REL = 0.05
TIER_ORDER = {"Low": 0, "Medium": 1, "High": 2, "Critical": 3}
DOMAINS = ("real", "demo")


def domain_for_mode(mode):
    return "demo" if mode == "demo" else "real"


def _modes_for_domain(domain):
    return ("demo",) if domain == "demo" else ("live", "cached")


def pair_key(object_a_id, object_b_id):
    a, b = sorted((str(object_a_id), str(object_b_id)))
    return f"{a}|{b}"


def _parse_ts(value):
    ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


# --- Correlation (pure) -------------------------------------------------------

def correlate(previous, new):
    """Match new events to previous observations (see module docstring).

    previous: list of dicts with id, pair_key, event_class, tca_timestamp.
    new: list of dicts with event_id, pair_key, event_class, tca_timestamp.
    Returns a list parallel to `new` of (previous_id or None, ambiguous bool).
    Deterministic for identical inputs."""
    edges = []
    new_counts = [0] * len(new)
    prev_counts = {}
    prev_by_key = {}
    for p in previous:
        prev_by_key.setdefault((p["pair_key"], p["event_class"]), []).append(p)
    for i, n in enumerate(new):
        window = CORRELATION_WINDOW_SECONDS.get(n["event_class"], 0)
        n_tca = _parse_ts(n["tca_timestamp"])
        for p in prev_by_key.get((n["pair_key"], n["event_class"]), []):
            delta = abs((n_tca - _parse_ts(p["tca_timestamp"])).total_seconds())
            if delta <= window:
                edges.append((delta, p["id"], n.get("event_id") or 0, i))
                new_counts[i] += 1
                prev_counts[p["id"]] = prev_counts.get(p["id"], 0) + 1

    edges.sort(key=lambda e: (e[0], e[1], e[2], e[3]))
    result = [(None, False)] * len(new)
    used_prev, used_new = set(), set()
    for _delta, prev_id, _event_id, i in edges:
        if i in used_new or prev_id in used_prev:
            continue
        used_new.add(i)
        used_prev.add(prev_id)
        result[i] = (prev_id, new_counts[i] > 1 or prev_counts[prev_id] > 1)
    return result


# --- Recording ----------------------------------------------------------------

def _baseline_run(conn, run_id, mode):
    modes = _modes_for_domain(domain_for_mode(mode))
    row = conn.execute(
        f"SELECT id FROM screening_runs WHERE id < ? AND observations_recorded = 1 "
        f"AND mode IN ({', '.join('?' * len(modes))}) ORDER BY id DESC LIMIT 1",
        (run_id, *modes),
    ).fetchone()
    return row["id"] if row else None


def latest_demo_scenario():
    """demo_scenario_json (parsed) of the most recent demo screening run, or
    None. Used by the demo history (step 2 requires step 1 to be latest)."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT demo_scenario_json FROM screening_runs WHERE mode = 'demo' "
                           "ORDER BY id DESC LIMIT 1").fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return _json_or_none(row["demo_scenario_json"])


def _json_or_none(raw):
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def demo_history_of(demo_scenario_json):
    """{id, step, of} of a controlled demo history run, else None."""
    history = (_json_or_none(demo_scenario_json) or {}).get("history")
    if not isinstance(history, dict):
        return None
    return {"id": history.get("id"), "step": history.get("step"), "of": history.get("of")}


def record_run_observations(run_id, mode, observations, reset_baseline=False):
    """Correlate and store one screening run's observations in a single
    transaction, then mark the run recorded with its correlation baseline.

    observations: dicts with event_id, object_a_id, object_b_id, event_class,
    tca_timestamp, miss_distance_km, rel_velocity_km_s, pc_score, risk_tier,
    priority_score, is_demo and optional dwell_minutes. Returns the baseline
    run id (or None). reset_baseline=True: no baseline, every observation
    starts a new track (demo rehearsal reset; the rule itself is unchanged)."""
    conn = get_connection()
    try:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            baseline_id = None if reset_baseline else _baseline_run(conn, run_id, mode)
            previous = []
            if baseline_id is not None:
                previous = [dict(r) for r in conn.execute(
                    "SELECT id, track_id, pair_key, event_class, tca_timestamp "
                    "FROM event_observations WHERE screening_run_id = ? ORDER BY id",
                    (baseline_id,),
                ).fetchall()]
            track_of = {p["id"]: p["track_id"] for p in previous}
            new = [{**o, "pair_key": pair_key(o["object_a_id"], o["object_b_id"])} for o in observations]
            matches = correlate(previous, new)
            now = _now_iso()
            for obs, (prev_id, ambiguous) in zip(new, matches):
                cur = conn.execute(
                    """
                    INSERT INTO event_observations
                        (screening_run_id, event_id, track_id, prev_observation_id, ambiguous_match,
                         object_a_id, object_b_id, pair_key, event_class, tca_timestamp,
                         miss_distance_km, rel_velocity_km_s, pc_score, risk_tier, priority_score,
                         dwell_minutes, is_demo, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (run_id, obs.get("event_id"), track_of.get(prev_id), prev_id, int(bool(ambiguous)),
                     obs["object_a_id"], obs["object_b_id"], obs["pair_key"], obs["event_class"],
                     obs["tca_timestamp"], obs["miss_distance_km"], obs.get("rel_velocity_km_s"),
                     obs["pc_score"], obs["risk_tier"], obs["priority_score"],
                     obs.get("dwell_minutes"), int(bool(obs.get("is_demo"))), now),
                )
                if prev_id is None or track_of.get(prev_id) is None:
                    conn.execute("UPDATE event_observations SET track_id = id WHERE id = ?", (cur.lastrowid,))
            conn.execute(
                "UPDATE screening_runs SET observations_recorded = 1, correlation_baseline_run_id = ? "
                "WHERE id = ?",
                (baseline_id, run_id),
            )
        return baseline_id
    finally:
        conn.close()


# --- Trends -------------------------------------------------------------------

def miss_distance_trend(previous_km, current_km):
    if previous_km is None or current_km is None:
        return None
    threshold = max(MISS_STABLE_THRESHOLD_KM, MISS_STABLE_THRESHOLD_REL * abs(previous_km))
    change = current_km - previous_km
    if abs(change) <= threshold:
        return "stable"
    return "increasing" if change > 0 else "decreasing"


def risk_tier_trend(previous_tier, current_tier):
    if previous_tier not in TIER_ORDER or current_tier not in TIER_ORDER:
        return None
    diff = TIER_ORDER[current_tier] - TIER_ORDER[previous_tier]
    if diff == 0:
        return "unchanged"
    return "increased" if diff > 0 else "decreased"


def tca_shift_seconds(previous_tca, current_tca):
    if previous_tca is None or current_tca is None:
        return None
    return round((_parse_ts(current_tca) - _parse_ts(previous_tca)).total_seconds(), 3)


def pc_ratio(previous_pc, current_pc):
    """current / previous collision indicator, numeric only (no direction
    label: Pc drifts every run as the lead-time sigma shrinks). None when not
    computable (missing values, or a previous Pc of 0, e.g. proximity)."""
    if previous_pc is None or current_pc is None or previous_pc <= 0:
        return None
    return current_pc / previous_pc


def _trends(previous, current):
    """previous/current: observation-like dicts or None."""
    if previous is None or current is None:
        mt = rt = shift = ratio = None
    else:
        mt = miss_distance_trend(previous["miss_distance_km"], current["miss_distance_km"])
        rt = risk_tier_trend(previous["risk_tier"], current["risk_tier"])
        shift = tca_shift_seconds(previous["tca_timestamp"], current["tca_timestamp"])
        ratio = pc_ratio(previous["pc_score"], current["pc_score"])
    return {
        "miss_distance": mt,
        "risk_tier": rt,
        "tca_shift_seconds": shift,
        "pc_ratio": ratio,
        "miss_stable_threshold_km": MISS_STABLE_THRESHOLD_KM,
        "miss_stable_threshold_rel": MISS_STABLE_THRESHOLD_REL,
    }


# --- Read helpers -------------------------------------------------------------

def _current_event_ids(conn):
    return {r["id"] for r in conn.execute("SELECT id FROM conjunction_events").fetchall()}


def observation_for_event(event_id):
    """Most recent stored observation for a conjunction_events id, or None."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM event_observations WHERE event_id = ? ORDER BY id DESC LIMIT 1", (event_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _observation_payload(row, current_ids):
    return {
        "observation_id": row["id"],
        "screening_run_id": row["screening_run_id"],
        "run_started_at": row.get("run_started_at"),
        "run_mode": row.get("run_mode"),
        "event_class": row["event_class"],
        "is_demo": bool(row["is_demo"]),
        "tca_timestamp": row["tca_timestamp"],
        "miss_distance_km": row["miss_distance_km"],
        "rel_velocity_km_s": row["rel_velocity_km_s"],
        "pc_score": row["pc_score"],
        "risk_tier": row["risk_tier"],
        "priority_score": row["priority_score"],
        "dwell_minutes": row.get("dwell_minutes"),
        "is_current": row.get("event_id") is not None and row["event_id"] in current_ids,
        "ambiguous_match": bool(row["ambiguous_match"]),
        "demo_history": demo_history_of(row.get("run_demo_scenario_json")),
    }


def get_event_evolution(event_id):
    """Evolution of one event across recorded screening runs, or None if the
    id matches neither a current event nor a stored observation."""
    conn = get_connection()
    try:
        current_ids = _current_event_ids(conn)
        obs = conn.execute(
            "SELECT * FROM event_observations WHERE event_id = ? ORDER BY id DESC LIMIT 1", (event_id,),
        ).fetchone()
        if obs is None:
            event = conn.execute("SELECT * FROM conjunction_events WHERE id = ?", (event_id,)).fetchone()
            if event is None:
                return None
            # Event from a run before this feature, or the current run's
            # observations are not recorded yet (briefs still generating).
            event = dict(event)
            run = conn.execute("SELECT started_at, mode, demo_scenario_json FROM screening_runs WHERE id = ?",
                               (event.get("screening_run_id"),)).fetchone()
            synthetic = {
                "observation_id": None,
                "screening_run_id": event.get("screening_run_id"),
                "run_started_at": run["started_at"] if run else None,
                "run_mode": run["mode"] if run else None,
                "event_class": event["event_class"],
                "is_demo": bool(event.get("is_demo")),
                "tca_timestamp": event["tca_timestamp"],
                "miss_distance_km": event["miss_distance_km"],
                "rel_velocity_km_s": event.get("rel_velocity_km_s"),
                "pc_score": event["pc_score"],
                "risk_tier": event["risk_tier"],
                "priority_score": event["priority_score"],
                "dwell_minutes": None,
                "is_current": True,
                "ambiguous_match": False,
                "demo_history": demo_history_of(run["demo_scenario_json"]) if run else None,
            }
            return {
                "event_id": event_id,
                "track_id": None,
                "is_demo": bool(event.get("is_demo")),
                "history_available": False,
                "correlation_rule": CORRELATION_RULE,
                "correlation_window_seconds": CORRELATION_WINDOW_SECONDS.get(event["event_class"]),
                "demo_history": synthetic["demo_history"],
                "observations": [synthetic],
                "trends": _trends(None, None),
            }

        obs = dict(obs)
        rows = [dict(r) for r in conn.execute(
            """
            SELECT o.*, r.started_at AS run_started_at, r.mode AS run_mode,
                   r.demo_scenario_json AS run_demo_scenario_json
            FROM event_observations o LEFT JOIN screening_runs r ON r.id = o.screening_run_id
            WHERE o.track_id = ? ORDER BY o.screening_run_id ASC, o.id ASC
            """,
            (obs["track_id"],),
        ).fetchall()]
        trends = _trends(rows[-2], rows[-1]) if len(rows) >= 2 else _trends(None, None)
        payloads = [_observation_payload(r, current_ids) for r in rows]
        return {
            "event_id": event_id,
            "track_id": obs["track_id"],
            "is_demo": bool(obs["is_demo"]),
            "history_available": True,
            "correlation_rule": CORRELATION_RULE,
            "correlation_window_seconds": CORRELATION_WINDOW_SECONDS.get(obs["event_class"]),
            # Controlled demo history of the latest observation ({id, step, of})
            # or None; per-observation values are in observations[].demo_history.
            "demo_history": payloads[-1]["demo_history"],
            "observations": payloads,
            "trends": trends,
        }
    finally:
        conn.close()


# --- Refresh delta ------------------------------------------------------------

_DELTA_BUCKETS = ("new", "increased", "decreased", "unchanged", "not_present")


def _run_ref(run):
    return {"id": run["id"], "started_at": run["started_at"], "mode": run["mode"]} if run else None


def _snapshot(obs):
    if obs is None:
        return None
    return {
        "tca_timestamp": obs["tca_timestamp"],
        "miss_distance_km": obs["miss_distance_km"],
        "rel_velocity_km_s": obs["rel_velocity_km_s"],
        "risk_tier": obs["risk_tier"],
        "pc_score": obs["pc_score"],
        "priority_score": obs["priority_score"],
    }


def compute_delta(domain=None, run_id=None):
    """Delta between a recorded screening run (default: the latest recorded
    run of `domain`; `domain` defaults to the latest recorded run's domain)
    and the run it was correlated against (same domain)."""
    conn = get_connection()
    try:
        if run_id is not None:
            latest = conn.execute("SELECT * FROM screening_runs WHERE id = ? AND observations_recorded = 1",
                                  (run_id,)).fetchone()
        elif domain is None:
            latest = conn.execute("SELECT * FROM screening_runs WHERE observations_recorded = 1 "
                                  "ORDER BY id DESC LIMIT 1").fetchone()
        else:
            modes = _modes_for_domain(domain)
            latest = conn.execute(
                f"SELECT * FROM screening_runs WHERE observations_recorded = 1 "
                f"AND mode IN ({', '.join('?' * len(modes))}) ORDER BY id DESC LIMIT 1",
                modes,
            ).fetchone()
        empty_counts = {b: 0 for b in _DELTA_BUCKETS}
        empty_items = {b: [] for b in _DELTA_BUCKETS}
        if latest is None:
            return {"available": False, "reason": "no_runs", "domain": domain,
                    "latest_run": None, "previous_run": None,
                    "counts": empty_counts, "items": empty_items}
        latest = dict(latest)
        domain = domain_for_mode(latest["mode"])
        baseline_id = latest.get("correlation_baseline_run_id")
        previous = None
        if baseline_id is not None:
            row = conn.execute("SELECT * FROM screening_runs WHERE id = ?", (baseline_id,)).fetchone()
            previous = dict(row) if row else None
        if previous is None:
            return {"available": False, "reason": "no_previous_run", "domain": domain,
                    "latest_run": _run_ref(latest), "previous_run": None,
                    "counts": empty_counts, "items": empty_items}

        current_ids = _current_event_ids(conn)
        latest_obs = [dict(r) for r in conn.execute(
            "SELECT * FROM event_observations WHERE screening_run_id = ? ORDER BY id", (latest["id"],)
        ).fetchall()]
        prev_obs = {r["id"]: dict(r) for r in conn.execute(
            "SELECT * FROM event_observations WHERE screening_run_id = ? ORDER BY id", (previous["id"],)
        ).fetchall()}
        # Display metadata only: runs numbered between the two compared runs
        # (other-domain runs, e.g. DEMO between two LIVE runs, or runs whose
        # observations were not recorded). They are not part of this delta.
        intermediate = [
            {"id": r["id"], "mode": r["mode"], "started_at": r["started_at"],
             "recorded": bool(r["observations_recorded"])}
            for r in conn.execute(
                "SELECT id, mode, started_at, observations_recorded FROM screening_runs "
                "WHERE id > ? AND id < ? ORDER BY id", (previous["id"], latest["id"]),
            ).fetchall()
        ]
    finally:
        conn.close()

    names = {o["norad_id"]: o["name"] for o in list_objects(include_inactive=True)}

    def item(prev, cur):
        ref = cur or prev
        event_id = cur.get("event_id") if cur else None
        return {
            "track_id": ref["track_id"],
            "event_id": event_id if event_id in current_ids else None,
            "event_class": ref["event_class"],
            "is_demo": bool(ref["is_demo"]),
            "object_a_id": ref["object_a_id"],
            "object_b_id": ref["object_b_id"],
            "object_a_name": names.get(ref["object_a_id"], ref["object_a_id"]),
            "object_b_name": names.get(ref["object_b_id"], ref["object_b_id"]),
            "previous": _snapshot(prev),
            "current": _snapshot(cur),
            "miss_distance_trend": miss_distance_trend(prev["miss_distance_km"], cur["miss_distance_km"])
            if prev and cur else None,
            "tca_shift_seconds": tca_shift_seconds(prev["tca_timestamp"], cur["tca_timestamp"])
            if prev and cur else None,
            "pc_ratio": pc_ratio(prev["pc_score"], cur["pc_score"]) if prev and cur else None,
        }

    items = {b: [] for b in _DELTA_BUCKETS}
    matched_prev = set()
    for cur in latest_obs:
        prev = prev_obs.get(cur["prev_observation_id"])
        if prev is None:
            items["new"].append(item(None, cur))
            continue
        matched_prev.add(prev["id"])
        bucket = {"increased": "increased", "decreased": "decreased"}.get(
            risk_tier_trend(prev["risk_tier"], cur["risk_tier"]), "unchanged")
        items[bucket].append(item(prev, cur))
    for pid, prev in prev_obs.items():
        if pid not in matched_prev:
            items["not_present"].append(item(prev, None))

    def sort_key(it):
        snap = it["current"] or it["previous"]
        return (-snap["priority_score"], it["track_id"] or 0)

    for bucket in items.values():
        bucket.sort(key=sort_key)
    return {
        "available": True,
        "reason": None,
        "domain": domain,
        "latest_run": _run_ref(latest),
        "previous_run": _run_ref(previous),
        "intermediate_runs": intermediate,
        "counts": {b: len(v) for b, v in items.items()},
        "items": items,
    }


# --- Risk drivers (provenance `risk` block) -----------------------------------

# Mirrors backend/threat.py::risk_tier_for_proximity (High if dwell > 60 min
# and min distance < 10 km, else Medium); a test keeps the two in sync.
PROXIMITY_TIER_THRESHOLDS = {"high_dwell_min": 60, "high_max_km": 10}


def risk_drivers(event, obj_a):
    """Extra explanation fields for the provenance `risk` block, from stored
    values and config only (no new science). For collision events priority =
    pc x criticality multiplier x 1e6, with criticality defaulting to Tier3
    exactly as the pipeline does. Proximity priority is dwell-based, so no
    criticality multiplier is presented as applying to it."""
    from backend.config import (
        CRITICALITY_MULTIPLIERS,
        RISK_TIER_CRITICAL_PC,
        RISK_TIER_HIGH_PC,
        RISK_TIER_MEDIUM_PC,
    )

    is_collision = event["event_class"] == "collision_risk"
    criticality_effective = (obj_a.get("criticality") if obj_a else None) or "Tier3"
    if is_collision:
        return {
            "event_class": event["event_class"],
            "pc_score": event["pc_score"],
            "priority_basis": "pc_x_criticality",
            "criticality_effective": criticality_effective,
            "criticality_multiplier_effective": CRITICALITY_MULTIPLIERS.get(criticality_effective, 1.0),
            "dwell_minutes": None,
            "tier_thresholds": {"critical": RISK_TIER_CRITICAL_PC, "high": RISK_TIER_HIGH_PC,
                                "medium": RISK_TIER_MEDIUM_PC},
        }
    obs = observation_for_event(event["id"]) if event.get("id") is not None else None
    return {
        "event_class": event["event_class"],
        "pc_score": event["pc_score"],
        "priority_basis": "proximity_dwell",
        "criticality_effective": criticality_effective,
        "criticality_multiplier_effective": None,  # not applied to proximity priority
        "dwell_minutes": obs.get("dwell_minutes") if obs else None,
        "tier_thresholds": dict(PROXIMITY_TIER_THRESHOLDS),
    }


def compact_delta(run_id):
    """Counts-only delta for one just-recorded run (refresh/demo responses)."""
    full = compute_delta(run_id=run_id)
    return {
        "available": full["available"],
        "reason": full["reason"],
        "domain": full["domain"],
        "previous_run_id": full["previous_run"]["id"] if full["previous_run"] else None,
        "counts": full["counts"],
    }
