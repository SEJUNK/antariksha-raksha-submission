"""SQLite schema + CRUD helpers (blueprint Section 5). Plain functions, parameterized queries."""

import json
import sqlite3
from datetime import datetime, timezone

from backend.config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS objects (
  norad_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  object_type TEXT NOT NULL CHECK(object_type IN ('satellite','debris','foreign_sat')),
  owner_country TEXT,
  criticality TEXT CHECK(criticality IN ('Tier1','Tier2','Tier3') OR criticality IS NULL),
  tle_line1 TEXT NOT NULL,
  tle_line2 TEXT NOT NULL,
  last_updated TEXT NOT NULL,
  -- 1 if the object is in the current working set (returned by the most
  -- recent successful ingest), 0 if it has dropped out. Inactive rows are
  -- kept, never deleted, so historical events/decisions still resolve
  -- names; screening and the globe use active rows only.
  active INTEGER NOT NULL DEFAULT 1,
  -- 'tle' (tle_line1/2 hold the CelesTrak lines) or 'omm' (omm_json holds
  -- the validated CCSDS OMM mean elements SGP4 is initialised from; the TLE
  -- columns are '' -- no TLE is ever generated for an OMM object, and code
  -- dispatches on source_format, never on the line contents).
  source_format TEXT NOT NULL DEFAULT 'tle',
  omm_json TEXT
);

CREATE TABLE IF NOT EXISTS conjunction_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  object_a_id TEXT NOT NULL REFERENCES objects(norad_id),
  object_b_id TEXT NOT NULL REFERENCES objects(norad_id),
  event_class TEXT NOT NULL CHECK(event_class IN ('collision_risk','proximity_watch')),
  tca_timestamp TEXT NOT NULL,
  miss_distance_km REAL NOT NULL,
  rel_velocity_km_s REAL,
  pc_score REAL NOT NULL,
  risk_tier TEXT NOT NULL CHECK(risk_tier IN ('Low','Medium','High','Critical')),
  priority_score REAL NOT NULL,
  -- 1 if this event was produced by a demo screening run that applied a
  -- disclosed demo TLE override (backend/demo_seed.py, demo_overrides
  -- table), 0 for events from real ingested TLEs. Exposed via the
  -- provenance endpoint so the UI never presents a demo event as real.
  is_demo INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  screening_run_id INTEGER,
  pc_samples INTEGER,
  pc_sigma_km REAL,
  pc_hard_body_radius_km REAL,
  pc_method TEXT,
  tca_refinement TEXT
);

CREATE TABLE IF NOT EXISTS mission_briefs (
  event_id INTEGER PRIMARY KEY REFERENCES conjunction_events(id),
  brief_text TEXT NOT NULL,
  maneuver_text TEXT NOT NULL,
  delta_v_ms REAL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','dismissed')),
  reviewer_notes TEXT,
  generated_by TEXT NOT NULL DEFAULT 'llm' CHECK(generated_by IN ('llm','fallback_template')),
  -- 'consistent' | 'flagged' | 'skipped' | 'not_applicable'; NULL for rows
  -- written before review_status existed.
  review_status TEXT
);

-- Persistent operator decision audit trail. Unlike conjunction_events /
-- mission_briefs (rebuilt on every screening run), rows here are kept
-- across runs: they feed the analytics dashboard and the
-- rejection-feedback loop that steers future brief generation.
CREATE TABLE IF NOT EXISTS decision_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_class TEXT NOT NULL,
  object_a_name TEXT,
  object_b_name TEXT,
  risk_tier TEXT,
  pc_score REAL,
  miss_distance_km REAL,
  generated_by TEXT,
  decision TEXT NOT NULL CHECK(decision IN ('approved','dismissed')),
  rejection_reason TEXT,
  decided_at TEXT NOT NULL,
  event_id INTEGER,
  is_demo INTEGER,
  screening_run_id INTEGER,
  tca_timestamp TEXT
);

-- Demo-only TLE overrides (backend/demo_seed.py). Kept separate from
-- `objects` so a demo never overwrites real CelesTrak data: only the demo
-- pipeline applies these, and a normal screening clears them.
CREATE TABLE IF NOT EXISTS demo_overrides (
  norad_id TEXT PRIMARY KEY,
  tle_line1 TEXT NOT NULL,
  tle_line2 TEXT NOT NULL,
  scenario TEXT NOT NULL,
  derived_from_norad_id TEXT,
  created_at TEXT NOT NULL
);

-- One row per catalog ingest (persistent run provenance).
CREATE TABLE IF NOT EXISTS ingest_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  completed_at TEXT NOT NULL,
  source TEXT,
  used_cache INTEGER NOT NULL DEFAULT 0,
  object_count INTEGER,
  counts_json TEXT,
  -- 'ok' (catalog reconciled) or 'failed' (ingest safety check refused to
  -- reconcile; catalog untouched). NULL on legacy rows = 'ok'.
  status TEXT,
  source_format TEXT,
  resolved_count INTEGER,
  unresolved_count INTEGER,
  -- [{group, name_query, reason, kept_norad_id}] for named entries that
  -- did not resolve to exactly one object.
  unresolved_json TEXT,
  -- {"A:<name_query>": norad_id} for every named entry resolved (or kept)
  -- in this run; the next run keeps an unresolved entry's object active.
  resolution_json TEXT,
  rejected_records_json TEXT,
  failure_reason TEXT
);

-- One row per screening pass (persistent run provenance).
CREATE TABLE IF NOT EXISTS screening_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT NOT NULL,
  completed_at TEXT,
  mode TEXT NOT NULL CHECK(mode IN ('live','cached','demo')),
  data_source TEXT,
  ingest_run_id INTEGER,
  object_count INTEGER,
  objects_screened INTEGER,
  objects_skipped_stale INTEGER,
  counts_by_type_json TEXT,
  tle_age_min_days REAL,
  tle_age_max_days REAL,
  tle_age_avg_days REAL,
  window_hours REAL,
  step_seconds REAL,
  screening_threshold_km REAL,
  proximity_watch_km REAL,
  candidate_pairs INTEGER,
  collision_events INTEGER,
  proximity_events INTEGER,
  demo_scenario_json TEXT,
  -- 1 once this run's event_observations were recorded (backend/event_history.py);
  -- NULL for runs before that feature or runs that failed before recording.
  observations_recorded INTEGER,
  -- Previous recorded run of the same domain this run was correlated against.
  correlation_baseline_run_id INTEGER
);

-- Persistent per-run event observations (backend/event_history.py). Unlike
-- conjunction_events (rebuilt every screening run), rows here are kept so
-- an event's evolution across runs and refresh deltas can be shown. One row
-- per event per recorded screening run; numeric values only, no AI text.
-- event_id is the conjunction_events.id at write time (ids are never reused,
-- AUTOINCREMENT) and deliberately has no FK because events are rebuilt.
-- track_id is the id of the first observation in a correlation chain
-- (prototype matching rule, not an authoritative event identity).
CREATE TABLE IF NOT EXISTS event_observations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  screening_run_id INTEGER NOT NULL REFERENCES screening_runs(id),
  event_id INTEGER,
  track_id INTEGER,
  prev_observation_id INTEGER REFERENCES event_observations(id),
  ambiguous_match INTEGER NOT NULL DEFAULT 0,
  object_a_id TEXT NOT NULL,
  object_b_id TEXT NOT NULL,
  pair_key TEXT NOT NULL,
  event_class TEXT NOT NULL,
  tca_timestamp TEXT NOT NULL,
  miss_distance_km REAL NOT NULL,
  rel_velocity_km_s REAL,
  pc_score REAL NOT NULL,
  risk_tier TEXT NOT NULL,
  priority_score REAL NOT NULL,
  dwell_minutes REAL,
  is_demo INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_event_observations_run ON event_observations(screening_run_id);
CREATE INDEX IF NOT EXISTS ix_event_observations_track ON event_observations(track_id);
CREATE INDEX IF NOT EXISTS ix_event_observations_event ON event_observations(event_id);

-- Small key/value store for catalog bookkeeping (e.g. first_seen_baseline:
-- objects whose first_seen is later than this were new to the LOCAL catalog).
CREATE TABLE IF NOT EXISTS catalog_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

-- Operator review acknowledgements for objects that were new to the local
-- catalog (append-only audit; a later row supersedes an earlier one).
CREATE TABLE IF NOT EXISTS object_reviews (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  norad_id TEXT NOT NULL,
  reviewed_at TEXT NOT NULL,
  note TEXT
);
CREATE INDEX IF NOT EXISTS ix_object_reviews_norad ON object_reviews(norad_id);

-- Operator accounts (backend/auth.py). password_hash is a PBKDF2-SHA256
-- string and is never returned by any API. No default rows are ever created.
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL UNIQUE COLLATE NOCASE,
  display_name TEXT,
  role TEXT NOT NULL CHECK(role IN ('VIEWER','OPERATOR','ASSET_MANAGER','ADMINISTRATOR')),
  password_hash TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  created_by TEXT,
  updated_at TEXT,
  last_login_at TEXT
);

-- Login sessions. Only the SHA-256 of the session token is stored.
CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  revoked_at TEXT,
  last_seen_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_sessions_user ON sessions(user_id);

-- Append-only governance audit: logins, user administration, protected-asset
-- registry changes. Never updated or deleted (enforced by triggers).
CREATE TABLE IF NOT EXISTS governance_audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  at TEXT NOT NULL,
  actor_user_id INTEGER,
  actor_username TEXT,
  actor_role TEXT,
  action TEXT NOT NULL,
  target_type TEXT,
  target_id TEXT,
  details_json TEXT,
  is_demo INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_governance_audit_target ON governance_audit(target_type, id);
CREATE TRIGGER IF NOT EXISTS governance_audit_no_update BEFORE UPDATE ON governance_audit
BEGIN SELECT RAISE(ABORT, 'governance_audit is append-only'); END;
CREATE TRIGGER IF NOT EXISTS governance_audit_no_delete BEFORE DELETE ON governance_audit
BEGIN SELECT RAISE(ABORT, 'governance_audit is append-only'); END;

-- Operator-managed protected-asset registry (Group A). Seeded ONCE from
-- data/working_set.json group_a (ensure_protected_assets_seeded); afterwards
-- this table is authoritative and ingest reads status='active' rows.
CREATE TABLE IF NOT EXISTS protected_assets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name_query TEXT NOT NULL UNIQUE COLLATE NOCASE,
  exact_match TEXT,
  criticality TEXT NOT NULL CHECK(criticality IN ('Tier1','Tier2','Tier3')),
  note TEXT,
  status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','suspended','retired')),
  created_at TEXT NOT NULL,
  created_by TEXT,
  updated_at TEXT,
  updated_by TEXT
);

-- Admin-configurable system settings (backend/settings.py). A missing row
-- means "use the default"; every change is written to governance_audit.
CREATE TABLE IF NOT EXISTS app_settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT,
  updated_by TEXT
);

-- One row per catalog-refresh attempt, manual or scheduled
-- (pipeline.run_refresh_pipeline). Failed/refused attempts leave the
-- last-known-good catalog and events untouched.
CREATE TABLE IF NOT EXISTS refresh_attempts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  "trigger" TEXT NOT NULL CHECK("trigger" IN ('manual','scheduled')),
  started_at TEXT NOT NULL,
  completed_at TEXT,
  status TEXT NOT NULL CHECK(status IN ('running','success','failed','refused','skipped')),
  reason TEXT,
  ingest_run_id INTEGER,
  screening_run_id INTEGER,
  horizon_hours INTEGER,
  actor_username TEXT,
  actor_role TEXT
);
"""

# Columns added after the original schema. CREATE TABLE IF NOT EXISTS won't
# add columns to an existing table, so older DBs are migrated on startup.
_ADDED_COLUMNS = {
    # Existing rows migrate as active=1, i.e. unchanged behaviour until the
    # next successful ingest reconciles the working set.
    "objects": {
        "active": "INTEGER NOT NULL DEFAULT 1",
        # Existing rows are TLE rows (the only format stored before OMM
        # support); additive migration, no table rebuild.
        "source_format": "TEXT NOT NULL DEFAULT 'tle'",
        "omm_json": "TEXT",
        # When the object first entered the LOCAL catalog. Set on insert and
        # never updated; rows that existed before this column get the
        # catalog baseline time (see _ensure_first_seen_baseline).
        "first_seen": "TEXT",
    },
    # Legacy ingest rows migrate as status NULL = 'ok'.
    "ingest_runs": {
        "status": "TEXT",
        "source_format": "TEXT",
        "resolved_count": "INTEGER",
        "unresolved_count": "INTEGER",
        "unresolved_json": "TEXT",
        "resolution_json": "TEXT",
        "rejected_records_json": "TEXT",
        "failure_reason": "TEXT",
    },
    "conjunction_events": {
        "is_demo": "INTEGER NOT NULL DEFAULT 0",
        "screening_run_id": "INTEGER",
        "pc_samples": "INTEGER",
        "pc_sigma_km": "REAL",
        "pc_hard_body_radius_km": "REAL",
        "tca_refinement": "TEXT",
        "pc_method": "TEXT",
    },
    "mission_briefs": {
        "review_status": "TEXT",
    },
    "decision_log": {
        "event_id": "INTEGER",
        "is_demo": "INTEGER",
        "screening_run_id": "INTEGER",
        "tca_timestamp": "TEXT",
        # Authenticated principal who decided (NULL on legacy rows).
        "actor_user_id": "INTEGER",
        "actor_username": "TEXT",
        "actor_role": "TEXT",
    },
    "object_reviews": {
        "actor_user_id": "INTEGER",
        "actor_username": "TEXT",
        "actor_role": "TEXT",
    },
    # Legacy runs migrate as NULL = "no observations recorded" (never used
    # as a correlation baseline).
    "screening_runs": {
        "observations_recorded": "INTEGER",
        "correlation_baseline_run_id": "INTEGER",
        # 'manual' | 'scheduled' for refresh screenings; NULL for demo runs
        # and runs recorded before this column existed.
        "trigger": "TEXT",
    },
}


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _ensure_columns(conn, table, columns) -> None:
    """ALTER TABLE ADD COLUMN for each {name: ddl} missing from `table`.
    Safe to run every startup -- no-op once the columns exist. Table/column
    names come from _ADDED_COLUMNS, never from user input."""
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, ddl in columns.items():
        if name not in existing:
            conn.execute(f'ALTER TABLE {table} ADD COLUMN "{name}" {ddl}')


def init_db() -> None:
    conn = get_connection()
    try:
        conn.executescript(SCHEMA)
        for table, columns in _ADDED_COLUMNS.items():
            _ensure_columns(conn, table, columns)
        if conn.execute("SELECT COUNT(*) FROM objects").fetchone()[0]:
            # Existing catalog: everything already stored is the baseline.
            _ensure_first_seen_baseline(conn, _now_iso())
        conn.commit()
    finally:
        conn.close()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _actor_columns(actor):
    """(actor_user_id, actor_username, actor_role) for an audit row. `actor`
    is a dict with user_id/username/role (or None = unattributed)."""
    if not actor:
        return None, None, None
    return actor.get("user_id"), actor.get("username"), actor.get("role")


def _ensure_first_seen_baseline(conn, now) -> str:
    """Return the catalog's first_seen baseline, creating it at `now` if
    missing. Objects present at the baseline (existing rows, or the first
    population of an empty catalog) are not "new"; any object whose
    first_seen is later entered the local catalog afterwards. Rows without
    first_seen (stored before the column existed) get the baseline."""
    row = conn.execute("SELECT value FROM catalog_meta WHERE key = 'first_seen_baseline'").fetchone()
    if row:
        return row[0]
    conn.execute("INSERT INTO catalog_meta (key, value) VALUES ('first_seen_baseline', ?)", (now,))
    conn.execute("UPDATE objects SET first_seen = ? WHERE first_seen IS NULL", (now,))
    return now


_UPSERT_OBJECT_SQL = """
    INSERT INTO objects (norad_id, name, object_type, owner_country, criticality,
                          tle_line1, tle_line2, last_updated, active, source_format, omm_json,
                          first_seen)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
    ON CONFLICT(norad_id) DO UPDATE SET
        name=excluded.name,
        object_type=excluded.object_type,
        owner_country=excluded.owner_country,
        criticality=excluded.criticality,
        tle_line1=excluded.tle_line1,
        tle_line2=excluded.tle_line2,
        last_updated=excluded.last_updated,
        active=1,
        source_format=excluded.source_format,
        omm_json=excluded.omm_json
"""


class CatalogDeactivationRefused(ValueError):
    """reconcile_catalog would deactivate more of the active catalog than
    the caller allows; the transaction was rolled back (nothing changed)."""


def _orbital_columns(source_format, tle_line1, tle_line2, omm):
    """(tle_line1, tle_line2, source_format, omm_json) for one object row.
    TLE rows need both lines; OMM rows need the OMM elements and store ''
    in the (NOT NULL) TLE columns -- never a generated TLE."""
    source_format = source_format or "tle"
    if source_format == "tle":
        if not tle_line1 or not tle_line2:
            raise ValueError("TLE object requires tle_line1 and tle_line2")
        return tle_line1, tle_line2, "tle", None
    if source_format == "omm":
        if not omm:
            raise ValueError("OMM object requires its OMM elements")
        omm_json = omm if isinstance(omm, str) else json.dumps(omm, sort_keys=True)
        return "", "", "omm", omm_json
    raise ValueError(f"Unknown source_format {source_format!r}")


def _object_row(o, now):
    orbital = _orbital_columns(o.get("source_format"), o.get("tle_line1"), o.get("tle_line2"),
                               o.get("omm") if o.get("omm") is not None else o.get("omm_json"))
    return (o["norad_id"], o["name"], o["object_type"], o["owner_country"], o["criticality"],
            orbital[0], orbital[1], now, orbital[2], orbital[3], now)


def upsert_object(norad_id, name, object_type, owner_country, criticality, tle_line1, tle_line2,
                  source_format="tle", omm=None):
    """Insert/update one object and mark it active. Does not deactivate
    anything else -- whole-catalog reconciliation is reconcile_catalog().
    OMM objects pass source_format='omm', omm=<fields> and no TLE lines."""
    conn = get_connection()
    try:
        now = _now_iso()
        _ensure_first_seen_baseline(conn, now)
        conn.execute(
            _UPSERT_OBJECT_SQL,
            _object_row({"norad_id": norad_id, "name": name, "object_type": object_type,
                         "owner_country": owner_country, "criticality": criticality,
                         "tle_line1": tle_line1, "tle_line2": tle_line2,
                         "source_format": source_format, "omm": omm}, now),
        )
        conn.commit()
    finally:
        conn.close()


def reconcile_catalog(objects, keep_active_ids=(), max_deactivate_fraction=None):
    """Make `objects` (the complete working set from one successful ingest)
    the current catalog, in a single transaction: upsert each one as
    active=1 and mark every other stored object active=0. A reappearing
    object is simply upserted active again. Nothing is deleted, so
    historical events/decisions keep resolving names.

    keep_active_ids: stored objects to keep active although they are not in
    `objects` (named working-set entries that did not resolve this time;
    their previous elements are kept, never overwritten).
    max_deactivate_fraction: if set, raises CatalogDeactivationRefused
    (transaction rolled back, nothing changed) when more than this fraction
    of the currently active objects would be deactivated.

    Refuses an empty set -- "no data" is a failed ingest and must never
    deactivate the catalog. Returns (n_active, n_deactivated)."""
    if not objects:
        raise ValueError("reconcile_catalog called with an empty working set; refusing to deactivate the catalog")
    now = _now_iso()
    conn = get_connection()
    try:
        with conn:  # one transaction: commits on success, rolls back on any error
            n_active_before = conn.execute("SELECT COUNT(*) FROM objects WHERE active = 1").fetchone()[0]
            _ensure_first_seen_baseline(conn, now)
            conn.executemany(_UPSERT_OBJECT_SQL, [_object_row(o, now) for o in objects])
            ids = sorted({o["norad_id"] for o in objects})
            kept = sorted(set(keep_active_ids) - set(ids))
            # Temp table instead of a giant IN (...) list (SQLite caps bound
            # parameters per statement).
            conn.execute("CREATE TEMP TABLE IF NOT EXISTS _ingest_ids (norad_id TEXT PRIMARY KEY)")
            conn.execute("DELETE FROM _ingest_ids")
            conn.executemany("INSERT INTO _ingest_ids (norad_id) VALUES (?)", [(i,) for i in ids + kept])
            cur = conn.execute(
                "UPDATE objects SET active = 0 WHERE active != 0 "
                "AND norad_id NOT IN (SELECT norad_id FROM _ingest_ids)"
            )
            deactivated = cur.rowcount
            conn.execute("DELETE FROM _ingest_ids")
            if (max_deactivate_fraction is not None and n_active_before
                    and deactivated / n_active_before > max_deactivate_fraction):
                # Raising inside `with conn` rolls the whole transaction back.
                raise CatalogDeactivationRefused(
                    f"Ingest would deactivate {deactivated} of {n_active_before} active objects "
                    f"(> {max_deactivate_fraction:.0%} allowed); catalog left unchanged."
                )
        return len(ids), deactivated
    finally:
        conn.close()


def list_objects(include_inactive=False):
    """Current working set (active objects) -- what screening, the globe and
    status counts use. include_inactive=True also returns objects that have
    dropped out of the catalog, for historical lookups (e.g. resolving the
    names of objects in an older event or decision)."""
    conn = get_connection()
    try:
        if include_inactive:
            rows = conn.execute("SELECT * FROM objects").fetchall()
        else:
            rows = conn.execute("SELECT * FROM objects WHERE active = 1").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def insert_event(object_a_id, object_b_id, event_class, tca_timestamp, miss_distance_km,
                  rel_velocity_km_s, pc_score, risk_tier, priority_score, is_demo=False,
                  screening_run_id=None, pc_samples=None, pc_sigma_km=None,
                  pc_hard_body_radius_km=None, tca_refinement=None, pc_method=None) -> int:
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            INSERT INTO conjunction_events
                (object_a_id, object_b_id, event_class, tca_timestamp, miss_distance_km,
                 rel_velocity_km_s, pc_score, risk_tier, priority_score, is_demo, created_at,
                 screening_run_id, pc_samples, pc_sigma_km, pc_hard_body_radius_km, tca_refinement,
                 pc_method)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (object_a_id, object_b_id, event_class, tca_timestamp, miss_distance_km,
             rel_velocity_km_s, pc_score, risk_tier, priority_score, int(is_demo), _now_iso(),
             screening_run_id, pc_samples, pc_sigma_km, pc_hard_body_radius_km, tca_refinement,
             pc_method),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def list_events():
    """Events sorted by priority_score DESC (collision + proximity interleaved by score)."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM conjunction_events ORDER BY priority_score DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_event_with_brief(event_id):
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT ce.*, mb.brief_text, mb.maneuver_text, mb.delta_v_ms, mb.status,
                   mb.reviewer_notes, mb.generated_by, mb.review_status
            FROM conjunction_events ce
            LEFT JOIN mission_briefs mb ON mb.event_id = ce.id
            WHERE ce.id = ?
            """,
            (event_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def insert_brief(event_id, brief_text, maneuver_text, delta_v_ms, generated_by, reviewer_notes=None,
                 review_status=None):
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO mission_briefs
                (event_id, brief_text, maneuver_text, delta_v_ms, status, reviewer_notes, generated_by,
                 review_status)
            -- A decision made while the brief was still generating lives only
            -- in decision_log; inherit it so the brief doesn't reappear as
            -- pending (and a repeat decision isn't logged twice).
            VALUES (?, ?, ?, ?,
                    COALESCE((SELECT decision FROM decision_log WHERE event_id = ?
                              ORDER BY id DESC LIMIT 1), 'pending'),
                    ?, ?, ?)
            ON CONFLICT(event_id) DO UPDATE SET
                brief_text=excluded.brief_text,
                maneuver_text=excluded.maneuver_text,
                delta_v_ms=excluded.delta_v_ms,
                reviewer_notes=excluded.reviewer_notes,
                generated_by=excluded.generated_by,
                review_status=excluded.review_status
            """,
            (event_id, brief_text, maneuver_text, delta_v_ms, event_id, reviewer_notes, generated_by,
             review_status),
        )
        conn.commit()
    finally:
        conn.close()


def clear_events():
    """Wipe conjunction_events + mission_briefs before a fresh /api/refresh
    screening pass -- events are re-derived each run, not accumulated."""
    conn = get_connection()
    try:
        conn.execute("DELETE FROM mission_briefs")
        conn.execute("DELETE FROM conjunction_events")
        conn.commit()
    finally:
        conn.close()


def set_brief_status(event_id, status):
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE mission_briefs SET status = ? WHERE event_id = ?",
            (status, event_id),
        )
        conn.commit()
        return get_event_with_brief(event_id)
    finally:
        conn.close()


def transition_brief_status(event_id, status):
    """Idempotent operator decision: move the event's brief to `status`
    ('approved'/'dismissed') only if it is not already there. Returns
    (event_with_brief, changed), or (None, False) if the event does not
    exist. `changed` tells the caller whether to append a decision_log row,
    so a retried/double-clicked approve or dismiss never duplicates the
    audit trail, while a genuine approved<->dismissed change is still
    recorded (both directions remain allowed, as before).

    The check-and-set is a single conditional UPDATE inside a write
    transaction, so two concurrent identical requests cannot both see
    "changed". If the brief row does not exist yet (event inserted, brief
    still generating) there is no stored status; the latest decision_log
    entry for this event is the current state instead."""
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT 1 FROM conjunction_events WHERE id = ?", (event_id,)).fetchone() is None:
            conn.rollback()
            return None, False
        cur = conn.execute(
            "UPDATE mission_briefs SET status = ? WHERE event_id = ? AND status != ?",
            (status, event_id, status),
        )
        if cur.rowcount:
            changed = True
        elif conn.execute("SELECT 1 FROM mission_briefs WHERE event_id = ?", (event_id,)).fetchone():
            changed = False  # already in the requested state
        else:
            last = conn.execute(
                "SELECT decision FROM decision_log WHERE event_id = ? ORDER BY id DESC LIMIT 1",
                (event_id,),
            ).fetchone()
            changed = last is None or last["decision"] != status
        conn.commit()
    finally:
        conn.close()
    return get_event_with_brief(event_id), changed


def insert_decision(event_class, object_a_name, object_b_name, risk_tier, pc_score,
                     miss_distance_km, generated_by, decision, rejection_reason=None,
                     event_id=None, is_demo=None, screening_run_id=None, tca_timestamp=None,
                     actor=None):
    """`actor` = the authenticated principal ({user_id, username, role}) from
    the request session -- never from the request body. None = unattributed
    (legacy rows and direct calls stay NULL)."""
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO decision_log
                (event_class, object_a_name, object_b_name, risk_tier, pc_score,
                 miss_distance_km, generated_by, decision, rejection_reason, decided_at,
                 event_id, is_demo, screening_run_id, tca_timestamp,
                 actor_user_id, actor_username, actor_role)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (event_class, object_a_name, object_b_name, risk_tier, pc_score,
             miss_distance_km, generated_by, decision, rejection_reason, _now_iso(),
             event_id, None if is_demo is None else int(bool(is_demo)), screening_run_id, tca_timestamp,
             *_actor_columns(actor)),
        )
        conn.commit()
    finally:
        conn.close()


def decision_stats():
    """Aggregate accept/reject counts plus the recent rejection details,
    for the analytics dashboard."""
    conn = get_connection()
    try:
        total = conn.execute("SELECT COUNT(*) AS n FROM decision_log").fetchone()["n"]
        approved = conn.execute(
            "SELECT COUNT(*) AS n FROM decision_log WHERE decision = 'approved'"
        ).fetchone()["n"]
        dismissed = total - approved
        recent_rejections = [dict(r) for r in conn.execute(
            """
            SELECT object_a_name, object_b_name, event_class, risk_tier,
                   rejection_reason, decided_at, is_demo
            FROM decision_log
            WHERE decision = 'dismissed'
            ORDER BY id DESC LIMIT 10
            """
        ).fetchall()]
        return {
            "total": total,
            "approved": approved,
            "dismissed": dismissed,
            "acceptance_rate": round(approved / total, 3) if total else None,
            "recent_rejections": recent_rejections,
        }
    finally:
        conn.close()


def list_all_decisions():
    """Full decision_log, newest first -- for CSV export of the audit trail."""
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM decision_log ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def decisions_for_event(event_id):
    """Operator decisions recorded for one event, newest first."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM decision_log WHERE event_id = ? ORDER BY id DESC", (event_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def recent_rejection_reasons(limit=3):
    """Most recent operator rejection reasons, newest first -- injected into
    the brief-generation prompt so the agent adapts to operator feedback."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT rejection_reason FROM decision_log
            WHERE decision = 'dismissed' AND rejection_reason IS NOT NULL
              AND TRIM(rejection_reason) != ''
            ORDER BY id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [r["rejection_reason"] for r in rows]
    finally:
        conn.close()


# --- Demo overrides ---------------------------------------------------------

def set_demo_override(norad_id, tle_line1, tle_line2, scenario, derived_from_norad_id=None):
    """Store the demo TLE for one object, replacing any previous override
    (one active demo scenario at a time). Never touches `objects`."""
    conn = get_connection()
    try:
        conn.execute("DELETE FROM demo_overrides")
        conn.execute(
            """
            INSERT INTO demo_overrides
                (norad_id, tle_line1, tle_line2, scenario, derived_from_norad_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (norad_id, tle_line1, tle_line2, scenario, derived_from_norad_id, _now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def clear_demo_overrides():
    conn = get_connection()
    try:
        conn.execute("DELETE FROM demo_overrides")
        conn.commit()
    finally:
        conn.close()


def list_demo_overrides():
    conn = get_connection()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM demo_overrides").fetchall()]
    finally:
        conn.close()


def apply_demo_overrides(objects, overrides):
    """Return copies of `objects` with override TLEs applied; overridden rows
    carry demo_adjusted=True plus the scenario metadata."""
    by_id = {o["norad_id"]: o for o in overrides}
    result = []
    for obj in objects:
        ov = by_id.get(obj["norad_id"])
        if ov is None:
            result.append(dict(obj))
            continue
        result.append({
            **{k: v for k, v in obj.items() if k != "omm"},
            "tle_line1": ov["tle_line1"],
            "tle_line2": ov["tle_line2"],
            # The disclosed demo override is always TLE lines, so the
            # adjusted copy propagates from them even if the catalog object
            # was ingested as OMM.
            "source_format": "tle",
            "omm_json": None,
            "demo_adjusted": True,
            "demo_scenario": ov["scenario"],
            "demo_derived_from_norad_id": ov["derived_from_norad_id"],
        })
    return result


def list_objects_with_demo_overrides(include_inactive=False):
    """Real catalog with any active demo overrides applied (demo_adjusted=True
    on those rows). Overrides exist only while the current events come from a
    demo run -- a normal screening clears them. Active objects only unless
    include_inactive=True (see list_objects)."""
    return apply_demo_overrides(list_objects(include_inactive=include_inactive), list_demo_overrides())


# --- Run provenance ---------------------------------------------------------

def _json_or_null(value):
    return None if value is None else json.dumps(value)


def insert_ingest_run(source, used_cache, object_count, counts, status="ok", source_format=None,
                      resolved_count=None, unresolved=None, resolution=None, rejected_records=None,
                      failure_reason=None) -> int:
    """One ingest_runs row. status='failed' rows record an ingest that a
    safety check refused to reconcile (catalog untouched); they are never
    returned by latest_ingest_run()."""
    if status not in ("ok", "failed"):
        raise ValueError(f"Unknown ingest status {status!r}")
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            INSERT INTO ingest_runs (completed_at, source, used_cache, object_count, counts_json,
                                     status, source_format, resolved_count, unresolved_count,
                                     unresolved_json, resolution_json, rejected_records_json,
                                     failure_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (_now_iso(), source, int(bool(used_cache)), object_count, _json_or_null(counts),
             status, source_format, resolved_count,
             None if unresolved is None else len(unresolved),
             _json_or_null(unresolved), _json_or_null(resolution), _json_or_null(rejected_records),
             failure_reason),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _fetch_one(sql, params=()):
    conn = get_connection()
    try:
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def latest_ingest_run():
    """Latest successful ingest (the one the active catalog came from)."""
    return _fetch_one(
        "SELECT * FROM ingest_runs WHERE status IS NULL OR status = 'ok' ORDER BY id DESC LIMIT 1"
    )


def latest_failed_ingest_run():
    """Latest refused ingest newer than the latest successful one, or None."""
    return _fetch_one(
        "SELECT * FROM ingest_runs WHERE status = 'failed' AND id > "
        "COALESCE((SELECT MAX(id) FROM ingest_runs WHERE status IS NULL OR status = 'ok'), 0) "
        "ORDER BY id DESC LIMIT 1"
    )


def get_ingest_run(run_id):
    if run_id is None:
        return None
    return _fetch_one("SELECT * FROM ingest_runs WHERE id = ?", (run_id,))


_SCREENING_RUN_FIELDS = frozenset((
    "completed_at", "data_source", "ingest_run_id", "object_count", "objects_screened",
    "objects_skipped_stale", "counts_by_type_json", "tle_age_min_days", "tle_age_max_days",
    "tle_age_avg_days", "window_hours", "step_seconds", "screening_threshold_km",
    "proximity_watch_km", "candidate_pairs", "collision_events", "proximity_events",
    "demo_scenario_json", "observations_recorded", "correlation_baseline_run_id", "trigger",
))


def _quoted(column):
    # Column names come from _SCREENING_RUN_FIELDS (never user input); quoted
    # because "trigger" is an SQL keyword.
    return f'"{column}"'


def _check_screening_fields(fields):
    unknown = set(fields) - _SCREENING_RUN_FIELDS
    if unknown:
        raise ValueError(f"Unknown screening_runs fields: {sorted(unknown)}")


def insert_screening_run(started_at, mode, **fields) -> int:
    _check_screening_fields(fields)
    cols = ["started_at", "mode", *fields.keys()]
    conn = get_connection()
    try:
        cur = conn.execute(
            f"INSERT INTO screening_runs ({', '.join(_quoted(c) for c in cols)}) "
            f"VALUES ({', '.join('?' * len(cols))})",
            (started_at, mode, *fields.values()),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def update_screening_run(run_id, **fields) -> None:
    _check_screening_fields(fields)
    if not fields:
        return
    conn = get_connection()
    try:
        conn.execute(
            f"UPDATE screening_runs SET {', '.join(_quoted(k) + ' = ?' for k in fields)} WHERE id = ?",
            (*fields.values(), run_id),
        )
        conn.commit()
    finally:
        conn.close()


def latest_screening_run():
    return _fetch_one("SELECT * FROM screening_runs ORDER BY id DESC LIMIT 1")


def get_screening_run(run_id):
    if run_id is None:
        return None
    return _fetch_one("SELECT * FROM screening_runs WHERE id = ?", (run_id,))


if __name__ == "__main__":
    init_db()
    conn = get_connection()
    tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    conn.close()
    print("Tables created:", [t["name"] for t in tables])


# ---------------------------------------------------------------------------
# New-object review (objects that entered the LOCAL catalog after its
# baseline). "New here" never implies newly launched; absence never implies
# retirement.
# ---------------------------------------------------------------------------
def first_seen_baseline():
    conn = get_connection()
    try:
        row = conn.execute("SELECT value FROM catalog_meta WHERE key = 'first_seen_baseline'").fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def list_new_objects():
    """Objects (active or not) whose first_seen is later than the catalog
    baseline, newest first, each with its latest operator review (or None)."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT value FROM catalog_meta WHERE key = 'first_seen_baseline'").fetchone()
        if not row:
            return []
        objs = [dict(r) for r in conn.execute(
            "SELECT * FROM objects WHERE first_seen IS NOT NULL AND first_seen > ? "
            "ORDER BY first_seen DESC, norad_id", (row[0],)).fetchall()]
        reviews = {}
        for r in conn.execute("SELECT * FROM object_reviews ORDER BY id").fetchall():
            reviews[r["norad_id"]] = dict(r)  # later rows supersede earlier ones
        for o in objs:
            o["review"] = reviews.get(o["norad_id"])
        return objs
    finally:
        conn.close()


def insert_object_review(norad_id, note=None, actor=None):
    conn = get_connection()
    try:
        cur = conn.execute("INSERT INTO object_reviews (norad_id, reviewed_at, note, actor_user_id, "
                           "actor_username, actor_role) VALUES (?, ?, ?, ?, ?, ?)",
                           (norad_id, _now_iso(), note, *_actor_columns(actor)))
        conn.commit()
        row = conn.execute("SELECT * FROM object_reviews WHERE id = ?", (cur.lastrowid,)).fetchone()
        return dict(row)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Governance audit (append-only): logins, user administration, protected-asset
# registry changes. `actor` = {user_id, username, role} or None.
# ---------------------------------------------------------------------------
def _insert_governance(conn, actor, action, target_type=None, target_id=None, details=None, is_demo=False):
    cur = conn.execute(
        "INSERT INTO governance_audit (at, actor_user_id, actor_username, actor_role, action, target_type, "
        "target_id, details_json, is_demo) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (_now_iso(), *_actor_columns(actor), action, target_type,
         None if target_id is None else str(target_id),
         None if details is None else json.dumps(details, sort_keys=True, default=str), int(bool(is_demo))),
    )
    return cur.lastrowid


def insert_governance_audit(actor, action, target_type=None, target_id=None, details=None, is_demo=False):
    conn = get_connection()
    try:
        row_id = _insert_governance(conn, actor, action, target_type, target_id, details, is_demo)
        conn.commit()
        return row_id
    finally:
        conn.close()


def _governance_row(r):
    d = dict(r)
    raw = d.pop("details_json", None)
    try:
        d["details"] = json.loads(raw) if raw else None
    except (TypeError, ValueError):
        d["details"] = None
    d["is_demo"] = bool(d.get("is_demo"))
    return d


def list_governance_audit(limit=200, target_type=None):
    """Most recent governance_audit rows, newest first."""
    limit = max(1, min(int(limit), 1000))
    conn = get_connection()
    try:
        if target_type:
            rows = conn.execute("SELECT * FROM governance_audit WHERE target_type = ? ORDER BY id DESC LIMIT ?",
                                (target_type, limit)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM governance_audit ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [_governance_row(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Users + sessions (backend/auth.py holds the policy: validation, hashing,
# permissions). Public user dicts never include password_hash.
# ---------------------------------------------------------------------------
USER_ROLES = ("VIEWER", "OPERATOR", "ASSET_MANAGER", "ADMINISTRATOR")
_PUBLIC_USER_COLUMNS = ("id", "username", "display_name", "role", "active", "created_at", "created_by",
                        "updated_at", "last_login_at")


class DuplicateError(ValueError):
    """A UNIQUE constraint (username / protected-asset name_query) was hit."""


class LastAdministratorError(ValueError):
    """The change would leave no active ADMINISTRATOR."""


class NotFoundError(LookupError):
    pass


class InvalidTransitionError(ValueError):
    pass


class LastActiveAssetError(ValueError):
    """The change would leave zero active protected assets."""


def _public_user(row):
    if row is None:
        return None
    d = {k: row[k] for k in _PUBLIC_USER_COLUMNS}
    d["active"] = bool(d["active"])
    return d


def _audit_user(user):
    return {k: user[k] for k in ("username", "display_name", "role", "active")}


def count_users():
    conn = get_connection()
    try:
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    finally:
        conn.close()


def get_user_auth_record(username):
    """Full row INCLUDING password_hash, for login verification only."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_user(user_id):
    conn = get_connection()
    try:
        return _public_user(conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())
    finally:
        conn.close()


def get_user_by_username(username):
    conn = get_connection()
    try:
        return _public_user(conn.execute("SELECT * FROM users WHERE username = ? COLLATE NOCASE",
                                         (username,)).fetchone())
    finally:
        conn.close()


def list_users():
    conn = get_connection()
    try:
        return [_public_user(r) for r in conn.execute("SELECT * FROM users ORDER BY id").fetchall()]
    finally:
        conn.close()


def create_user(username, display_name, role, password_hash, created_by, actor, audit_details=None):
    """Insert a user and its governance_audit 'user_create' row atomically."""
    now = _now_iso()
    conn = get_connection()
    try:
        try:
            cur = conn.execute(
                "INSERT INTO users (username, display_name, role, password_hash, active, created_at, created_by, "
                "updated_at) VALUES (?, ?, ?, ?, 1, ?, ?, ?)",
                (username, display_name, role, password_hash, now, created_by, now))
        except sqlite3.IntegrityError as exc:
            if "UNIQUE" in str(exc):
                raise DuplicateError(f"A user named '{username}' already exists.") from exc
            raise
        user = _public_user(conn.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone())
        _insert_governance(conn, actor, "user_create", "user", user["id"],
                           {"after": _audit_user(user), **(audit_details or {})})
        conn.commit()
        return user
    finally:
        conn.close()


def _active_admin_count(conn):
    return conn.execute("SELECT COUNT(*) FROM users WHERE role = 'ADMINISTRATOR' AND active = 1").fetchone()[0]


def update_user(user_id, actor, role=None, active=None, display_name=None, password_hash=None,
                set_display_name=False, audit_action="user_update"):
    """Apply changes in one transaction. Refuses (LastAdministratorError) a
    change that leaves no active ADMINISTRATOR. Deactivation or a password
    change revokes the user's sessions. Returns the public user dict."""
    now = _now_iso()
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            raise NotFoundError("User not found")
        before = _public_user(row)
        sets, params, changed = [], [], []
        if role is not None and role != row["role"]:
            sets.append("role = ?")
            params.append(role)
            changed.append("role")
        if active is not None and int(bool(active)) != row["active"]:
            sets.append("active = ?")
            params.append(int(bool(active)))
            changed.append("active")
        if set_display_name and display_name != row["display_name"]:
            sets.append("display_name = ?")
            params.append(display_name)
            changed.append("display_name")
        if password_hash is not None:
            sets.append("password_hash = ?")
            params.append(password_hash)
            changed.append("password")
        if not sets:
            conn.rollback()
            return before
        sets.append("updated_at = ?")
        params.append(now)
        # Column names come from the fixed list above, never from input.
        conn.execute(f"UPDATE users SET {', '.join(sets)} WHERE id = ?", (*params, user_id))
        if row["role"] == "ADMINISTRATOR" and row["active"] and _active_admin_count(conn) == 0:
            raise LastAdministratorError("Refused: this would leave no active ADMINISTRATOR.")
        after = _public_user(conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())
        revoked = 0
        if ("active" in changed and not after["active"]) or "password" in changed:
            revoked = conn.execute("UPDATE sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
                                   (now, user_id)).rowcount
        details = {"before": _audit_user(before), "after": _audit_user(after), "changed": changed}
        if revoked:
            details["sessions_revoked"] = revoked
        _insert_governance(conn, actor, audit_action, "user", user_id, details)
        conn.commit()
        return after
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def touch_last_login(user_id):
    conn = get_connection()
    try:
        conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (_now_iso(), user_id))
        conn.commit()
    finally:
        conn.close()


def create_session(token_hash, user_id, expires_at):
    now = _now_iso()
    conn = get_connection()
    try:
        conn.execute("INSERT INTO sessions (token_hash, user_id, created_at, expires_at, last_seen_at) "
                     "VALUES (?, ?, ?, ?, ?)", (token_hash, user_id, now, expires_at, now))
        conn.commit()
    finally:
        conn.close()


def get_session_with_user(token_hash):
    """The session row joined with its user (fresh from the DB), or None.
    Expiry/revocation/active checks are done by the caller (backend/auth.py)."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT s.token_hash, s.user_id, s.created_at AS session_created_at, s.expires_at, s.revoked_at, "
            "s.last_seen_at, u.username, u.display_name, u.role, u.active "
            "FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?", (token_hash,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def touch_session(token_hash):
    conn = get_connection()
    try:
        conn.execute("UPDATE sessions SET last_seen_at = ? WHERE token_hash = ?", (_now_iso(), token_hash))
        conn.commit()
    finally:
        conn.close()


def revoke_session(token_hash):
    """Revoke one session; returns the user_id if a live session was revoked."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT user_id FROM sessions WHERE token_hash = ? AND revoked_at IS NULL",
                           (token_hash,)).fetchone()
        if row is None:
            return None
        conn.execute("UPDATE sessions SET revoked_at = ? WHERE token_hash = ?", (_now_iso(), token_hash))
        conn.commit()
        return row["user_id"]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Protected-asset registry (Group A). The DB table is authoritative once
# seeded; changes affect the NEXT successful refresh only and never touch
# existing events/observations.
# ---------------------------------------------------------------------------
ASSET_STATUSES = ("active", "suspended", "retired")
ASSET_TRANSITIONS = {("active", "suspended"), ("suspended", "active"),
                     ("active", "retired"), ("suspended", "retired")}
_ASSET_SEED_META_KEY = "protected_assets_seeded_at"
SYSTEM_ACTOR = {"user_id": None, "username": "system", "role": None}


def _asset_dict(row):
    return dict(row) if row is not None else None


def _audit_asset(asset):
    return {k: asset.get(k) for k in ("name_query", "exact_match", "criticality", "note", "status")}


def ensure_protected_assets_seeded(source_path):
    """Seed protected_assets ONCE from `source_path` (working_set.json)
    group_a. Afterwards the table is authoritative and this is a no-op
    (tracked in catalog_meta, so retiring/editing never re-seeds). Returns
    the number of rows seeded (0 when already seeded)."""
    conn = get_connection()
    try:
        has_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'protected_assets'").fetchone()
    finally:
        conn.close()
    if not has_table:
        init_db()
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT 1 FROM catalog_meta WHERE key = ?", (_ASSET_SEED_META_KEY,)).fetchone():
            conn.rollback()
            return 0
        now = _now_iso()
        seeded = []
        if conn.execute("SELECT COUNT(*) FROM protected_assets").fetchone()[0] == 0:
            try:
                with open(source_path, "r", encoding="utf-8") as f:
                    entries = json.load(f).get("group_a", []) or []
            except (OSError, ValueError):
                entries = []
            for e in entries:
                if (not isinstance(e, dict) or not e.get("name_query")
                        or e.get("criticality") not in ("Tier1", "Tier2", "Tier3")):
                    continue
                try:
                    conn.execute(
                        "INSERT INTO protected_assets (name_query, exact_match, criticality, note, status, "
                        "created_at, created_by, updated_at, updated_by) "
                        "VALUES (?, ?, ?, ?, 'active', ?, 'system', ?, 'system')",
                        (e["name_query"], e.get("exact_match"), e["criticality"], e.get("note"), now, now))
                    seeded.append(e["name_query"])
                except sqlite3.IntegrityError:
                    continue  # duplicate name in the file: first one wins
        conn.execute("INSERT INTO catalog_meta (key, value) VALUES (?, ?)", (_ASSET_SEED_META_KEY, now))
        _insert_governance(conn, SYSTEM_ACTOR, "asset_seed", "protected_asset", None,
                           {"source": "working_set.json group_a", "seeded": seeded})
        conn.commit()
        return len(seeded)
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_protected_assets(status=None):
    conn = get_connection()
    try:
        if status:
            rows = conn.execute("SELECT * FROM protected_assets WHERE status = ? ORDER BY id", (status,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM protected_assets ORDER BY id").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_protected_asset(asset_id):
    conn = get_connection()
    try:
        return _asset_dict(conn.execute("SELECT * FROM protected_assets WHERE id = ?", (asset_id,)).fetchone())
    finally:
        conn.close()


def active_group_a_entries():
    """Active registry rows in the exact working_set.json group_a entry shape
    and order (name_query, [exact_match], criticality, [note]) so named-entry
    resolution in backend/ingest.py is unchanged."""
    out = []
    for a in list_protected_assets(status="active"):
        entry = {"name_query": a["name_query"]}
        if a.get("exact_match"):
            entry["exact_match"] = a["exact_match"]
        entry["criticality"] = a["criticality"]
        if a.get("note") is not None:
            entry["note"] = a["note"]
        out.append(entry)
    return out


def _actor_label(actor):
    return (actor or {}).get("username")


def create_protected_asset(name_query, exact_match, criticality, note, actor):
    now = _now_iso()
    conn = get_connection()
    try:
        try:
            cur = conn.execute(
                "INSERT INTO protected_assets (name_query, exact_match, criticality, note, status, created_at, "
                "created_by, updated_at, updated_by) VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?)",
                (name_query, exact_match, criticality, note, now, _actor_label(actor), now, _actor_label(actor)))
        except sqlite3.IntegrityError as exc:
            if "UNIQUE" in str(exc):
                raise DuplicateError(f"A protected asset with name_query '{name_query}' already exists "
                                     "(including retired entries).") from exc
            raise
        asset = _asset_dict(conn.execute("SELECT * FROM protected_assets WHERE id = ?", (cur.lastrowid,)).fetchone())
        _insert_governance(conn, actor, "asset_create", "protected_asset", asset["id"],
                           {"before": None, "after": _audit_asset(asset)})
        conn.commit()
        return asset
    finally:
        conn.close()


_ASSET_EDITABLE = ("criticality", "note", "exact_match")


def update_protected_asset(asset_id, changes, actor):
    """`changes` subset of {criticality, note, exact_match} (name_query is
    immutable). Retired assets are terminal and cannot be edited."""
    if set(changes) - set(_ASSET_EDITABLE):
        raise ValueError("Only criticality, note and exact_match can be changed.")
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        before = _asset_dict(conn.execute("SELECT * FROM protected_assets WHERE id = ?", (asset_id,)).fetchone())
        if before is None:
            raise NotFoundError("Protected asset not found")
        if before["status"] == "retired":
            raise InvalidTransitionError("Retired protected assets are terminal and cannot be edited.")
        diff = {k: changes[k] for k in _ASSET_EDITABLE if k in changes and before.get(k) != changes[k]}
        if not diff:
            conn.rollback()
            return before
        sets = ", ".join(f"{k} = ?" for k in diff)  # keys from the fixed allow-list above
        conn.execute(f"UPDATE protected_assets SET {sets}, updated_at = ?, updated_by = ? WHERE id = ?",
                     (*diff.values(), _now_iso(), _actor_label(actor), asset_id))
        after = _asset_dict(conn.execute("SELECT * FROM protected_assets WHERE id = ?", (asset_id,)).fetchone())
        _insert_governance(conn, actor, "asset_update", "protected_asset", asset_id,
                           {"before": _audit_asset(before), "after": _audit_asset(after), "changed": sorted(diff)})
        conn.commit()
        return after
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def set_protected_asset_status(asset_id, status, reason, actor):
    """active<->suspended, active/suspended->retired; retired is terminal.
    Refuses (LastActiveAssetError) leaving zero active assets."""
    if status not in ASSET_STATUSES:
        raise ValueError(f"status must be one of {list(ASSET_STATUSES)}")
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        before = _asset_dict(conn.execute("SELECT * FROM protected_assets WHERE id = ?", (asset_id,)).fetchone())
        if before is None:
            raise NotFoundError("Protected asset not found")
        if (before["status"], status) not in ASSET_TRANSITIONS:
            raise InvalidTransitionError(
                f"Transition {before['status']} -> {status} is not allowed"
                + (" (retired is terminal)." if before["status"] == "retired" else "."))
        if before["status"] == "active":
            n_active = conn.execute("SELECT COUNT(*) FROM protected_assets WHERE status = 'active'").fetchone()[0]
            if n_active <= 1:
                raise LastActiveAssetError("Refused: this would leave zero active protected assets.")
        conn.execute("UPDATE protected_assets SET status = ?, updated_at = ?, updated_by = ? WHERE id = ?",
                     (status, _now_iso(), _actor_label(actor), asset_id))
        after = _asset_dict(conn.execute("SELECT * FROM protected_assets WHERE id = ?", (asset_id,)).fetchone())
        _insert_governance(conn, actor, "asset_status", "protected_asset", asset_id,
                           {"before": _audit_asset(before), "after": _audit_asset(after), "reason": reason})
        conn.commit()
        return after
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# App settings (backend/settings.py holds the policy: keys, allowed values,
# defaults). set_setting_audited writes the value and its governance_audit
# row in one transaction.
# ---------------------------------------------------------------------------
def get_setting_row(key):
    return _fetch_one("SELECT * FROM app_settings WHERE key = ?", (key,))


def set_setting_audited(key, value, actor, audit_details):
    now = _now_iso()
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO app_settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at, "
            "updated_by = excluded.updated_by",
            (key, str(value), now, (actor or {}).get("username")),
        )
        _insert_governance(conn, actor, "config_change", "setting", key, audit_details)
        conn.commit()
    finally:
        conn.close()
    return get_setting_row(key)


# ---------------------------------------------------------------------------
# Refresh attempts (pipeline.run_refresh_pipeline / backend/scheduler.py)
# ---------------------------------------------------------------------------
REFRESH_TRIGGERS = ("manual", "scheduled")
REFRESH_ATTEMPT_STATUSES = ("running", "success", "failed", "refused", "skipped")
_REFRESH_ATTEMPT_UPDATABLE = frozenset(("completed_at", "status", "reason", "ingest_run_id",
                                        "screening_run_id", "horizon_hours"))


def insert_refresh_attempt(trigger, status, started_at=None, completed_at=None, reason=None,
                           horizon_hours=None, actor=None):
    if trigger not in REFRESH_TRIGGERS:
        raise ValueError(f"Unknown refresh trigger {trigger!r}")
    if status not in REFRESH_ATTEMPT_STATUSES:
        raise ValueError(f"Unknown refresh status {status!r}")
    conn = get_connection()
    try:
        cur = conn.execute(
            'INSERT INTO refresh_attempts ("trigger", started_at, completed_at, status, reason, horizon_hours, '
            "actor_username, actor_role) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (trigger, started_at or _now_iso(), completed_at, status, reason, horizon_hours,
             (actor or {}).get("username"), (actor or {}).get("role")),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def update_refresh_attempt(attempt_id, **fields):
    unknown = set(fields) - _REFRESH_ATTEMPT_UPDATABLE
    if unknown:
        raise ValueError(f"Unknown refresh_attempts fields: {sorted(unknown)}")
    if fields.get("status") is not None and fields["status"] not in REFRESH_ATTEMPT_STATUSES:
        raise ValueError(f"Unknown refresh status {fields['status']!r}")
    if not fields:
        return
    conn = get_connection()
    try:
        conn.execute(f"UPDATE refresh_attempts SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
                     (*fields.values(), attempt_id))
        conn.commit()
    finally:
        conn.close()


def get_refresh_attempt(attempt_id):
    return _fetch_one("SELECT * FROM refresh_attempts WHERE id = ?", (attempt_id,))


def latest_refresh_attempt():
    return _fetch_one("SELECT * FROM refresh_attempts ORDER BY id DESC LIMIT 1")


def latest_successful_refresh_attempt():
    return _fetch_one("SELECT * FROM refresh_attempts WHERE status = 'success' ORDER BY id DESC LIMIT 1")


def list_refresh_attempts(limit=50):
    limit = max(1, min(int(limit), 500))
    conn = get_connection()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM refresh_attempts ORDER BY id DESC LIMIT ?",
                                              (limit,)).fetchall()]
    finally:
        conn.close()


def mark_interrupted_refresh_attempts():
    """At startup: attempts still 'running' belonged to a previous process
    that stopped mid-refresh. Recorded as failed (reason 'interrupted')."""
    conn = get_connection()
    try:
        cur = conn.execute("UPDATE refresh_attempts SET status = 'failed', reason = 'interrupted', "
                           "completed_at = ? WHERE status = 'running'", (_now_iso(),))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()
