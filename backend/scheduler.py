"""Scheduled catalog refresh: cadence cron "0 */2 * * *" UTC (every 2 hours,
on even UTC hours).

Modes (config.SCHEDULER_MODE, env ANTARIKSHA_SCHEDULER_MODE):
  internal (default) -- an in-process daemon thread, started on app startup,
                        sleeps until the next even UTC hour and calls
                        pipeline.run_refresh_pipeline('scheduled').
  external           -- no thread; an external cron calls
                        GET/POST /api/internal/scheduled-refresh with
                        `Authorization: Bearer <SCHEDULER_SECRET|CRON_SECRET>`.
  off                -- no scheduled refreshes (tests force this).

Every scheduled run goes through the same run_refresh_pipeline as a manual
refresh, with extra safety guards (see pipeline.scheduled_guard_reason):
skip when a successful refresh completed < 110 min ago ('too_soon'), when
the current events come from a DEMO run started < 60 min ago
('demo_active'), or when a refresh is already running
('refresh_in_progress'). Skips and failures are recorded in
refresh_attempts; failures keep the last-known-good catalog and events.
"""

import logging
import threading
from datetime import datetime, timedelta, timezone

from backend import config

logger = logging.getLogger(__name__)

_THREAD = None
_STOP = threading.Event()


def next_even_utc_hour(now=None):
    """The next cron "0 */2 * * *" fire time strictly after `now` (UTC):
    minute 0 of the next even UTC hour."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    base = now.replace(minute=0, second=0, microsecond=0)
    candidate = base + timedelta(hours=1 if base.hour % 2 else 2)
    # base.hour odd -> next hour is even; base.hour even -> +2 h (strictly after now).
    return candidate


def scheduler_mode():
    mode = (config.SCHEDULER_MODE or "off").strip().lower()
    return mode if mode in config.SCHEDULER_MODES else "off"


def scheduler_enabled():
    """True when scheduled refreshes can happen: internal mode, or external
    mode with a shared secret configured for the cron endpoint."""
    mode = scheduler_mode()
    if mode == "internal":
        return True
    if mode == "external":
        return bool(config.scheduler_secret())
    return False


def internal_thread_running():
    return _THREAD is not None and _THREAD.is_alive()


def run_scheduled_refresh(background=False):
    """Trigger one scheduled refresh through the shared pipeline. Never
    raises for pipeline failures (they are recorded as attempts)."""
    from backend import pipeline

    return pipeline.run_refresh_pipeline("scheduled", background=background)


def _loop():
    logger.info("Internal scheduler started (cron %s UTC, %s).", config.SCHEDULER_CRON,
                config.SCHEDULER_CADENCE_LABEL)
    while not _STOP.is_set():
        target = next_even_utc_hour()
        delay = max(0.0, (target - datetime.now(timezone.utc)).total_seconds())
        if _STOP.wait(delay):
            break
        try:
            result = run_scheduled_refresh()
            logger.info("Scheduled refresh attempt %s: %s%s", result.get("attempt_id"), result.get("status"),
                        f" ({result['reason']})" if result.get("reason") else "")
        except Exception:
            logger.exception("Scheduled refresh crashed; will retry at the next scheduled time.")
    logger.info("Internal scheduler stopped.")


def start_internal_scheduler():
    """Start the daemon thread when mode is 'internal' (idempotent). Returns
    the thread, or None when the mode is external/off."""
    global _THREAD
    mode = scheduler_mode()
    if mode != "internal":
        logger.info("Internal scheduler not started (ANTARIKSHA_SCHEDULER_MODE=%s).", mode)
        return None
    if internal_thread_running():
        return _THREAD
    _STOP.clear()
    _THREAD = threading.Thread(target=_loop, name="antariksha-scheduler", daemon=True)
    _THREAD.start()
    return _THREAD


def stop_internal_scheduler(timeout=5.0):
    global _THREAD
    _STOP.set()
    if _THREAD is not None:
        _THREAD.join(timeout)
    _THREAD = None


def scheduler_status():
    """Scheduler block for /api/status (no secrets)."""
    from backend import pipeline
    from backend.db import latest_refresh_attempt

    mode = scheduler_mode()
    enabled = scheduler_enabled()
    last = latest_refresh_attempt()
    return {
        "mode": mode,
        "enabled": enabled,
        "cadence": config.SCHEDULER_CRON,
        "cadence_label": config.SCHEDULER_CADENCE_LABEL,
        "next_scheduled_at": next_even_utc_hour().isoformat() if enabled else None,
        "last_successful_refresh_at": pipeline.last_successful_refresh_at(),
        "last_attempt_at": (last.get("completed_at") or last.get("started_at")) if last else None,
        "last_attempt_status": last.get("status") if last else None,
        "last_attempt_trigger": last.get("trigger") if last else None,
        "last_attempt_reason": last.get("reason") if last else None,
        "running": pipeline.refresh_in_progress(),
    }


def health_scheduler_status():
    """Reduced, non-sensitive scheduler block for public GET /api/health."""
    s = scheduler_status()
    return {k: s[k] for k in ("mode", "enabled", "next_scheduled_at", "last_successful_refresh_at",
                              "last_attempt_at", "last_attempt_status")}
