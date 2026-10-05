"""Optional Telegram notifications for significant screening events.

Notification-only channel: it reports events the deterministic backend has
already computed and stored. It never approves or dismisses an assessment,
never changes orbital data or risk values, has no command path to any
spacecraft and is not part of the physics pipeline. Best-effort (no delivery
guarantee, retries or escalation); a Telegram failure never fails screening.

Configuration (backend environment only -- never Git, never Vite/Vercel):
  TELEGRAM_ENABLED       'true' to send (default off; credentials alone do
                         NOT enable sending)
  TELEGRAM_BOT_TOKEN     bot token from @BotFather (secret)
  TELEGRAM_CHAT_ID       destination chat id
  TELEGRAM_MIN_RISK      lowest tier that notifies: High (default) |
                         Critical | Medium. Low never notifies. Uses the
                         existing risk tiers; no new thresholds.
  TELEGRAM_NOTIFY_DEMO   'true' to also notify DEMO events, clearly labelled
                         as a controlled simulation (default off)
  ANTARIKSHA_PUBLIC_APP_URL  optional public console URL for the review link

Trigger point: notify_run(), called once per screening run AFTER the run's
event observations are stored (risk tier final, persistent track id known).

De-duplication (per event track from backend/event_history.py, which
correlates the same encounter across runs): a track is notified when its
current tier is notifiable AND higher than every tier already successfully
notified for that track. So: first detection -> one alert; same tier on later
refreshes -> none; escalation (High -> Critical) -> one new alert;
de-escalation -> none. A track break (encounter missing from a run, TCA jump
beyond the correlation window, demo reset) starts a new track and may alert
again. At most MAX_ALERTS_PER_RUN alerts per run; after the first failed
delivery in a run the rest wait for the next run. Attempts are recorded in
governance_audit (action 'notification'); disabled Telegram writes nothing.

Setup: docs/technical/DEPLOYMENT.md, section "Optional Telegram notifications".
"""

import json
import logging
import os
import re
import threading
import time

import requests

from backend.event_history import TIER_ORDER

logger = logging.getLogger(__name__)

CHANNEL = "telegram"
AUDIT_ACTION = "notification"
TRACK_TARGET = "event_track"
TEST_TARGET = "telegram_test"
NOTIFIER_ACTOR = {"user_id": None, "username": "notifier", "role": None}
SEND_TIMEOUT_SECONDS = 5
TEST_COOLDOWN_SECONDS = 60
# At most this many alerts per screening run (Telegram per-chat limits; the
# remaining tracks are notified at the next run).
MAX_ALERTS_PER_RUN = 5
MIN_RISK_ALLOWED = ("Medium", "High", "Critical")
DEFAULT_MIN_RISK = "High"

DEMO_HEADER = "🧪 DEMO — CONTROLLED SIMULATION\nThis is NOT an operational conjunction warning."
DISCLAIMER = "⚠️ This is a decision-support notification, not an operational maneuver command."

# Backwards-compatible module attributes (tests may monkeypatch these); the
# environment is re-read at call time when they are left empty.
TELEGRAM_BOT_TOKEN = ""
TELEGRAM_CHAT_ID = ""


# --- Configuration ----------------------------------------------------------

def _truthy(value):
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def _token():
    return TELEGRAM_BOT_TOKEN or os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()


def _chat_id():
    return TELEGRAM_CHAT_ID or os.environ.get("TELEGRAM_CHAT_ID", "").strip()


def telegram_enabled() -> bool:
    return _truthy(os.environ.get("TELEGRAM_ENABLED"))


def telegram_configured() -> bool:
    """Both credentials present (says nothing about TELEGRAM_ENABLED)."""
    return bool(_token() and _chat_id())


def min_risk_tier() -> str:
    raw = os.environ.get("TELEGRAM_MIN_RISK", "").strip().capitalize()
    return raw if raw in MIN_RISK_ALLOWED else DEFAULT_MIN_RISK


def notify_demo() -> bool:
    return _truthy(os.environ.get("TELEGRAM_NOTIFY_DEMO"))


def public_app_url():
    raw = os.environ.get("ANTARIKSHA_PUBLIC_APP_URL", "").strip().rstrip("/")
    return raw if raw.startswith("https://") else None


def is_notifiable_tier(tier) -> bool:
    return tier in TIER_ORDER and TIER_ORDER[tier] >= TIER_ORDER[min_risk_tier()]


def status():
    """Non-secret configuration summary (never the token or chat id)."""
    return {
        "channel": CHANNEL,
        "enabled": telegram_enabled(),
        "configured": telegram_configured(),
        "active": telegram_enabled() and telegram_configured(),
        "bot_token": "********" if _token() else None,
        "chat_id": "configured" if _chat_id() else None,
        "min_risk": min_risk_tier(),
        "notify_demo": notify_demo(),
        "review_link": bool(public_app_url()),
    }


# --- Secret hygiene -----------------------------------------------------------

_TOKEN_IN_URL = re.compile(r"/bot[^/\s]+/")


class _RedactBotToken(logging.Filter):
    """urllib3 logs request paths at DEBUG; the Bot API path embeds the token."""

    def filter(self, record):
        try:
            msg = record.getMessage()
        except Exception:
            return True
        if "/bot" in msg:
            record.msg, record.args = _TOKEN_IN_URL.sub("/bot<redacted>/", msg), ()
        return True


logging.getLogger("urllib3.connectionpool").addFilter(_RedactBotToken())


# --- Message ------------------------------------------------------------------

def _fmt_tca(ts):
    text = str(ts or "")
    m = re.match(r"(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})", text)
    return f"{m.group(1)} {m.group(2)} UTC" if m else (text or "N/A")


def _num(value, fmt):
    return format(value, fmt) if isinstance(value, (int, float)) else "N/A"


def build_message(event):
    """Concise alert from already-computed event facts (no new calculations).

    event keys: event_id, track_id, event_class, risk_tier, object_a_name,
    object_b_name, tca_timestamp, miss_distance_km, rel_velocity_km_s,
    pc_score, is_demo, data_source."""
    tier = str(event.get("risk_tier") or "N/A")
    collision = event.get("event_class") == "collision_risk"
    kind = "CONJUNCTION RISK" if collision else "PROXIMITY WATCH"
    lines = []
    if event.get("is_demo"):
        lines += [DEMO_HEADER, ""]
    lines += [
        f"🚨 ANTARIKSHA-RAKSHA — {tier.upper()} {kind}",
        "",
        f"Object A: {event.get('object_a_name') or 'N/A'}",
        f"Object B: {event.get('object_b_name') or 'N/A'}",
        "",
        f"TCA: {_fmt_tca(event.get('tca_timestamp'))}",
        f"{'Miss distance' if collision else 'Minimum separation'}: {_num(event.get('miss_distance_km'), '.3f')} km",
    ]
    if collision:
        lines.append(f"Relative velocity: {_num(event.get('rel_velocity_km_s'), '.2f')} km/s")
    lines += ["", f"Risk: {tier.upper()}"]
    if collision:
        lines.append(f"Pc indicator: {_num(event.get('pc_score'), '.2e')}")
    lines += [
        "",
        ("Data: controlled demo scenario (engineered orbit override of public CelesTrak data)" if event.get("is_demo")
         else f"Data: {event.get('data_source') or 'CelesTrak public GP/TLE data'}"),
        "Assessment: Simplified analytic collision-probability indicator — NOT an operational covariance-based Pc",
        f"Event #{event.get('event_id')} · track #{event.get('track_id')}",
        "",
        DISCLAIMER,
    ]
    url = public_app_url()
    if url:
        lines += ["", f"Review: {url}"]
    return "\n".join(lines)


# --- Delivery -------------------------------------------------------------------

def _http_post(*args, **kwargs):
    return requests.post(*args, **kwargs)


def send_message(text):
    """POST one message to the Bot API. Returns {"sent": bool, "status": str,
    "error": str|None}. Never raises; never puts the token in the result or
    logs (only the exception type / HTTP status)."""
    if not telegram_enabled():
        return {"sent": False, "status": "disabled", "error": None}
    token, chat_id = _token(), _chat_id()
    if not token or not chat_id:
        missing = "bot_token" if not token else "chat_id"
        logger.warning("Telegram enabled but %s is not configured; notification skipped.", missing)
        return {"sent": False, "status": "unconfigured", "error": f"missing_{missing}"}
    try:
        resp = _http_post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
            timeout=SEND_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        try:
            ok = resp.json().get("ok") is True
        except Exception:
            ok = False
        if not ok:
            logger.warning("Telegram notification rejected (unexpected Bot API response); continuing.")
            return {"sent": False, "status": "failed", "error": "unexpected_response"}
        return {"sent": True, "status": "sent", "error": None}
    except Exception as exc:
        code = getattr(getattr(exc, "response", None), "status_code", None)
        error = type(exc).__name__ + (f" HTTP {code}" if code else "")
        logger.warning("Telegram notification failed (%s); continuing without notification.", error)
        return {"sent": False, "status": "failed", "error": error}


def _audit(target_type, target_id, details, is_demo=False, actor=None):
    from backend.db import insert_governance_audit

    try:
        insert_governance_audit(actor or NOTIFIER_ACTOR, AUDIT_ACTION, target_type, target_id,
                                {"channel": CHANNEL, **details}, is_demo=is_demo)
    except Exception:
        logger.exception("Could not write the notification audit row")


def _highest_notified_tier(track_id):
    """Highest risk tier already successfully notified for this track."""
    from backend.db import get_connection

    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT details_json FROM governance_audit WHERE action = ? AND target_type = ? AND target_id = ?",
            (AUDIT_ACTION, TRACK_TARGET, str(track_id)),
        ).fetchall()
    finally:
        conn.close()
    best = None
    for r in rows:
        try:
            details = json.loads(r["details_json"])
        except (TypeError, ValueError):
            continue
        if not isinstance(details, dict) or details.get("status") != "sent":
            continue
        tier = details.get("risk_tier")
        if tier in TIER_ORDER and (best is None or TIER_ORDER[tier] > TIER_ORDER[best]):
            best = tier
    return best


def _run_events(run_id):
    from backend.db import get_connection

    conn = get_connection()
    try:
        return [dict(r) for r in conn.execute(
            """
            SELECT o.event_id, o.track_id, o.event_class, o.risk_tier, o.tca_timestamp,
                   o.miss_distance_km, o.rel_velocity_km_s, o.pc_score, o.is_demo,
                   COALESCE(a.name, o.object_a_id) AS object_a_name,
                   COALESCE(b.name, o.object_b_id) AS object_b_name
            FROM event_observations o
            LEFT JOIN objects a ON a.norad_id = o.object_a_id
            LEFT JOIN objects b ON b.norad_id = o.object_b_id
            WHERE o.screening_run_id = ? ORDER BY o.priority_score DESC, o.id ASC
            """,
            (run_id,),
        ).fetchall()]
    finally:
        conn.close()


def notify_run(run_id, data_source=None):
    """Notify significant events of one recorded screening run (see module
    docstring for the policy). Returns a summary; never raises."""
    summary = {"run_id": run_id, "sent": 0, "failed": 0, "skipped": 0, "deferred": 0, "status": "ok"}
    if not telegram_enabled():
        summary["status"] = "disabled"
        return summary
    try:
        events = _run_events(run_id)
    except Exception:
        logger.exception("Notification step could not read screening run %s", run_id)
        summary["status"] = "error"
        return summary
    stop = False
    for evt in events:
        if stop:
            break
        try:
            if not is_notifiable_tier(evt["risk_tier"]):
                summary["skipped"] += 1
                continue
            if evt["is_demo"] and not notify_demo():
                summary["skipped"] += 1
                continue
            already = _highest_notified_tier(evt["track_id"])
            if already is not None and TIER_ORDER[evt["risk_tier"]] <= TIER_ORDER[already]:
                summary["skipped"] += 1
                continue
            if summary["sent"] >= MAX_ALERTS_PER_RUN:
                summary["deferred"] += 1  # next run (highest priority first)
                continue
            result = send_message(build_message({**evt, "data_source": data_source}))
            if result["status"] in ("sent", "failed", "unconfigured"):
                _audit(TRACK_TARGET, str(evt["track_id"]), {
                    "status": result["status"], "error": result["error"], "event_id": evt["event_id"],
                    "track_id": evt["track_id"], "screening_run_id": run_id,
                    "risk_tier": evt["risk_tier"], "event_class": evt["event_class"],
                    "escalated_from": already,
                }, is_demo=bool(evt["is_demo"]))
            summary["sent" if result["sent"] else "failed"] += 1
            if not result["sent"]:
                # Bad credentials or Telegram down: one audited attempt per run,
                # never a burst that holds the pipeline lock; retried next run.
                stop = True
        except Exception:
            logger.exception("Notification step failed for one event; continuing")
            summary["failed"] += 1
    if summary["sent"] or summary["failed"]:
        logger.info("Telegram notifications for run %s: %s sent, %s failed, %s skipped, %s deferred.",
                    run_id, summary["sent"], summary["failed"], summary["skipped"], summary["deferred"])
    return summary


# --- Protected test send (ADMINISTRATOR, see backend/main.py) ---------------------

_TEST_LOCK = threading.Lock()
_last_test_at = 0.0


def send_test_notification(actor):
    """Server-built channel check (no client-supplied text). Cooldown-limited
    and audited. Returns (http_status, payload)."""
    global _last_test_at
    with _TEST_LOCK:
        now = time.monotonic()
        wait = TEST_COOLDOWN_SECONDS - (now - _last_test_at)
        if _last_test_at and wait > 0:
            return 429, {"error": "cooldown", "detail": f"Wait {int(wait) + 1} s before sending another test.",
                         "retry_after_seconds": int(wait) + 1}
        _last_test_at = now
    text = "\n".join([
        "🧪 TEST — ANTARIKSHA-RAKSHA notification channel check",
        "",
        "This is not an event and not an operational warning.",
        f"Requested by an administrator ({actor.get('username') or 'unknown'}).",
        "",
        DISCLAIMER,
    ] + (["", f"Console: {public_app_url()}"] if public_app_url() else []))
    result = send_message(text)
    _audit(TEST_TARGET, None, {"status": result["status"], "error": result["error"]}, actor=actor)
    return 200, {**result, "telegram": status()}
