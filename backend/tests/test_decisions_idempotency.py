"""Operator decisions are idempotent: repeating the current decision (double
click, client retry) returns the current state without a duplicate
decision_log row; genuine approved<->dismissed changes are still recorded.
Also covers dismiss-reason bounds/normalisation (reasons are fed back into
the brief-generation prompt)."""

import threading

import pytest

from backend.tests._asgi import request


@pytest.fixture
def decision_db(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.db.DB_PATH", tmp_path / "decision_test.db")
    monkeypatch.setattr("backend.config.API_KEY", "")
    monkeypatch.setattr("backend.config.AUTH_REQUIRED", False)
    from backend.db import init_db, insert_brief, insert_event, upsert_object
    init_db()
    upsert_object("1", "ASSET", "satellite", "India", "Tier1", "l1", "l2")
    upsert_object("2", "DEB", "debris", None, None, "l1", "l2")
    event_id = insert_event("1", "2", "collision_risk", "2026-07-21T04:12:00+00:00", 0.05, 7.1,
                            2e-4, "Critical", 600.0, screening_run_id=3)
    insert_brief(event_id, "brief", "maneuver", 0.05, "fallback_template")
    return event_id


def _history(event_id):
    from backend.db import decisions_for_event
    return [(d["decision"], d["rejection_reason"]) for d in reversed(decisions_for_event(event_id))]


def test_pending_to_approved_records_once(decision_db):
    from backend.main import approve_event

    result = approve_event(decision_db)
    assert result["status"] == "approved" and result["decision_recorded"] is True
    assert _history(decision_db) == [("approved", None)]


def test_repeat_approve_is_idempotent(decision_db):
    from backend.main import approve_event

    approve_event(decision_db)
    again = approve_event(decision_db)
    assert again["status"] == "approved"
    assert again["decision_recorded"] is False
    assert _history(decision_db) == [("approved", None)]


def test_pending_to_dismissed_records_reason(decision_db):
    from backend.main import DismissBody, dismiss_event

    result = dismiss_event(decision_db, DismissBody(reason="sensor artefact"))
    assert result["status"] == "dismissed" and result["decision_recorded"] is True
    assert _history(decision_db) == [("dismissed", "sensor artefact")]


def test_repeat_dismiss_is_idempotent_and_keeps_first_reason(decision_db):
    from backend.main import DismissBody, dismiss_event

    dismiss_event(decision_db, DismissBody(reason="sensor artefact"))
    again = dismiss_event(decision_db, DismissBody(reason="different retry text"))
    assert again["status"] == "dismissed" and again["decision_recorded"] is False
    assert _history(decision_db) == [("dismissed", "sensor artefact")]


def test_genuine_transitions_are_recorded_both_ways(decision_db):
    from backend.main import DismissBody, approve_event, dismiss_event

    dismiss_event(decision_db, DismissBody(reason="first look"))
    assert approve_event(decision_db)["decision_recorded"] is True        # dismissed -> approved
    assert dismiss_event(decision_db, DismissBody(reason="re-assessed"))["decision_recorded"] is True
    assert _history(decision_db) == [("dismissed", "first look"), ("approved", None),
                                     ("dismissed", "re-assessed")]


def test_unknown_event_is_404(decision_db):
    from fastapi import HTTPException

    from backend.main import approve_event
    with pytest.raises(HTTPException) as exc:
        approve_event(99999)
    assert exc.value.status_code == 404


def test_brief_not_yet_generated_uses_decision_log_as_state(decision_db):
    """Event inserted but its brief still generating: no stored status, so
    the last logged decision is the current state -- still idempotent."""
    from backend.db import insert_event
    from backend.main import approve_event

    eid = insert_event("1", "2", "collision_risk", "2026-07-21T05:00:00+00:00", 0.05, 7.1,
                       2e-4, "Critical", 600.0)
    assert approve_event(eid)["decision_recorded"] is True
    assert approve_event(eid)["decision_recorded"] is False
    assert _history(eid) == [("approved", None)]


def test_brief_arriving_after_decision_inherits_it(decision_db):
    """Decision made while the brief was generating, then the brief lands:
    the brief must show the decision (not 'pending') and a repeat of the same
    decision must not add a second audit row."""
    from backend.db import get_event_with_brief, insert_brief, insert_event
    from backend.main import approve_event

    eid = insert_event("1", "2", "collision_risk", "2026-07-21T06:00:00+00:00", 0.05, 7.1,
                       2e-4, "Critical", 600.0)
    assert approve_event(eid)["decision_recorded"] is True
    insert_brief(eid, "late brief", "maneuver", 0.05, "llm", review_status="consistent")
    assert get_event_with_brief(eid)["status"] == "approved"
    assert approve_event(eid)["decision_recorded"] is False
    assert _history(eid) == [("approved", None)]


def test_concurrent_identical_approvals_record_once(decision_db):
    from backend.main import approve_event

    barrier = threading.Barrier(8)
    results, errors = [], []

    def worker():
        try:
            barrier.wait()
            results.append(approve_event(decision_db)["decision_recorded"])
        except Exception as exc:  # surfaced via the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert errors == []
    assert sorted(results) == [False] * 7 + [True]
    assert _history(decision_db) == [("approved", None)]


def test_api_retry_over_http_is_idempotent(decision_db):
    from backend.main import app

    for _ in range(3):
        status, _, body = request(app, "POST", f"/api/events/{decision_db}/dismiss",
                                  json_body={"reason": "  duplicate of\nearlier\tevent  "})
        assert status == 200 and body["status"] == "dismissed"
    assert _history(decision_db) == [("dismissed", "duplicate of earlier event")]

    status, _, body = request(app, "POST", f"/api/events/{decision_db}/approve")
    assert status == 200 and body["decision_recorded"] is True
    status, _, body = request(app, "POST", f"/api/events/{decision_db}/approve")
    assert status == 200 and body["decision_recorded"] is False
    assert [d for d, _ in _history(decision_db)] == ["dismissed", "approved"]


def test_dismiss_reason_too_long_is_422_and_not_recorded(decision_db):
    from backend.main import DISMISS_REASON_MAX_CHARS, app

    status, _, _ = request(app, "POST", f"/api/events/{decision_db}/dismiss",
                           json_body={"reason": "x" * (DISMISS_REASON_MAX_CHARS + 1)})
    assert status == 422
    assert _history(decision_db) == []
    status, _, _ = request(app, "POST", f"/api/events/{decision_db}/dismiss",
                           json_body={"reason": "x" * DISMISS_REASON_MAX_CHARS})
    assert status == 200


def test_dismiss_reason_normalised():
    from backend.main import DismissBody

    assert DismissBody(reason="  ok\r\nnext\x00line\x1b[31m  ").reason == "ok next line [31m"
    assert DismissBody(reason=" \n\t ").reason is None
    assert DismissBody().reason is None
