"""FastAPI app + routes (blueprint Section 6.8)."""

import hmac
import logging
import re
import threading
import time
import unicodedata

import requests
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator

from backend import config
from fastapi.responses import JSONResponse, PlainTextResponse

from backend.db import (
    decision_stats,
    decisions_for_event,
    get_event_with_brief,
    init_db,
    insert_decision,
    latest_ingest_run,
    list_all_decisions,
    list_events,
    list_objects,
    list_objects_with_demo_overrides,
    transition_brief_status,
)
from backend.auth import (
    AuthError,
    Principal,
    actor_of,
    admin_router,
    auth_error_response,
    bootstrap_users,
    get_principal,
    require_permission,
    SCHEDULER_ENDPOINT,
)
from backend.auth import router as auth_router
from backend.notify import telegram_configured, telegram_enabled
from backend.ingest import run_ingest
from backend.pipeline import run_full_pipeline
from backend.propagate import current_positions, position_tracks

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Optional API key for state-changing requests (see the "API access control"
# section of backend/config.py). Applied as an app-wide dependency so every
# current and future POST/PUT/PATCH/DELETE route is covered; read-only
# methods (and CORS preflight, answered by CORSMiddleware before routing)
# stay open. Settings are read from backend.config at request time so they
# can be changed in tests. Request bodies and key values are never logged.
_READ_ONLY_METHODS = {"GET", "HEAD", "OPTIONS"}


def require_api_key_for_writes(request: Request):
    if request.method in _READ_ONLY_METHODS:
        return
    if request.scope.get("path") == SCHEDULER_ENDPOINT:
        return  # authenticated by its own shared secret (scheduled_refresh_endpoint)
    expected = config.API_KEY
    if not expected:
        if config.AUTH_REQUIRED:
            raise HTTPException(status_code=503, detail="API authentication is required but no API key "
                                                        "is configured on the server (ANTARIKSHA_API_KEY).")
        return  # local prototype mode
    supplied = request.headers.get(config.API_KEY_HEADER, "")
    if not hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(status_code=401, detail=f"Missing or invalid {config.API_KEY_HEADER} header.",
                            headers={"WWW-Authenticate": "ApiKey"})


# Dependencies run in order: the optional deployment API-key gate first, then
# the session/CSRF check (backend/auth.py::get_principal) for every /api route
# except GET /api/health and POST /api/auth/login|logout.
app = FastAPI(title="ANTARIKSHA-RAKSHA",
              dependencies=[Depends(require_api_key_for_writes), Depends(get_principal)])
app.add_exception_handler(AuthError, lambda request, exc: auth_error_response(exc))

# Credentialed CORS (session cookie) only for the explicit origin list; a
# wildcard is never allowed with credentials.
_CORS_ORIGINS = [o for o in config.CORS_ORIGINS if o != "*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(admin_router)

# Local brief translation (optional, disabled by default). Covered by the
# app-wide write-auth dependency above like every other route. POST
# /api/translation/brief is read-only (nothing persisted) -> 'translate',
# which every role has.
from backend.translation_api import router as translation_router  # noqa: E402

app.include_router(translation_router, dependencies=[Depends(require_permission("translate"))])


@app.on_event("startup")
def _startup():
    init_db()
    from backend.db import ensure_protected_assets_seeded

    from backend.db import ProtectedAssetSeedError

    try:
        if ensure_protected_assets_seeded(config.WORKING_SET_PATH):
            logger.info("Protected-asset registry seeded once from data/working_set.json group_a; the database "
                        "registry is authoritative from now on.")
    except ProtectedAssetSeedError as exc:
        # Fail safe: nothing seeded or recorded; refreshes are refused until the source is fixed.
        logger.error("%s", exc)
    bootstrap_users()
    if "*" in config.CORS_ORIGINS:
        logger.error("ANTARIKSHA_CORS_ORIGINS contains '*': ignored (credentialed CORS needs explicit origins).")
    if config.API_KEY:
        logger.info("API auth ENABLED: state-changing requests require the %s header; GET endpoints open.",
                    config.API_KEY_HEADER)
    elif config.AUTH_REQUIRED:
        logger.error("ANTARIKSHA_AUTH_REQUIRED is set but ANTARIKSHA_API_KEY is empty: state-changing "
                     "requests will be refused (503) until a key is configured.")
    else:
        logger.info("API auth disabled = local prototype mode (set ANTARIKSHA_API_KEY to require %s on "
                    "state-changing requests).", config.API_KEY_HEADER)
    logger.info("CORS allowed origins: %s", ", ".join(config.CORS_ORIGINS) or "(none)")
    from backend.db import mark_interrupted_refresh_attempts
    from backend.scheduler import start_internal_scheduler

    if mark_interrupted_refresh_attempts():
        logger.warning("Refresh attempt(s) left 'running' by a previous process were recorded as failed "
                       "(interrupted).")
    start_internal_scheduler()


@app.on_event("shutdown")
def _shutdown():
    from backend.scheduler import stop_internal_scheduler

    stop_internal_scheduler()


# /api/objects and /api/tracks use the override-applied catalog: demo
# overrides only exist while the current events come from a demo run (a
# normal screening clears them), so the globe matches the event feed. Rows
# for an adjusted object carry demo_adjusted=True.
@app.get("/api/objects")
def get_objects():
    from backend.db import resolved_protected_norad_ids

    # `protected` comes from the protected-asset registry as resolved by the
    # latest successful refresh -- the same source as screening.
    protected_ids = resolved_protected_norad_ids()
    objects = current_positions(objects=list_objects_with_demo_overrides())
    for o in objects:
        o["protected"] = str(o.get("norad_id")) in protected_ids
    return objects


@app.get("/api/tracks")
def get_tracks(window_minutes: int = Query(120, ge=1, le=240),
               step_seconds: int = Query(60, ge=10, le=300)):
    # Bounded so one request cannot ask for an unbounded number of samples
    # per object (out-of-range or non-integer values -> 422).
    return position_tracks(window_minutes, step_seconds, objects=list_objects_with_demo_overrides())


@app.get("/api/status")
def get_live_data_status_route():
    """System-wide 'LIVE DATA' status: data source, catalog freshness, object
    counts, configured screening window, and current candidate counts. See
    backend/provenance.py::get_live_data_status (dashboard-level; distinct
    from the per-event /api/events/{id}/provenance)."""
    from backend.provenance import get_live_data_status
    from backend.scheduler import scheduler_status

    return {**get_live_data_status(), "scheduler": scheduler_status()}


DEMO_MODES = ("collision", "proximity")


@app.post("/api/demo/seed", dependencies=[Depends(require_permission("demo"))])
def demo_seed(mode: str = "collision", history_step: int | None = Query(None, ge=1, le=2)):
    """Run a disclosed demo scenario: store a simulated TLE for one object
    as a separate demo override (the real catalog is untouched; Section 10
    ethics) and re-run screening in demo mode so the event appears in the
    feed within one poll cycle. Returns the specific event_id produced so
    the UI can open it directly rather than guessing which feed row is the
    demo's.

    history_step=1|2 (collision only) runs the controlled two-step DEMO
    history: step 1 = larger disclosed designed miss, step 2 = smaller miss at
    the same designed encounter epoch, correlated into one demo track. Step 2
    without a recent step 1 -> 409 (reason history_not_started)."""
    from backend.demo_seed import DemoScenarioError, seed_event

    if mode not in DEMO_MODES:
        raise HTTPException(status_code=400, detail=f"Unknown demo mode '{mode}'; expected one of {list(DEMO_MODES)}")
    if history_step is not None and mode != "collision":
        raise HTTPException(status_code=400, detail="history_step is only supported for mode=collision")
    # Error bodies keep `detail` a plain string (the frontend displays it
    # as-is) and add a machine-readable `error` (+ `reason`) so a client can
    # tell an expected scenario failure from an internal error.
    try:
        kwargs = {} if history_step is None else {"history_step": history_step}
        return seed_event(proximity=(mode == "proximity"), **kwargs)
    except DemoScenarioError as exc:
        # Operator-facing failure, not a raw traceback (see DemoScenarioError).
        logger.warning("Demo scenario '%s' failed (%s): %s", mode, exc.reason, exc)
        return JSONResponse(status_code=exc.status_code, content={
            "detail": str(exc), "error": "demo_scenario_failed", "reason": exc.reason,
        })
    except Exception:
        # Unexpected internal error: full traceback in the server log only;
        # the client gets a generic message (no stack trace or file paths).
        logger.exception("Unexpected internal error while running demo scenario '%s'", mode)
        return JSONResponse(status_code=500, content={
            "detail": "The demo scenario failed due to an internal server error. "
                      "See the backend log for details.",
            "error": "internal_error",
        })


@app.get("/api/events")
def get_events():
    return list_events()


@app.get("/api/events/{event_id}")
def get_event(event_id: int):
    event = get_event_with_brief(event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return event


DISMISS_REASON_MAX_CHARS = 500


class DismissBody(BaseModel):
    # Recent reasons are fed back into the brief-generation prompt
    # (db.recent_rejection_reasons), so the text is bounded (>500 chars ->
    # 422) and normalised to a single line: control characters (newlines,
    # tabs, etc.) become spaces, whitespace runs collapse, ends are trimmed.
    reason: str | None = Field(None, max_length=DISMISS_REASON_MAX_CHARS)

    @field_validator("reason")
    @classmethod
    def _clean_reason(cls, value):
        if value is None:
            return None
        cleaned = "".join(" " if unicodedata.category(ch).startswith("C") else ch for ch in value)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned or None


def _log_decision(event_id: int, decision: str, rejection_reason: str | None = None, actor=None):
    """Record the operator decision in the persistent decision_log (kept
    across screening runs, which rebuild events/briefs; feeds the analytics
    dashboard + feedback loop)."""
    event = get_event_with_brief(event_id)
    if event is None:
        return False
    names = {o["norad_id"]: o["name"] for o in list_objects(include_inactive=True)}
    insert_decision(
        event_class=event["event_class"],
        object_a_name=names.get(event["object_a_id"], event["object_a_id"]),
        object_b_name=names.get(event["object_b_id"], event["object_b_id"]),
        risk_tier=event["risk_tier"],
        pc_score=event["pc_score"],
        miss_distance_km=event["miss_distance_km"],
        generated_by=event.get("generated_by"),
        decision=decision,
        rejection_reason=rejection_reason,
        event_id=event_id,
        is_demo=bool(event.get("is_demo", 0)),
        screening_run_id=event.get("screening_run_id"),
        tca_timestamp=event.get("tca_timestamp"),
        actor=actor,
    )
    return True


# Serialises check-and-set + audit append per process, so a double-click or
# client retry can't record the same decision twice. The DB-level
# conditional UPDATE in transition_brief_status is atomic on its own; this
# lock also covers the brief-not-yet-generated fallback and the log insert.
_DECISION_LOCK = threading.Lock()


def _decide(event_id: int, decision: str, rejection_reason: str | None = None, principal=None):
    """Idempotent decision: a repeat of the current decision returns the
    current state (200) without a new decision_log row; a first decision or
    a genuine approved<->dismissed change is recorded. Response is the event
    with brief plus `decision_recorded` (False for an idempotent repeat;
    on a repeat dismiss the originally logged reason is kept)."""
    with _DECISION_LOCK:
        result, changed = transition_brief_status(event_id, decision)
        if result is None:
            raise HTTPException(status_code=404, detail="Event not found")
        # A refresh can rebuild the events between the transition and the log
        # write; report what was actually written to the audit trail.
        recorded = changed and _log_decision(event_id, decision, rejection_reason=rejection_reason,
                                             actor=actor_of(principal))
    return {**result, "decision_recorded": bool(recorded)}


# The decision actor is the authenticated session principal -- never taken
# from the request body.
@app.post("/api/events/{event_id}/approve")
def approve_event(event_id: int, principal: Principal = Depends(require_permission("decide"))):
    return _decide(event_id, "approved", principal=principal)


@app.post("/api/events/{event_id}/dismiss")
def dismiss_event(event_id: int, body: DismissBody | None = None,
                  principal: Principal = Depends(require_permission("decide"))):
    return _decide(event_id, "dismissed", rejection_reason=body.reason if body else None, principal=principal)


@app.get("/api/events/{event_id}/decisions")
def get_event_decisions(event_id: int):
    """Operator decision history for one event, newest first."""
    if get_event_with_brief(event_id) is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return decisions_for_event(event_id)


@app.get("/api/decisions/stats")
def get_decision_stats():
    return decision_stats()


@app.get("/api/decisions/export")
def export_decisions():
    """Full decision audit trail as CSV (spreadsheet-ready). The actor columns
    (actor_username, actor_role) are appended last; empty = legacy record
    made before authentication existed."""
    include_actor = True
    import csv
    import io

    rows = list_all_decisions()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "decided_at", "event_class", "risk_tier", "object_a", "object_b",
                     "pc_score", "miss_distance_km", "brief_source", "decision", "rejection_reason",
                     "event_id", "is_demo", "screening_run_id", "tca_timestamp"]
                    + (["actor_username", "actor_role"] if include_actor else []))
    for r in rows:
        writer.writerow([r["id"], r["decided_at"], r["event_class"], r["risk_tier"],
                         r["object_a_name"], r["object_b_name"], r["pc_score"],
                         r["miss_distance_km"], r["generated_by"], r["decision"],
                         r["rejection_reason"] or "",
                         "" if r.get("event_id") is None else r["event_id"],
                         "" if r.get("is_demo") is None else r["is_demo"],
                         "" if r.get("screening_run_id") is None else r["screening_run_id"],
                         r.get("tca_timestamp") or ""]
                        + ([r.get("actor_username") or "", r.get("actor_role") or ""] if include_actor else []))
    return PlainTextResponse(
        buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=antariksha_decision_log.csv"},
    )


@app.get("/api/events/{event_id}/provenance")
def get_event_provenance_route(event_id: int):
    """Analysis provenance for one event: data source/freshness, propagation
    config, screening/probability/risk methodology, AI role, and real-vs-demo
    status -- all derived from existing config and event/object data (Section
    on jury transparency). See backend/provenance.py."""
    from backend.provenance import get_event_provenance

    provenance = get_event_provenance(event_id)
    if provenance is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return provenance


@app.get("/api/events/{event_id}/evolution")
def get_event_evolution_route(event_id: int):
    """How this event changed across recorded screening runs (persistent
    event_observations + prototype correlation rule; see
    backend/event_history.py). history_available=False for events whose run
    has no recorded observations yet."""
    from backend.event_history import get_event_evolution

    evolution = get_event_evolution(event_id)
    if evolution is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return evolution


@app.get("/api/screening/delta")
def get_screening_delta(domain: str | None = Query(None, pattern="^(real|demo)$")):
    """What changed between the latest recorded screening run and the
    previous recorded run of the same domain (real = live/cached, demo =
    demo; the two never mix). Defaults to the domain of the latest run."""
    from backend.event_history import compute_delta

    return compute_delta(domain=domain)


@app.get("/api/catalog/registry")
def get_protected_asset_registry():
    """Operator-configured protected assets (Group A) with their current data
    status, kept separate from the public object-catalog summary."""
    from backend.catalog_view import protected_asset_registry

    return protected_asset_registry()


@app.get("/api/catalog/new-objects")
def get_new_objects():
    """Objects that entered the LOCAL catalog after its baseline, with review
    status. New here does not mean newly launched."""
    from backend.catalog_view import new_object_review

    return new_object_review()


class ObjectReviewBody(BaseModel):
    note: str | None = Field(None, max_length=300)

    @field_validator("note")
    @classmethod
    def _clean_note(cls, value):
        if value is None:
            return None
        cleaned = " ".join("".join(" " if unicodedata.category(ch).startswith("C") else ch for ch in value).split())
        return cleaned or None


@app.post("/api/catalog/new-objects/{norad_id}/review")
def review_new_object(norad_id: str, body: ObjectReviewBody | None = None,
                      principal: Principal = Depends(require_permission("review_objects"))):
    """Record that an operator reviewed a new-to-catalog object (append-only
    audit). Changes nothing else: no classification, no protected status."""
    from backend.db import insert_object_review, list_new_objects

    if norad_id not in {o["norad_id"] for o in list_new_objects()}:
        raise HTTPException(status_code=404, detail="Not a new-to-catalog object")
    return insert_object_review(norad_id, body.note if body else None, actor=actor_of(principal))


@app.get("/api/events/{event_id}/profile")
def get_event_profile(event_id: int):
    """Separation-vs-time series around the event's TCA for charting."""
    from backend.propagate import separation_profile

    event = get_event_with_brief(event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    # include_inactive: the event may refer to an object that dropped out of a
    # later successful ingest.
    objects = (list_objects_with_demo_overrides(include_inactive=True) if event.get("is_demo")
               else list_objects(include_inactive=True))
    profile = separation_profile(event["object_a_id"], event["object_b_id"], event["tca_timestamp"],
                                 objects=objects)
    if profile is None:
        raise HTTPException(status_code=404, detail="Objects for event not found")
    return profile


def _ollama_reachable():
    if config.AI_MODE == "disabled":
        return False  # configured off: never touch the network
    try:
        requests.get(config.OLLAMA_URL.replace("/api/generate", "/api/tags"), timeout=3)
        return True
    except Exception:
        return False


def _ai_status(reachable):
    available = config.AI_MODE != "disabled" and bool(reachable)
    return {"mode": config.AI_MODE, "available": available,
            "status": "LOCAL_AI_AVAILABLE" if available else "AI_FALLBACK_ACTIVE"}


@app.get("/api/health")
def health():
    """Public liveness + non-sensitive operational summary (no secrets,
    tokens, hashes, URLs or file paths)."""
    from backend.scheduler import health_scheduler_status
    from backend.settings import get_screening_horizon_hours

    objects = list_objects()
    # Persisted ingest record survives backend restarts; the in-memory
    # attribute is only a fallback for a DB with no ingest_runs row yet.
    last_ingest = latest_ingest_run()
    using_cache = bool(last_ingest["used_cache"]) if last_ingest else getattr(run_ingest, "last_using_cache", False)
    reachable = _ollama_reachable()
    return {
        "ok": True,
        "status": "ok",
        "ollama_reachable": reachable,
        "object_count": len(objects),
        "using_cache": using_cache,
        # True only when alerts actually go out (TELEGRAM_ENABLED and credentials).
        "telegram_configured": telegram_enabled() and telegram_configured(),
        "scheduler": health_scheduler_status(),
        "screening_horizon_hours": get_screening_horizon_hours(),
        "ai": _ai_status(reachable),
    }


@app.post("/api/refresh")
def refresh(principal: Principal = Depends(require_permission("refresh"))):
    """Manual catalog refresh (trigger 'manual') through the shared
    pipeline.run_refresh_pipeline, the same entry point the scheduler uses.
    A refresh already in progress -> 409 refresh_in_progress."""
    from backend.ingest import IngestFailedError, IngestSafetyError
    from backend.pipeline import RefreshInProgressError, run_refresh_pipeline

    start = time.monotonic()
    try:
        # run_full_pipeline is looked up in this module at call time.
        result = run_refresh_pipeline("manual", actor=actor_of(principal), runner=run_full_pipeline)
    except RefreshInProgressError as exc:
        return JSONResponse(status_code=409, content={"detail": str(exc), "error": "refresh_in_progress"})
    except IngestSafetyError as exc:
        # Refused before screening: the previous catalog and events stay as
        # they were; the reason is recorded in a status='failed' ingest row.
        logger.warning("Refresh refused by ingest safety check (%s): %s", exc.reason, exc)
        return JSONResponse(status_code=409, content={
            "detail": str(exc), "error": "ingest_refused", "reason": exc.reason,
            "ingest_run_id": exc.ingest_run_id,
        })
    except IngestFailedError as exc:
        # Fetch/parse failure before the catalog was touched; traceback is in
        # the server log (pipeline.run_full_pipeline), the client gets the
        # clean operator message only.
        return JSONResponse(status_code=502, content={"detail": str(exc), "error": "ingest_failed"})
    except Exception:
        logger.exception("Unexpected internal error during refresh")
        return JSONResponse(status_code=500, content={
            "detail": "The refresh failed due to an internal server error. See the backend log for details.",
            "error": "internal_error",
        })
    summary = result["summary"]
    if not isinstance(summary, dict):
        summary = {"result": summary}
    summary = {**summary, "attempt_id": result["attempt_id"], "trigger": "manual"}
    summary["elapsed_seconds"] = round(time.monotonic() - start, 2)
    return summary


# ---------------------------------------------------------------------------
# External-cron scheduled refresh. Authenticated ONLY by
# `Authorization: Bearer <SCHEDULER_SECRET>` (or CRON_SECRET when
# SCHEDULER_SECRET is unset); exempt from session auth, CSRF and the optional
# API-key gate. It never authenticates as a user and can do nothing except
# trigger the scheduled refresh (with all scheduled-run guards). The secret
# is never logged or echoed.
# ---------------------------------------------------------------------------
@app.api_route(SCHEDULER_ENDPOINT, methods=["GET", "POST"])
def scheduled_refresh_endpoint(request: Request, wait: bool = False):
    from backend.scheduler import run_scheduled_refresh

    secret = config.scheduler_secret()
    if not secret:
        return JSONResponse(status_code=503, content={
            "detail": "Scheduled refresh endpoint is disabled: no scheduler secret is configured.",
            "error": "scheduler_disabled"})
    supplied = request.headers.get("authorization", "")
    scheme, _, token = supplied.partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(token.strip().encode("utf-8"),
                                                             secret.encode("utf-8")):
        return JSONResponse(status_code=401, content={"detail": "Missing or invalid scheduler secret.",
                                                      "error": "invalid_scheduler_secret"},
                            headers={"WWW-Authenticate": "Bearer"})
    result = run_scheduled_refresh(background=not wait)
    body = {"status": result["status"], "attempt_id": result["attempt_id"], "trigger": "scheduled"}
    if result["status"] == "accepted":
        return JSONResponse(status_code=202, content=body)
    body["reason"] = result.get("reason")
    summary = result.get("summary")
    if isinstance(summary, dict):
        body["screening_run_id"] = summary.get("screening_run_id")
        body["ingest_run_id"] = summary.get("ingest_run_id")
        body["window_hours"] = summary.get("window_hours")
    return JSONResponse(status_code=200, content=body)


# ---------------------------------------------------------------------------
# System settings (configure_system = ADMINISTRATOR only)
# ---------------------------------------------------------------------------
@app.get("/api/settings/screening-horizon")
def get_screening_horizon_setting():
    from backend.settings import screening_horizon_payload

    return screening_horizon_payload()


@app.put("/api/settings/screening-horizon")
async def put_screening_horizon_setting(request: Request,
                                        principal: Principal = Depends(require_permission("configure_system"))):
    """Change the screening horizon for the NEXT screening run. Strict: only
    the JSON integers in `allowed` are accepted (no strings, floats or
    booleans). Historical runs/events keep the horizon they recorded."""
    from starlette.concurrency import run_in_threadpool

    from backend.settings import InvalidSettingValue, set_screening_horizon_hours

    try:
        body = await request.json()
    except Exception:
        body = None
    if not isinstance(body, dict) or "hours" not in body:
        return _api_error(422, "Body must be a JSON object with an integer 'hours'.", "invalid_input")
    try:
        payload, _changed = await run_in_threadpool(set_screening_horizon_hours, body["hours"],
                                                    actor_of(principal))
    except InvalidSettingValue as exc:
        return _api_error(422, str(exc), "invalid_input")
    return payload


# ---------------------------------------------------------------------------
# Optional Telegram notifications (backend/notify.py). Credentials are
# backend environment variables only; these routes never return them.
# ADMINISTRATOR ('configure_system'); the test send is server-built text only,
# CSRF-protected (app-wide), cooldown-limited and audited.
# ---------------------------------------------------------------------------
@app.get("/api/admin/notifications/telegram", dependencies=[Depends(require_permission("configure_system"))])
def get_telegram_status():
    from backend import notify

    return notify.status()


@app.post("/api/admin/notifications/telegram/test")
def post_telegram_test(principal: Principal = Depends(require_permission("configure_system"))):
    from backend import notify

    status_code, payload = notify.send_test_notification(actor_of(principal) or {})
    return JSONResponse(status_code=status_code, content=payload)


# ---------------------------------------------------------------------------
# Protected-asset registry management (ASSET_MANAGER+). Changes take effect
# on the next successful refresh; existing events/observations are never
# touched. Every mutation writes governance_audit (before/after). Nothing in
# the AI/brief path calls these endpoints.
# ---------------------------------------------------------------------------
ASSET_EFFECTIVE = "next_successful_refresh"
ASSET_CRITICALITIES = ("Tier1", "Tier2", "Tier3")


def _ensure_assets_seeded():
    # Normally done once at startup; repeated here (a no-op once seeded) so
    # the registry is never read or modified before its one-time seed.
    from backend.db import ensure_protected_assets_seeded

    ensure_protected_assets_seeded(config.WORKING_SET_PATH)


def _api_error(status, detail, error, **extra):
    return JSONResponse(status_code=status, content={"detail": detail, "error": error, **extra})


def _validate_asset_fields(raw: dict, require_name: bool):
    """Returns cleaned fields for the keys present in `raw`; raises
    backend.auth.ValidationFailed on invalid input."""
    from backend.auth import ValidationFailed, clean_text

    out = {}
    if require_name or "name_query" in raw:
        name = clean_text(raw.get("name_query"), 64, "name_query", allow_empty=False)
        if len(name) < 2:
            raise ValidationFailed("name_query must be 2-64 characters.")
        out["name_query"] = name
    if "exact_match" in raw:
        out["exact_match"] = clean_text(raw.get("exact_match"), 64, "exact_match")
    if require_name or "criticality" in raw:
        if raw.get("criticality") not in ASSET_CRITICALITIES:
            raise ValidationFailed(f"criticality must be one of {list(ASSET_CRITICALITIES)}.")
        out["criticality"] = raw["criticality"]
    if "note" in raw:
        out["note"] = clean_text(raw.get("note"), 200, "note")
    return out


class AssetCreateBody(BaseModel):
    name_query: str | None = None
    exact_match: str | None = None
    criticality: str | None = None
    note: str | None = None


class AssetPatchBody(BaseModel):
    model_config = {"extra": "allow"}
    criticality: str | None = None
    note: str | None = None
    exact_match: str | None = None


class AssetStatusBody(BaseModel):
    status: str
    reason: str | None = None


@app.get("/api/protected-assets")
def list_protected_assets_route():
    from backend.db import list_protected_assets

    _ensure_assets_seeded()

    return {"assets": list_protected_assets(), "effective": ASSET_EFFECTIVE}


@app.get("/api/protected-assets/audit", dependencies=[Depends(require_permission("view_audit"))])
def protected_assets_audit(limit: int = Query(100, ge=1, le=1000)):
    from backend.db import list_governance_audit

    return {"entries": list_governance_audit(limit=limit, target_type="protected_asset")}


@app.post("/api/protected-assets", status_code=201)
def create_protected_asset_route(body: AssetCreateBody,
                                 principal: Principal = Depends(require_permission("manage_assets"))):
    from backend.auth import ValidationFailed
    from backend.db import DuplicateError, create_protected_asset

    _ensure_assets_seeded()

    try:
        f = _validate_asset_fields(body.model_dump(exclude_unset=True), require_name=True)
    except ValidationFailed as exc:
        return _api_error(422, str(exc), "invalid_input")
    try:
        asset = create_protected_asset(f["name_query"], f.get("exact_match"), f["criticality"], f.get("note"),
                                       actor=actor_of(principal))
    except DuplicateError as exc:
        return _api_error(409, str(exc), "duplicate")
    return {"asset": asset, "effective": ASSET_EFFECTIVE}


@app.patch("/api/protected-assets/{asset_id}")
def update_protected_asset_route(asset_id: int, body: AssetPatchBody,
                                 principal: Principal = Depends(require_permission("manage_assets"))):
    from backend.auth import ValidationFailed
    from backend.db import InvalidTransitionError, NotFoundError, update_protected_asset

    _ensure_assets_seeded()

    raw = body.model_dump(exclude_unset=True)
    unknown = sorted(set(raw) - {"criticality", "note", "exact_match"})
    if unknown:
        msg = ("name_query is immutable; retire this asset and add a new one instead."
               if "name_query" in unknown else f"Unknown field(s): {', '.join(unknown)}.")
        return _api_error(422, msg, "invalid_input")
    if not raw:
        return _api_error(422, "No changes supplied.", "invalid_input")
    try:
        changes = _validate_asset_fields(raw, require_name=False)
    except ValidationFailed as exc:
        return _api_error(422, str(exc), "invalid_input")
    try:
        asset = update_protected_asset(asset_id, changes, actor=actor_of(principal))
    except NotFoundError:
        return _api_error(404, "Protected asset not found.", "not_found")
    except InvalidTransitionError as exc:
        return _api_error(409, str(exc), "invalid_transition")
    return {"asset": asset, "effective": ASSET_EFFECTIVE}


@app.post("/api/protected-assets/{asset_id}/status")
def set_protected_asset_status_route(asset_id: int, body: AssetStatusBody,
                                     principal: Principal = Depends(require_permission("manage_assets"))):
    from backend.auth import ValidationFailed, clean_text
    from backend.db import (ASSET_STATUSES, InvalidTransitionError, LastActiveAssetError, NotFoundError,
                            set_protected_asset_status)

    _ensure_assets_seeded()

    if body.status not in ASSET_STATUSES:
        return _api_error(422, f"status must be one of {list(ASSET_STATUSES)}.", "invalid_input")
    try:
        reason = clean_text(body.reason, 200, "reason")
    except ValidationFailed as exc:
        return _api_error(422, str(exc), "invalid_input")
    try:
        asset = set_protected_asset_status(asset_id, body.status, reason, actor=actor_of(principal))
    except NotFoundError:
        return _api_error(404, "Protected asset not found.", "not_found")
    except InvalidTransitionError as exc:
        return _api_error(409, str(exc), "invalid_transition")
    except LastActiveAssetError as exc:
        return _api_error(409, str(exc), "last_active_asset")
    return {"asset": asset, "effective": ASSET_EFFECTIVE}
