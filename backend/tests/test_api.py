"""Route-level tests, calling the FastAPI handlers directly (no HTTP client
dependency): demo-mode validation, decision logging/history, CSV export,
and health's persisted cache flag."""

import csv
import io

import pytest
from fastapi import HTTPException


@pytest.fixture
def api_db(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.db.DB_PATH", tmp_path / "api_test.db")
    from backend.db import init_db, insert_brief, insert_event, upsert_object
    init_db()
    upsert_object("1", "ASSET", "satellite", "India", "Tier1", "l1", "l2")
    upsert_object("2", "DEB", "debris", None, None, "l1", "l2")
    event_id = insert_event("1", "2", "collision_risk", "2026-07-21T04:12:00+00:00", 0.05, 7.1,
                            2e-4, "Critical", 600.0, is_demo=True, screening_run_id=7)
    insert_brief(event_id, "brief", "maneuver", 0.05, "fallback_template", review_status="not_applicable")
    return event_id


def test_demo_seed_rejects_unknown_mode():
    from backend.main import demo_seed
    with pytest.raises(HTTPException) as exc:
        demo_seed(mode="asteroid")
    assert exc.value.status_code == 400


def test_decision_logged_with_event_context_and_history_endpoint(api_db):
    from backend.main import DismissBody, approve_event, dismiss_event, get_event_decisions

    approve_event(api_db)
    dismiss_event(api_db, DismissBody(reason="already handled"))

    history = get_event_decisions(api_db)
    assert [d["decision"] for d in history] == ["dismissed", "approved"]
    latest = history[0]
    assert latest["event_id"] == api_db
    assert latest["is_demo"] == 1
    assert latest["screening_run_id"] == 7
    assert latest["tca_timestamp"] == "2026-07-21T04:12:00+00:00"
    assert latest["rejection_reason"] == "already handled"

    with pytest.raises(HTTPException) as exc:
        get_event_decisions(99999)
    assert exc.value.status_code == 404


def test_csv_export_appends_new_columns(api_db):
    from backend.main import approve_event, export_decisions

    approve_event(api_db)
    rows = list(csv.reader(io.StringIO(export_decisions().body.decode())))
    header = rows[0]
    # Original columns unchanged and in order; new ones appended at the end.
    assert header[:11] == ["id", "decided_at", "event_class", "risk_tier", "object_a", "object_b",
                           "pc_score", "miss_distance_km", "brief_source", "decision", "rejection_reason"]
    assert header[11:15] == ["event_id", "is_demo", "screening_run_id", "tca_timestamp"]
    assert rows[1][11:15] == [str(api_db), "1", "7", "2026-07-21T04:12:00+00:00"]
    # Identity-aware audit columns come last (empty for unattributed rows).
    assert header[15:] == ["actor_username", "actor_role"]


def test_health_using_cache_comes_from_persisted_ingest(api_db, monkeypatch):
    from backend import main
    from backend.db import insert_ingest_run

    monkeypatch.setattr(main, "_ollama_reachable", lambda: False)
    insert_ingest_run("CelesTrak", used_cache=True, object_count=2, counts={})
    assert main.health()["using_cache"] is True
    insert_ingest_run("CelesTrak", used_cache=False, object_count=2, counts={})
    assert main.health()["using_cache"] is False
