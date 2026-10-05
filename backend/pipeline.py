"""Shared screening pipeline: propagate -> conjunction + threat -> risk -> briefs.

Split out from main.py so both `/api/refresh` (ingest + screening) and
backend/demo_seed.py (screening only, with a demo TLE override applied) can
trigger scoring through the same code path.
"""

import json
import logging
import threading
import time
from collections import Counter
from datetime import datetime, timezone

from backend import config
from backend.brief_agent import generate_and_review_brief
from backend.config import (
    PROPAGATION_STEP_SECONDS,
    PROXIMITY_WATCH_KM,
    SCREENING_THRESHOLD_KM,
)
from backend.conjunction import find_close_approaches
from backend.event_history import compact_delta, record_run_observations
from backend.db import (
    clear_demo_overrides,
    clear_events,
    init_db,
    insert_brief,
    insert_event,
    insert_governance_audit,
    insert_refresh_attempt,
    insert_screening_run,
    latest_ingest_run,
    latest_screening_run,
    latest_successful_refresh_attempt,
    list_objects,
    list_objects_with_demo_overrides,
    update_refresh_attempt,
    update_screening_run,
)
from backend.ingest import IngestFailedError, IngestSafetyError, run_ingest
from backend.maneuver import maneuver_for_event
from backend.notify import notify_run
from backend.propagate import make_state_functions, propagate_all
from backend.provenance import DATA_SOURCE, catalog_age_stats
from backend.risk_score import priority_score, proximity_priority_score, score_collision_event
from backend.settings import get_screening_horizon_hours
from backend.threat import detect_proximity_operations, risk_tier_for_proximity


# Screening clears + repopulates the events tables; two overlapping runs
# (e.g. /api/refresh racing /api/demo/seed from the UI) would delete each
# other's in-flight events mid-brief and break the mission_briefs FK. The
# pipeline is not reentrant -- serialize it.
#
# The lock covers each *whole* sequence, not just screening: refresh holds
# it across ingest -> clear demo overrides -> screening -> run record, and
# demo seeding (backend/demo_seed.py) holds it across object selection ->
# set demo override -> demo screening -> event match. Otherwise a refresh
# could clear a just-written demo override before the demo screening ran
# (the demo then silently produces nothing), or a demo could interleave
# with a refresh. Reentrant so the outer holder can still call
# run_screening_and_briefs(), which takes it again on the same thread.
PIPELINE_LOCK = threading.RLock()
_PIPELINE_LOCK = PIPELINE_LOCK  # backwards-compatible name

logger = logging.getLogger(__name__)

def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def run_screening_and_briefs(is_demo=False, demo_scenario=None, reset_baseline=False):
    """propagate -> conjunction + threat -> risk -> briefs. Clears and
    repopulates conjunction_events/mission_briefs and records one
    screening_runs row.

    is_demo=True (backend/demo_seed.py) screens the real catalog with the
    active demo_overrides applied and tags every event as demo. The default
    (/api/refresh) clears any demo overrides first and screens only the real
    ingested catalog, so a demo adjustment can never leak into a real run.

    reset_baseline=True records this run without a correlation baseline, so
    every event starts a fresh track (used by demo seeds so each rehearsal
    starts its own demo track; the correlation rule itself is unchanged)."""
    with PIPELINE_LOCK:
        return _run_screening_and_briefs_locked(is_demo, demo_scenario, reset_baseline)


def _run_screening_and_briefs_locked(is_demo=False, demo_scenario=None, reset_baseline=False):
    started_at = _now_iso()
    if is_demo:
        objects = list_objects_with_demo_overrides()
    else:
        clear_demo_overrides()
        objects = list_objects()
    objects_by_id = {o["norad_id"]: o for o in objects}

    last_ingest = latest_ingest_run()
    if is_demo:
        mode = "demo"
    elif last_ingest is None or last_ingest["used_cache"]:
        # No recorded network ingest -> never claimed as live.
        mode = "cached"
    else:
        mode = "live"

    # Active admin-configured horizon, read once at run start (live and demo
    # runs alike) unless the enclosing refresh already fixed it; recorded in
    # screening_runs.window_hours below so the run keeps its own value.
    ctx = _current_refresh_context()
    window_hours = (ctx.get("horizon_hours") if ctx else None) or get_screening_horizon_hours()
    trigger = None if is_demo else (ctx.get("trigger") if ctx else None)

    t0 = time.monotonic()
    times, positions_grid = propagate_all(window_hours=window_hours, objects=objects)
    state_fns = make_state_functions(objects, ids=positions_grid.keys())
    t_propagated = time.monotonic()

    clear_events()

    screen_stats = {}
    collision_raw = find_close_approaches(times, positions_grid, state_fns=state_fns,
                                          objects=objects, stats=screen_stats)
    proximity_raw = detect_proximity_operations(times, positions_grid, objects=objects)
    screen_stats["propagation_seconds"] = round(t_propagated - t0, 3)
    screen_stats["screening_seconds"] = round(time.monotonic() - t_propagated, 3)
    logger.info("Screening stats: %s", screen_stats)

    age_stats = catalog_age_stats(objects)
    run_id = insert_screening_run(
        started_at, mode,
        data_source=DATA_SOURCE,
        ingest_run_id=last_ingest["id"] if last_ingest else None,
        object_count=len(objects),
        objects_screened=len(positions_grid),
        objects_skipped_stale=age_stats["excluded_stale"],
        counts_by_type_json=json.dumps(dict(Counter(o["object_type"] for o in objects))),
        tle_age_min_days=age_stats["min"],
        tle_age_max_days=age_stats["max"],
        tle_age_avg_days=age_stats["avg"],
        window_hours=window_hours,
        step_seconds=PROPAGATION_STEP_SECONDS,
        screening_threshold_km=SCREENING_THRESHOLD_KM,
        proximity_watch_km=PROXIMITY_WATCH_KM,
        candidate_pairs=screen_stats.get("candidate_pairs", 0),
        demo_scenario_json=json.dumps(demo_scenario) if (is_demo and demo_scenario) else None,
        trigger=trigger,
    )

    n_collision, n_proximity = 0, 0
    # Numeric per-event values for event_observations (backend/event_history.py),
    # recorded in one transaction after both loops; no AI text.
    observations = []
    for evt in collision_raw:
        evt["event_class"] = "collision_risk"
        scored = score_collision_event(evt)
        pc = scored["pc_score"]
        risk_tier = scored["risk_tier"]
        obj_a = objects_by_id.get(evt["object_a_id"], {})
        obj_b = objects_by_id.get(evt["object_b_id"], {})
        criticality = obj_a.get("criticality") or "Tier3"
        priority = priority_score(pc, criticality)
        maneuver = maneuver_for_event("collision_risk", evt["rel_velocity_km_s"])

        event_id = insert_event(
            object_a_id=evt["object_a_id"],
            object_b_id=evt["object_b_id"],
            event_class="collision_risk",
            tca_timestamp=evt["tca_timestamp"],
            miss_distance_km=evt["miss_distance_km"],
            rel_velocity_km_s=evt["rel_velocity_km_s"],
            pc_score=pc,
            risk_tier=risk_tier,
            priority_score=priority,
            is_demo=is_demo,
            screening_run_id=run_id,
            pc_samples=None,  # analytic indicator: no sampling
            pc_sigma_km=scored["sigma_km"],
            pc_hard_body_radius_km=scored["hard_body_radius_km"],
            tca_refinement=evt.get("tca_refinement"),
            pc_method=scored["method"],
        )
        observations.append({
            "event_id": event_id, "object_a_id": evt["object_a_id"], "object_b_id": evt["object_b_id"],
            "event_class": "collision_risk", "tca_timestamp": evt["tca_timestamp"],
            "miss_distance_km": evt["miss_distance_km"], "rel_velocity_km_s": evt["rel_velocity_km_s"],
            "pc_score": pc, "risk_tier": risk_tier, "priority_score": priority,
            "dwell_minutes": None, "is_demo": is_demo,
        })
        brief = generate_and_review_brief(
            evt, pc, risk_tier, maneuver["delta_v_m_s"],
            object_a_name=obj_a.get("name", evt["object_a_id"]),
            object_b_name=obj_b.get("name", evt["object_b_id"]),
            object_b_type=obj_b.get("object_type", "debris"),
            criticality=criticality,
        )
        insert_brief(
            event_id=event_id,
            brief_text=brief["brief_text"],
            maneuver_text=brief["maneuver_text"],
            delta_v_ms=maneuver["delta_v_m_s"],
            generated_by=brief["generated_by"],
            reviewer_notes=brief.get("reviewer_notes"),
            review_status=brief.get("review_status"),
        )
        n_collision += 1

    for evt in proximity_raw:
        evt["event_class"] = "proximity_watch"
        evt["watch_km"] = PROXIMITY_WATCH_KM
        risk_tier = risk_tier_for_proximity(evt["dwell_minutes"], evt["min_distance_km"])
        obj_a = objects_by_id.get(evt["object_a_id"], {})
        obj_b = objects_by_id.get(evt["object_b_id"], {})
        criticality = obj_a.get("criticality") or "Tier3"
        priority = proximity_priority_score(evt["dwell_minutes"])
        maneuver = maneuver_for_event("proximity_watch", None)

        event_id = insert_event(
            object_a_id=evt["object_a_id"],
            object_b_id=evt["object_b_id"],
            event_class="proximity_watch",
            tca_timestamp=evt["tca_timestamp"],
            miss_distance_km=evt["min_distance_km"],
            rel_velocity_km_s=None,
            pc_score=0.0,
            risk_tier=risk_tier,
            priority_score=priority,
            is_demo=is_demo,
            screening_run_id=run_id,
        )
        observations.append({
            "event_id": event_id, "object_a_id": evt["object_a_id"], "object_b_id": evt["object_b_id"],
            "event_class": "proximity_watch", "tca_timestamp": evt["tca_timestamp"],
            "miss_distance_km": evt["min_distance_km"], "rel_velocity_km_s": None,
            "pc_score": 0.0, "risk_tier": risk_tier, "priority_score": priority,
            "dwell_minutes": evt["dwell_minutes"], "is_demo": is_demo,
        })
        brief = generate_and_review_brief(
            evt, pc=0.0, risk_tier=risk_tier, delta_v_m_s=None,
            object_a_name=obj_a.get("name", evt["object_a_id"]),
            object_b_name=obj_b.get("name", evt["object_b_id"]),
            object_b_type=obj_b.get("object_type", "foreign_sat"),
            criticality=criticality,
        )
        insert_brief(
            event_id=event_id,
            brief_text=brief["brief_text"],
            maneuver_text=brief["maneuver_text"],
            delta_v_ms=None,
            generated_by=brief["generated_by"],
            reviewer_notes=brief.get("reviewer_notes"),
            review_status=brief.get("review_status"),
        )
        n_proximity += 1

    # Persistent observations + correlation against the previous recorded run
    # of the same domain, in one transaction. A history failure must not fail
    # screening itself: it is logged, the run stays unrecorded (never used as
    # a correlation baseline) and the response carries delta=None.
    delta = None
    recorded = False
    try:
        record_run_observations(run_id, mode, observations, reset_baseline=reset_baseline)
        recorded = True
        delta = compact_delta(run_id)
    except Exception:
        logger.exception("Recording event observations failed for screening run %s", run_id)

    # Optional Telegram notifications (backend/notify.py): after events and
    # their persistent tracks are stored; reads stored values only and can
    # never fail or alter the screening run.
    if recorded:
        try:
            notify_run(run_id, data_source=DATA_SOURCE)
        except Exception:
            logger.exception("Notification step failed for screening run %s; screening unaffected", run_id)

    update_screening_run(
        run_id, completed_at=_now_iso(),
        collision_events=n_collision, proximity_events=n_proximity,
    )
    return {
        "delta": delta,
        "collision_events": n_collision,
        "proximity_events": n_proximity,
        "screening_run_id": run_id,
        "mode": mode,
        "screening_stats": screen_stats,
        "window_hours": window_hours,
        "step_seconds": PROPAGATION_STEP_SECONDS,
    }


def run_full_pipeline():
    """ingest -> propagate -> conjunction + threat -> risk -> briefs. This is
    what `/api/refresh` calls. run_ingest records the ingest_runs row. A
    normal (non-demo) screening clears any demo overrides, so the feed
    afterwards reflects real data only. Ingest runs inside PIPELINE_LOCK too,
    so a demo seed can never interleave with any step of a refresh."""
    with PIPELINE_LOCK:
        try:
            counts, using_cache = run_ingest()
        except IngestSafetyError:
            raise
        except Exception as exc:
            # Fetch/parse failure before the catalog was touched (no network
            # and no usable cache, malformed upstream data, ...). The detail
            # (which may contain local paths) stays in the server log only.
            logger.exception("Catalog ingest failed; catalog and events left unchanged")
            raise IngestFailedError(
                "Catalog refresh failed: CelesTrak data could not be fetched or parsed and no "
                f"usable offline cache was available ({type(exc).__name__}). The existing catalog "
                "and events were left unchanged; check network access and retry."
            ) from exc
        run_ingest.last_using_cache = using_cache
        ingest_run_id = getattr(run_ingest, "last_ingest_run_id", None)
        summary = run_screening_and_briefs()
    return {"object_counts": counts, "using_cache": using_cache, "ingest_run_id": ingest_run_id, **summary}


# ---------------------------------------------------------------------------
# Shared refresh pipeline (manual POST /api/refresh and the scheduler)
# ---------------------------------------------------------------------------
# ONE entry point, run_refresh_pipeline(trigger, actor), wraps the refresh
# (run_full_pipeline) with: overlap protection, scheduled-run safety guards,
# a refresh_attempts row, the screening run's trigger, and a governance
# audit row. Failures leave the last-known-good catalog/events untouched
# (ingest refuses/fails before reconciling; screening never starts).
#
# Overlap: _REFRESH_GUARD is a plain Lock taken non-blocking -- a second
# refresh (manual or scheduled) while one is running returns immediately
# (manual -> RefreshInProgressError -> HTTP 409; scheduled -> 'skipped').
# It is separate from PIPELINE_LOCK, which still serialises refresh vs demo
# seeding (a refresh waits for an in-flight demo, as before). A plain Lock
# (not RLock) so a background refresh thread can release what the request
# thread acquired.
_REFRESH_GUARD = threading.Lock()
_REFRESH_CONTEXT = threading.local()
SCHEDULER_ACTOR = {"user_id": None, "username": "scheduler", "role": None}

# Last background refresh thread (tests join it).
_LAST_BACKGROUND_THREAD = None


class RefreshInProgressError(RuntimeError):
    """A refresh is already running (manual refresh -> HTTP 409)."""

    def __init__(self, attempt_id=None):
        super().__init__("A catalog refresh is already in progress; wait for it to finish and retry.")
        self.attempt_id = attempt_id


def _current_refresh_context():
    return getattr(_REFRESH_CONTEXT, "value", None)


def refresh_in_progress():
    return _REFRESH_GUARD.locked()


def _parse_ts(value):
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def last_successful_refresh_at():
    """Completion time of the last successful refresh: the latest successful
    refresh_attempts row; for DBs whose refreshes predate that table, the
    latest successful ingest_runs row."""
    attempt = latest_successful_refresh_attempt()
    if attempt and attempt.get("completed_at"):
        return attempt["completed_at"]
    ingest = latest_ingest_run()
    return ingest["completed_at"] if ingest else None


def scheduled_guard_reason(now=None):
    """Reason a scheduled refresh must be skipped right now, or None:
    'too_soon'    a successful refresh completed < SCHEDULER_MIN_INTERVAL_MINUTES ago
    'demo_active' current events come from a DEMO run started
                  < SCHEDULER_DEMO_GUARD_MINUTES ago."""
    now = now or datetime.now(timezone.utc)
    last_ok = _parse_ts(last_successful_refresh_at())
    if last_ok is not None and (now - last_ok).total_seconds() < config.SCHEDULER_MIN_INTERVAL_MINUTES * 60:
        return "too_soon"
    run = latest_screening_run()
    if run and run.get("mode") == "demo":
        started = _parse_ts(run.get("started_at"))
        if started is not None and (now - started).total_seconds() < config.SCHEDULER_DEMO_GUARD_MINUTES * 60:
            return "demo_active"
    return None


def _actor_for(trigger, actor):
    return SCHEDULER_ACTOR if trigger == "scheduled" else actor


def _audit_refresh(trigger, actor, attempt_id, status, reason=None, **extra):
    try:
        insert_governance_audit(_actor_for(trigger, actor), "refresh", "refresh_attempt", attempt_id,
                                {"trigger": trigger, "status": status, "reason": reason, **extra})
    except Exception:
        logger.exception("Could not write the refresh governance audit row (attempt %s)", attempt_id)


def _skip(trigger, actor, reason):
    now = _now_iso()
    attempt_id = insert_refresh_attempt(trigger, "skipped", started_at=now, completed_at=now, reason=reason,
                                        actor=_actor_for(trigger, actor))
    _audit_refresh(trigger, actor, attempt_id, "skipped", reason)
    logger.info("%s refresh skipped (%s); attempt %s", trigger.capitalize(), reason, attempt_id)
    return {"status": "skipped", "reason": reason, "attempt_id": attempt_id, "trigger": trigger,
            "summary": None}


def _begin_refresh(trigger, actor):
    """Acquire the overlap guard, apply scheduled-run guards and record the
    'running' attempt. Returns (handle, None) or (None, skipped_result)."""
    if trigger not in ("manual", "scheduled"):
        raise ValueError(f"Unknown refresh trigger {trigger!r}")
    if not _REFRESH_GUARD.acquire(blocking=False):
        return None, _skip(trigger, actor, "refresh_in_progress")
    try:
        reason = scheduled_guard_reason() if trigger == "scheduled" else None
        if not reason:
            horizon = get_screening_horizon_hours()
            attempt_id = insert_refresh_attempt(trigger, "running", horizon_hours=horizon,
                                                actor=_actor_for(trigger, actor))
    except BaseException:
        _REFRESH_GUARD.release()
        raise
    if reason:
        _REFRESH_GUARD.release()
        return None, _skip(trigger, actor, reason)
    return {"trigger": trigger, "actor": actor, "attempt_id": attempt_id, "horizon_hours": horizon}, None


def _execute_refresh(handle, runner, raise_errors):
    """Run the refresh for a begun attempt; always releases the guard."""
    try:
        if handle["trigger"] == "scheduled":
            # Wait for any in-flight demo first, then re-check the demo guard
            # under the lock so an automatic refresh never wipes a demo that
            # finished while it waited. Reentrant: the runner takes it again.
            with PIPELINE_LOCK:
                reason = scheduled_guard_reason()
                if reason == "demo_active":
                    attempt_id = handle["attempt_id"]
                    update_refresh_attempt(attempt_id, status="skipped", reason=reason, completed_at=_now_iso())
                    _audit_refresh("scheduled", None, attempt_id, "skipped", reason)
                    return {"status": "skipped", "reason": reason, "attempt_id": attempt_id,
                            "trigger": "scheduled", "summary": None}
                return _run_and_record(handle, runner, raise_errors)
        return _run_and_record(handle, runner, raise_errors)
    finally:
        _REFRESH_GUARD.release()


def _run_and_record(handle, runner, raise_errors):
    trigger, actor, attempt_id = handle["trigger"], handle["actor"], handle["attempt_id"]
    horizon = handle["horizon_hours"]
    runner = runner or run_full_pipeline  # resolved at call time (patchable)
    summary, error, extra = None, None, {}
    _REFRESH_CONTEXT.value = {"trigger": trigger, "horizon_hours": horizon}
    try:
        summary = runner()
        status, reason = "success", None
    except IngestSafetyError as exc:
        status, reason, error = "refused", getattr(exc, "reason", None) or "ingest_refused", exc
        extra = {"ingest_run_id": getattr(exc, "ingest_run_id", None)}
    except IngestFailedError as exc:
        status, reason, error = "failed", "ingest_failed", exc
    except Exception as exc:
        logger.exception("Unexpected internal error during %s refresh (attempt %s)", trigger, attempt_id)
        status, reason, error = "failed", "internal_error", exc
    finally:
        _REFRESH_CONTEXT.value = None

    fields = {"status": status, "reason": reason, "completed_at": _now_iso()}
    if error is None and isinstance(summary, dict):
        if summary.get("ingest_run_id") is not None:
            fields["ingest_run_id"] = summary["ingest_run_id"]
        if summary.get("screening_run_id") is not None:
            fields["screening_run_id"] = summary["screening_run_id"]
        if summary.get("window_hours") is not None:
            fields["horizon_hours"] = int(summary["window_hours"])
    elif extra.get("ingest_run_id") is not None:
        fields["ingest_run_id"] = extra["ingest_run_id"]
    try:
        update_refresh_attempt(attempt_id, **fields)
    except Exception:
        logger.exception("Could not record refresh attempt %s outcome", attempt_id)
    _audit_refresh(trigger, actor, attempt_id, status, reason,
                   screening_run_id=fields.get("screening_run_id"),
                   horizon_hours=fields.get("horizon_hours", horizon))
    if error is not None:
        logger.warning("%s refresh attempt %s %s (%s); last-known-good catalog and events kept.",
                       trigger.capitalize(), attempt_id, status, reason)
        if raise_errors:
            raise error
        return {"status": status, "reason": reason, "attempt_id": attempt_id, "trigger": trigger,
                "summary": None}
    return {"status": "success", "reason": None, "attempt_id": attempt_id, "trigger": trigger,
            "summary": summary}


def run_refresh_pipeline(trigger, actor=None, runner=None, background=False, raise_errors=None):
    """The single refresh entry point for manual (POST /api/refresh) and
    scheduled (internal scheduler thread / external-cron endpoint) refreshes.

    Returns {status, reason, attempt_id, trigger, summary}; status is
    'success' | 'skipped' | 'failed' | 'refused', or 'accepted' when
    background=True started a worker thread. Manual refreshes (raise_errors
    defaults to True for them) raise RefreshInProgressError on overlap and
    re-raise ingest/internal errors after recording them, so the HTTP layer
    keeps its existing 409/502/500 shapes. Scheduled refreshes never raise
    for pipeline failures. `runner` defaults to run_full_pipeline."""
    global _LAST_BACKGROUND_THREAD

    if raise_errors is None:
        raise_errors = trigger == "manual"
    init_db()  # idempotent schema guard (refresh_attempts/app_settings exist before use)
    handle, skipped = _begin_refresh(trigger, actor)
    if skipped is not None:
        if trigger == "manual" and skipped["reason"] == "refresh_in_progress":
            raise RefreshInProgressError(skipped["attempt_id"])
        return skipped
    if not background:
        return _execute_refresh(handle, runner, raise_errors)

    def _worker():
        try:
            _execute_refresh(handle, runner, raise_errors=False)
        except Exception:
            logger.exception("Background %s refresh crashed (attempt %s)", trigger, handle["attempt_id"])

    thread = threading.Thread(target=_worker, name=f"refresh-{trigger}-{handle['attempt_id']}", daemon=True)
    _LAST_BACKGROUND_THREAD = thread
    try:
        thread.start()
    except BaseException:
        _REFRESH_GUARD.release()
        update_refresh_attempt(handle["attempt_id"], status="failed", reason="internal_error",
                               completed_at=_now_iso())
        raise
    return {"status": "accepted", "reason": None, "attempt_id": handle["attempt_id"], "trigger": trigger,
            "summary": None}
