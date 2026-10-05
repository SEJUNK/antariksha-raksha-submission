"""Event analysis provenance -- jury/operator transparency layer.

For a single conjunction/proximity event, answers: where the orbital data
came from, how fresh it is, what engine/config produced each number, how
collision probability and risk were actually computed, what the AI did and
did not do, and whether the event is real or demo/simulated.

Deliberately a read-only aggregation: every value here is derived from
existing config (backend/config.py), the event/object rows, and the
persisted run-provenance rows (ingest_runs / screening_runs) in the DB.
Nothing new is computed about the event itself.
"""

import json
from collections import Counter
from datetime import datetime, timezone

from backend.brief_agent import REVIEW_DESCRIPTION
from backend.settings import get_screening_horizon_hours
from backend.config import (
    CATALOG_REFRESH_STALE_HOURS,
    CRITICALITY_MULTIPLIERS,
    OLLAMA_MODEL,
    PC_HARD_BODY_RADIUS_KM,
    PROPAGATION_STEP_SECONDS,
    PROPAGATION_WINDOW_HOURS,
    PROXIMITY_WATCH_KM,
    SCREENING_THRESHOLD_KM,
)
from backend.db import (
    apply_demo_overrides,
    decisions_for_event,
    get_event_with_brief,
    get_ingest_run,
    get_screening_run,
    latest_failed_ingest_run,
    latest_ingest_run,
    latest_screening_run,
    list_demo_overrides,
    list_events,
    list_objects,
)
from backend.propagate import MAX_TLE_AGE_DAYS, tle_age_days_for
from backend.risk_score import PC_METHOD
from backend.event_history import risk_drivers as _risk_drivers

DATA_SOURCE = "CelesTrak public GP/TLE data (celestrak.org)"

MODE_LABELS = {
    "live": "LIVE DATA",
    "cached": "CACHED DATA",
    "stale": "STALE DATA",
    "demo": "DEMO MODE",
    "not_screened": "NOT SCREENED",
}

FEEDBACK_MECHANISM = (
    "In-context feedback: up to 3 recent operator rejection reasons are added "
    "to the prompt; no model weights are modified"
)
AI_DOES = [
    "Drafts the operator-readable brief from values already computed by the analysis pipeline",
    "Runs a second-pass consistency check of the draft against the input data",
]
AI_DOES_NOT = [
    "Compute TCA, miss distance, relative velocity, Pc, risk tier or priority",
    "Plan or execute maneuvers (delta-v comes from a deterministic estimate)",
    "Make decisions -- every brief requires operator approval or dismissal",
    "Learn by updating model weights",
]


def catalog_age_stats(objects):
    """TLE epoch age statistics for a catalog: min/max/avg days and how many
    objects exceed MAX_TLE_AGE_DAYS (excluded from screening by
    propagate_all). Single helper shared by the dashboard status and the
    screening-run record, so the two can never disagree."""
    ages = []
    for obj in objects:
        try:
            ages.append(tle_age_days_for(obj))
        except Exception:
            continue
    return {
        "min": round(min(ages), 2) if ages else None,
        "max": round(max(ages), 2) if ages else None,
        "avg": round(sum(ages) / len(ages), 2) if ages else None,
        "excluded_stale": sum(1 for a in ages if a > MAX_TLE_AGE_DAYS),
    }


def _json_or_none(text):
    if not text:
        return None
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


def screening_run_summary(run):
    """Screening-run row plus parsed JSON fields and its ingest record, or
    None. Shared by the status snapshot (latest_run) and event provenance."""
    if run is None:
        return None
    ingest = get_ingest_run(run.get("ingest_run_id"))
    return {
        **run,
        "source": run.get("data_source"),
        "ingest_completed_at": ingest["completed_at"] if ingest else None,
        "ingest_used_cache": bool(ingest["used_cache"]) if ingest else None,
        "ingest_source_format": ingest.get("source_format") if ingest else None,
        "counts_by_type": _json_or_none(run.get("counts_by_type_json")),
        "tle_age_days": {
            "min": run.get("tle_age_min_days"),
            "max": run.get("tle_age_max_days"),
            "avg": run.get("tle_age_avg_days"),
        },
        "demo_scenario": _json_or_none(run.get("demo_scenario_json")),
    }


def _object_provenance(obj):
    """name/NORAD ID/type/criticality/TLE age for one object, or None if the
    object row is missing (e.g. decayed/removed since the event was scored)."""
    if obj is None:
        return None
    try:
        age_days = round(tle_age_days_for(obj), 2)
    except Exception:
        age_days = None
    demo_adjusted = bool(obj.get("demo_adjusted", False))
    return {
        "name": obj.get("name"),
        "norad_id": obj.get("norad_id"),
        "object_type": obj.get("object_type"),
        "criticality": obj.get("criticality"),
        # 'tle' or 'omm': the element format this object was propagated from
        # (a demo-adjusted copy is always the disclosed TLE override).
        "source_format": obj.get("source_format") or "tle",
        "tle_age_days": age_days,
        "demo_adjusted": demo_adjusted,
        "derived_from_norad_id": obj.get("demo_derived_from_norad_id") if demo_adjusted else None,
        "demo_scenario": obj.get("demo_scenario") if demo_adjusted else None,
    }


def screening_horizon(run):
    """(window_hours, step_seconds, source) for a screening-run row.

    The horizon/step actually used are persisted per run (screening_runs.
    window_hours / step_seconds); the configured constants are only a
    fallback for legacy events/runs that predate that record."""
    if run and run.get("window_hours") is not None and run.get("step_seconds") is not None:
        return run["window_hours"], run["step_seconds"], "screening_run"
    return PROPAGATION_WINDOW_HOURS, PROPAGATION_STEP_SECONDS, "config_fallback"


def _generated_by_label(generated_by):
    if generated_by == "llm":
        return f"LLM (local Ollama {OLLAMA_MODEL})"
    if generated_by == "fallback_template":
        return "Deterministic template"
    return None


def get_event_provenance(event_id):
    """Build the provenance record for one event. Returns None if the event
    does not exist (caller maps that to a 404)."""
    event = get_event_with_brief(event_id)
    if event is None:
        return None

    is_demo = bool(event.get("is_demo", 0))
    # Include inactive objects: an event may refer to an object that dropped
    # out of the catalog in a later successful ingest.
    objects = list_objects(include_inactive=True)
    if is_demo:
        # A demo event was computed from the override-applied catalog; show
        # the objects as that run saw them (and flag the adjusted one).
        objects = apply_demo_overrides(objects, list_demo_overrides())
    objects_by_id = {o["norad_id"]: o for o in objects}
    obj_a = objects_by_id.get(event["object_a_id"])
    obj_b = objects_by_id.get(event["object_b_id"])

    is_collision = event["event_class"] == "collision_risk"
    screening_threshold_km = SCREENING_THRESHOLD_KM if is_collision else PROXIMITY_WATCH_KM
    state_evaluated_at = "TCA (refined)" if is_collision else "grid time of minimum separation"

    generated_by = event.get("generated_by")
    ai_used = generated_by == "llm"

    hard_body = (event.get("pc_hard_body_radius_km") or PC_HARD_BODY_RADIUS_KM) if is_collision else None

    if is_collision:
        # Events scored before the analytic indicator have pc_samples set and
        # no pc_method: describe them as what they were (Monte Carlo).
        legacy_mc = event.get("pc_method") is None and event.get("pc_samples")
        samples = event.get("pc_samples") if legacy_mc else None
        method = event.get("pc_method") or (
            "Monte Carlo sampling (legacy event, scored before the analytic indicator)"
            if legacy_mc else PC_METHOD
        )
        probability = {
            "method": method,
            "label": "Simplified analytic collision-probability indicator -- NOT an operational covariance-based Pc"
            if not legacy_mc else "Legacy Monte Carlo estimate",
            "deterministic": not legacy_mc,
            "samples": samples,
            "pc_resolution": (1.0 / samples) if samples else None,
            "sigma_km": event.get("pc_sigma_km"),
            "relative_sigma_km": (2 ** 0.5) * event["pc_sigma_km"] if event.get("pc_sigma_km") else None,
            "hard_body_radius_km": hard_body,
            "state_evaluated_at": state_evaluated_at,
            "simplified": True,
            "validation_reference": "Monte Carlo sampling is kept only as a test reference for the analytic formula",
            # Deliberately not described as covariance-based -- see README Limitations.
            "uncertainty_model": (
                "Simplified prototype estimate: isotropic Gaussian position "
                "sigma per object, growing linearly with time-to-TCA. "
                "NOT full covariance propagation."
            ),
            "limitations": [
                "Isotropic Gaussian position uncertainty with linear growth -- not propagated covariance",
                "Short-encounter assumption (straight-line relative motion through the encounter plane) -- "
                "weak for slow or co-orbital encounters",
                "Uses assumed sigma, not orbit-determination covariance, so absolute Pc values are indicative only",
            ] + ([f"Legacy Monte Carlo: Pc = 0 meant below the resolution of 1/{samples}"] if legacy_mc else []),
        }
    else:
        probability = {
            "method": "Not computed for proximity_watch events (no Pc; dwell-time/distance based tiering only)",
            "samples": None,
            "pc_resolution": None,
            "sigma_km": None,
            "hard_body_radius_km": None,
            "state_evaluated_at": None,
            "simplified": True,
            "uncertainty_model": "N/A",
            "limitations": [],
        }

    screening_run = screening_run_summary(get_screening_run(event.get("screening_run_id")))
    horizon_hours, step_seconds, horizon_source = screening_horizon(screening_run)

    return {
        "data": {
            "source": DATA_SOURCE,
            "object_a": _object_provenance(obj_a),
            "object_b": _object_provenance(obj_b),
            "screening_run": screening_run,
        },
        "propagation": {
            "engine": "SGP4 (via Skyfield)",
            # Values the event's own screening run used (not today's config).
            "horizon_hours": horizon_hours,
            "step_seconds": step_seconds,
            "horizon_source": horizon_source,
            "screening_run_id": screening_run["id"] if screening_run else None,
        },
        "analysis": {
            "event_class": event["event_class"],
            "screening_threshold_km": screening_threshold_km,
            "tca": event["tca_timestamp"],
            "miss_distance_km": event["miss_distance_km"],
            "relative_velocity_km_s": event.get("rel_velocity_km_s"),
            "tca_refinement": event.get("tca_refinement") if is_collision else None,
            "state_evaluated_at": state_evaluated_at,
        },
        "probability": probability,
        "risk": {
            "tier": event["risk_tier"],
            "criticality": obj_a.get("criticality") if obj_a else None,
            "criticality_multiplier": CRITICALITY_MULTIPLIERS.get(obj_a["criticality"]) if obj_a and obj_a.get("criticality") else None,
            "priority_score": event["priority_score"],
            # Risk-driver explanation from stored values/config only (see
            # backend/event_history.py::risk_drivers).
            **_risk_drivers(event, obj_a),
        },
        "ai": {
            "used": ai_used,
            "model": OLLAMA_MODEL if ai_used else None,
            "generated_by": generated_by,
            "generated_by_label": _generated_by_label(generated_by),
            "purpose": "Brief generation / operator-readable explanation only -- decision-support, not decision-making",
            "involved_in_numerical_calculation": False,
            "fallback": "Deterministic template brief (no LLM call) when the local Ollama model is unreachable or times out, "
                        "or when the regenerated LLM draft still fails the deterministic tone guard",
            "reviewer_notes": event.get("reviewer_notes"),
            "review_status": event.get("review_status"),
            "review_description": REVIEW_DESCRIPTION,
            "feedback_mechanism": FEEDBACK_MECHANISM,
            "ai_does": AI_DOES,
            "ai_does_not": AI_DOES_NOT,
        },
        "provenance": {
            "is_demo": is_demo,
            "mode": "DEMO" if is_demo else "REAL",
        },
        "human_decision": {
            "status": event.get("status"),
            "history": decisions_for_event(event_id),
        },
    }


def _hours_since(iso_ts):
    try:
        ts = datetime.fromisoformat(iso_ts)
    except (TypeError, ValueError):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ts).total_seconds() / 3600.0


def ingest_resolution_summary(last_ingest):
    """Source format, named-entry resolution and refusal state of the
    catalog ingest, for the status payload."""
    failed = latest_failed_ingest_run()
    return {
        "source_format": last_ingest.get("source_format") if last_ingest else None,
        "resolved_count": last_ingest.get("resolved_count") if last_ingest else None,
        "unresolved": (_json_or_none(last_ingest.get("unresolved_json")) or []) if last_ingest else [],
        "rejected_records": (_json_or_none(last_ingest.get("rejected_records_json")) or []) if last_ingest else [],
        "last_failure": {
            "at": failed["completed_at"],
            "reason": failed.get("failure_reason"),
            "unresolved": _json_or_none(failed.get("unresolved_json")) or [],
        } if failed else None,
    }


def get_live_data_status():
    """System-wide data status snapshot -- where the working catalog came
    from, how fresh it is, what mode the current events are in, and the
    current screening output, before any single event is opened. Distinct
    from get_event_provenance() above (which is per-event)."""
    objects = list_objects()
    events = list_events()
    age_stats = catalog_age_stats(objects)
    counts_by_type = Counter(obj["object_type"] for obj in objects)

    last_ingest = latest_ingest_run()
    latest_run = screening_run_summary(latest_screening_run())
    run_window_hours, run_step_seconds, horizon_source = screening_horizon(latest_run)

    last_refresh_at = last_ingest["completed_at"] if last_ingest else None
    last_refresh_used_cache = bool(last_ingest["used_cache"]) if last_ingest else None
    refresh_age_hours = _hours_since(last_refresh_at) if last_refresh_at else None
    refresh_is_old = refresh_age_hours is not None and refresh_age_hours > CATALOG_REFRESH_STALE_HOURS

    # Demo runs no longer touch `objects`, so the catalog "as of" time is the
    # last recorded ingest; objects.last_updated is the fallback for DBs
    # populated before ingest_runs existed.
    timestamp = last_refresh_at or max((obj["last_updated"] for obj in objects), default=None)

    warnings = []
    if latest_run and latest_run.get("mode") == "demo":
        mode = "demo"
        warnings.append(
            "Demo mode: current events come from a disclosed simulated TLE adjustment "
            "and must not be treated as real conjunctions."
        )
    elif latest_run is None:
        mode = "not_screened"
        warnings.append("No screening run has been recorded yet -- run a refresh to screen the catalog.")
    elif last_ingest and last_ingest["used_cache"]:
        mode = "cached"
    elif last_ingest is None or refresh_is_old:
        mode = "stale"
    else:
        mode = "live"

    if last_ingest is None:
        warnings.append("No catalog refresh from CelesTrak has been recorded; data freshness is unverified.")
    elif last_ingest["used_cache"]:
        warnings.append(
            "The last catalog refresh could not reach CelesTrak for every group and fell back "
            "to locally cached TLE files."
        )
    if refresh_is_old:
        warnings.append(
            f"The catalog was last refreshed {refresh_age_hours:.0f} hours ago "
            f"(older than {CATALOG_REFRESH_STALE_HOURS} hours)."
        )
    if age_stats["excluded_stale"]:
        warnings.append(
            f"{age_stats['excluded_stale']} object(s) are excluded from screening because their "
            f"TLE epoch is older than {MAX_TLE_AGE_DAYS} days."
        )
    ingest_info = ingest_resolution_summary(last_ingest)
    if ingest_info["unresolved"]:
        warnings.append(
            f"{len(ingest_info['unresolved'])} working-set name entr"
            f"{'y' if len(ingest_info['unresolved']) == 1 else 'ies'} did not resolve to exactly one "
            "catalog object in the last refresh; the previously ingested object is kept where known."
        )
    if ingest_info["last_failure"]:
        warnings.append(f"The most recent catalog refresh was refused: {ingest_info['last_failure']['reason']}")

    return {
        "source": DATA_SOURCE,
        "timestamp": timestamp,
        "objects_ingested": {
            "satellite": counts_by_type.get("satellite", 0),
            "debris": counts_by_type.get("debris", 0),
            "foreign_sat": counts_by_type.get("foreign_sat", 0),
        },
        "object_count": len(objects),
        "tle_age_days": {"min": age_stats["min"], "max": age_stats["max"], "avg": age_stats["avg"]},
        # Horizon/step of the latest screening run (whose events are shown);
        # configured_* are the current config values for the next run.
        "screening_window_hours": run_window_hours,
        "screening_step_seconds": run_step_seconds,
        "screening_horizon_source": horizon_source,
        # Active admin-configured horizon for the next run (backend/settings.py).
        "configured_window_hours": get_screening_horizon_hours(),
        "configured_step_seconds": PROPAGATION_STEP_SECONDS,
        "conjunction_candidates": sum(1 for e in events if e["event_class"] == "collision_risk"),
        "proximity_candidates": sum(1 for e in events if e["event_class"] == "proximity_watch"),
        "mode": mode,
        "mode_label": MODE_LABELS[mode],
        "warnings": warnings,
        "last_refresh_at": last_refresh_at,
        "last_refresh_used_cache": last_refresh_used_cache,
        "catalog_refresh_stale_hours": CATALOG_REFRESH_STALE_HOURS,
        "max_tle_age_days": MAX_TLE_AGE_DAYS,
        "objects_excluded_stale": age_stats["excluded_stale"],
        "catalog_source_formats": dict(Counter(obj.get("source_format") or "tle" for obj in objects)),
        "ingest": ingest_info,
        "latest_run": latest_run,
        "demo_scenario": latest_run.get("demo_scenario") if mode == "demo" else None,
    }
