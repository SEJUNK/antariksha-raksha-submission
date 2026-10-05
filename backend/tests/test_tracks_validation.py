"""/api/tracks query validation: window_minutes 1..240 (default 120),
step_seconds 10..300 (default 60); anything else is a 422 before any
propagation runs. Propagation itself is stubbed -- only the HTTP contract
is under test here."""

import pytest

from backend.tests._asgi import request


@pytest.fixture
def tracks_calls(monkeypatch):
    from backend import main

    calls = []

    def fake_tracks(window_minutes, step_seconds, objects=None):
        calls.append((window_minutes, step_seconds))
        return [{"window_minutes": window_minutes, "step_seconds": step_seconds}]

    monkeypatch.setattr(main, "position_tracks", fake_tracks)
    monkeypatch.setattr(main, "list_objects_with_demo_overrides", lambda: [])
    return calls


def _get(query=""):
    from backend.main import app
    return request(app, "GET", "/api/tracks" + (f"?{query}" if query else ""))


def test_tracks_defaults(tracks_calls):
    status, _, body = _get()
    assert status == 200
    assert tracks_calls == [(120, 60)]
    assert body == [{"window_minutes": 120, "step_seconds": 60}]


def test_tracks_min_boundary(tracks_calls):
    status, _, _ = _get("window_minutes=1&step_seconds=10")
    assert status == 200
    assert tracks_calls == [(1, 10)]


def test_tracks_max_boundary(tracks_calls):
    status, _, _ = _get("window_minutes=240&step_seconds=300")
    assert status == 200
    assert tracks_calls == [(240, 300)]


@pytest.mark.parametrize("query", [
    "window_minutes=0",
    "window_minutes=-5",
    "window_minutes=241",
    "window_minutes=100000",
    "window_minutes=12.5",
    "window_minutes=abc",
    "step_seconds=0",
    "step_seconds=-60",
    "step_seconds=9",
    "step_seconds=301",
    "step_seconds=1.5",
    "step_seconds=abc",
])
def test_tracks_invalid_values_rejected_with_422(tracks_calls, query):
    status, _, body = _get(query)
    assert status == 422
    assert tracks_calls == []  # never reached propagation
    assert isinstance(body["detail"], list)
