"""Constants, thresholds, and configuration for ANTARIKSHA-RAKSHA."""

import os as _os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
REPO_ROOT = BACKEND_DIR.parent
DATA_DIR = REPO_ROOT / "data"


def _env_path(name, default):
    """Deployment-friendly path override (e.g. a mounted persistent volume).
    Relative values resolve against the repository root; unset keeps the
    default."""
    raw = _os.environ.get(name, "").strip()
    if not raw:
        return default
    path = Path(raw).expanduser()
    return path if path.is_absolute() else REPO_ROOT / path


WORKING_SET_PATH = DATA_DIR / "working_set.json"
#   ANTARIKSHA_DB_PATH        SQLite file      (default data/antariksha.db)
#   ANTARIKSHA_TLE_CACHE_DIR  TLE/OMM cache    (default data/tle_cache)
DB_PATH = _env_path("ANTARIKSHA_DB_PATH", DATA_DIR / "antariksha.db")
TLE_CACHE_DIR = _env_path("ANTARIKSHA_TLE_CACHE_DIR", DATA_DIR / "tle_cache")

CELESTRAK_BASE_URL = "https://celestrak.org/NORAD/elements/gp.php"

# --- Catalog input format (OMM readiness; backend/orbital_formats.py) -------
# "tle" (default, unchanged behaviour): CelesTrak FORMAT=TLE.
# "omm": CelesTrak FORMAT=json (CCSDS OMM keywords), validated and stored as
# native OMM elements (objects.omm_json); SGP4 is initialised from them
# directly, so catalog numbers beyond the 5-digit TLE field are kept.
import os as _os  # noqa: E402  (kept local to this section)

CELESTRAK_FORMAT = _os.environ.get("ANTARIKSHA_CELESTRAK_FORMAT", "tle").strip().lower()
CELESTRAK_FORMATS = ("tle", "omm")

# --- Ingest safety (backend/ingest.py) ---------------------------------------
# A named working-set entry that does not resolve to exactly one object keeps
# its previously ingested object active (recorded as unresolved). The ingest
# is refused (catalog untouched, IngestSafetyError) when fewer than this
# fraction of Group A (operator-configured protected) entries resolve ...
INGEST_MIN_GROUP_A_RESOLVED_FRACTION = float(
    _os.environ.get("ANTARIKSHA_INGEST_MIN_GROUP_A_RESOLVED_FRACTION", "0.5"))
# ... or when reconciliation would deactivate more than this fraction of the
# currently active catalog.
INGEST_MAX_DEACTIVATE_FRACTION = float(
    _os.environ.get("ANTARIKSHA_INGEST_MAX_DEACTIVATE_FRACTION", "0.5"))

# Propagation / screening
SCREENING_THRESHOLD_KM = 10.0
# Default screening horizon. The ACTIVE horizon is admin-configurable
# (backend/settings.py, app_settings 'screening_horizon_hours', one of
# SCREENING_HORIZON_ALLOWED_HOURS); this constant is the default used while
# no setting row exists. The step is fixed (not configurable).
PROPAGATION_WINDOW_HOURS = 72
PROPAGATION_STEP_SECONDS = 60
SCREENING_HORIZON_ALLOWED_HOURS = (24, 48, 72, 96, 120)
PROXIMITY_WATCH_KM = 25.0
PROXIMITY_DWELL_MIN_STEPS = 10

# LLM (local Ollama only; no external AI provider)
# 127.0.0.1 rather than "localhost": same local Ollama service, but on
# Windows "localhost" tries IPv6 ::1 first and adds ~2 s to every call.
#   ANTARIKSHA_OLLAMA_URL    (default http://127.0.0.1:11434/api/generate)
#   ANTARIKSHA_OLLAMA_MODEL  (default llama3.2:3b)
#   ANTARIKSHA_AI_MODE       'ollama' (default) | 'disabled' -- disabled never
#                            calls the network; briefs use the deterministic
#                            template fallback immediately.
OLLAMA_URL = _os.environ.get("ANTARIKSHA_OLLAMA_URL", "").strip() or "http://127.0.0.1:11434/api/generate"
OLLAMA_MODEL = _os.environ.get("ANTARIKSHA_OLLAMA_MODEL", "").strip() or "llama3.2:3b"
OLLAMA_TIMEOUT_SECONDS = 30
AI_MODES = ("ollama", "disabled")
AI_MODE = _os.environ.get("ANTARIKSHA_AI_MODE", "ollama").strip().lower() or "ollama"
if AI_MODE not in AI_MODES:
    AI_MODE = "ollama"

# Criticality weighting (Section 4)
CRITICALITY_MULTIPLIERS = {
    "Tier1": 3.0,
    "Tier2": 2.0,
    "Tier3": 1.0,
}

# Risk scoring (Section 6.4)
PC_SIGMA_KM_BASE = 0.1
PC_SIGMA_KM_GROWTH_PER_DAY = 0.05
PC_HARD_BODY_RADIUS_KM = 0.02
PC_N_SAMPLES = 5000

RISK_TIER_CRITICAL_PC = 1e-4
RISK_TIER_HIGH_PC = 1e-5
RISK_TIER_MEDIUM_PC = 1e-6

# Status labeling only: a catalog refresh older than this is shown as STALE
# DATA on the dashboard. This is a display/labeling policy -- it does NOT
# exclude objects or change screening (per-object exclusion is governed by
# MAX_TLE_AGE_DAYS in backend/propagate.py).
CATALOG_REFRESH_STALE_HOURS = 24

# ---------------------------------------------------------------------------
# API access control + CORS (optional; backend/main.py)
# ---------------------------------------------------------------------------
# Default = local prototype mode: no API key, every endpoint open, CORS only
# for the local Vite dev server. For any shared/networked deployment set:
#   ANTARIKSHA_API_KEY=<secret>      state-changing requests (POST/PUT/PATCH/
#                                    DELETE) must send header `X-API-Key`.
#                                    Read-only GET endpoints stay open.
#   ANTARIKSHA_AUTH_REQUIRED=true    fail closed: if no key is configured,
#                                    mutating requests are refused (503)
#                                    instead of silently running unauthenticated.
#   ANTARIKSHA_CORS_ORIGINS=a,b      comma-separated allowed browser origins.
import os as _os  # noqa: E402  (re-imported so this section stands alone)

API_KEY_HEADER = "X-API-Key"
API_KEY = _os.environ.get("ANTARIKSHA_API_KEY", "").strip()
AUTH_REQUIRED = _os.environ.get("ANTARIKSHA_AUTH_REQUIRED", "").strip().lower() in ("1", "true", "yes", "on")
CORS_ORIGINS = [o.strip() for o in _os.environ.get("ANTARIKSHA_CORS_ORIGINS", "http://localhost:5173").split(",")
                if o.strip()]

# ---------------------------------------------------------------------------
# Operator accounts, sessions and role-based access control (backend/auth.py)
# ---------------------------------------------------------------------------
# Prototype RBAC, enforced by the backend. Every /api/* route needs a valid
# login session except GET /api/health and POST /api/auth/login. There are NO
# default or built-in credentials: create the first ADMINISTRATOR with
#   python -m backend.users create --username <name> --role ADMINISTRATOR
# or, once, via ANTARIKSHA_BOOTSTRAP_ADMIN_USERNAME/_PASSWORD (only used while
# the users table is empty). The optional ANTARIKSHA_API_KEY above remains an
# additional deployment gate evaluated before the session check.
#   ANTARIKSHA_SESSION_HOURS=12        session lifetime (cookie Max-Age too)
#   ANTARIKSHA_COOKIE_SECURE=true      mark the session cookie Secure (HTTPS)
SESSION_COOKIE_NAME = "antariksha_session"
SESSION_HOURS = float(_os.environ.get("ANTARIKSHA_SESSION_HOURS", "12"))
COOKIE_SECURE = _os.environ.get("ANTARIKSHA_COOKIE_SECURE", "").strip().lower() in ("1", "true", "yes", "on")
# Mutating requests must carry this header (CSRF guard: a cross-site form
# cannot set it, and a cross-origin script setting it triggers a preflight).
CSRF_HEADER = "X-Antariksha-Client"
CSRF_HEADER_VALUE = "console"
PASSWORD_MIN_LENGTH = 10
BOOTSTRAP_ADMIN_USERNAME = _os.environ.get("ANTARIKSHA_BOOTSTRAP_ADMIN_USERNAME", "").strip()
BOOTSTRAP_ADMIN_PASSWORD = _os.environ.get("ANTARIKSHA_BOOTSTRAP_ADMIN_PASSWORD", "")

# ---------------------------------------------------------------------------
# Local dynamic translation of AI briefs (optional; backend/translation.py)
# ---------------------------------------------------------------------------
# Off by default. When enabled, English briefs can be machine-translated into
# Indian languages by a LOCAL IndicTrans2 model -- never by an external API.
# The model is loaded lazily on the first non-English request and only from
# local files (no download at startup or request time). English remains the
# source of record: translated text is never written to the DB/audit trail.
#   LOCAL_TRANSLATION_ENABLED=true|false   (default false)
#   TRANSLATION_MODEL=<local dir or HF repo id already in the local HF cache>
#   TRANSLATION_DEVICE=cpu|cuda            (default cpu)
# These are read from the environment at service construction time; the
# values below are only the defaults.
LOCAL_TRANSLATION_ENABLED_DEFAULT = False
TRANSLATION_MODEL_DEFAULT = "ai4bharat/indictrans2-en-indic-dist-200M"
TRANSLATION_DEVICE_DEFAULT = "cpu"
TRANSLATION_MAX_CHARS = 6000


# ---------------------------------------------------------------------------
# Scheduled catalog refresh (backend/scheduler.py)
# ---------------------------------------------------------------------------
# Cadence: cron "0 */2 * * *" UTC (every 2 hours on even UTC hours).
#   ANTARIKSHA_SCHEDULER_MODE = internal (default: in-process daemon thread)
#                             | external (no thread; an external cron calls
#                               GET/POST /api/internal/scheduled-refresh with
#                               `Authorization: Bearer <SCHEDULER_SECRET>`)
#                             | off
#   SCHEDULER_SECRET (or CRON_SECRET, e.g. Vercel Cron) authenticates the
#   external-cron endpoint; read at request time, never logged. Unset ->
#   the endpoint answers 503 scheduler_disabled.
SCHEDULER_MODES = ("internal", "external", "off")
SCHEDULER_MODE = _os.environ.get("ANTARIKSHA_SCHEDULER_MODE", "internal").strip().lower() or "internal"
if SCHEDULER_MODE not in SCHEDULER_MODES:
    SCHEDULER_MODE = "off"
SCHEDULER_CRON = "0 */2 * * *"
SCHEDULER_CADENCE_LABEL = "every 2 hours"
# Safety: never poll CelesTrak automatically more often than ~2 h, and never
# let an automatic refresh wipe a demo the operator is presenting.
SCHEDULER_MIN_INTERVAL_MINUTES = 110
SCHEDULER_DEMO_GUARD_MINUTES = 60


def scheduler_secret():
    """The external-cron shared secret (SCHEDULER_SECRET, else CRON_SECRET),
    or '' when none is configured. Read from the environment on each call."""
    return (_os.environ.get("SCHEDULER_SECRET", "").strip()
            or _os.environ.get("CRON_SECRET", "").strip())
