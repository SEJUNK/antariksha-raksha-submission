"""Demo-scenario error handling: expected scenario failures surface as
DemoScenarioError -> structured JSON (`detail` string + `error` +
`reason`), unexpected internal errors -> generic 500 (logged server-side
with traceback, no internals in the body). Uses the same synthetic-TLE DB
fixture as test_demo_seed.py; the real pipeline runs, Ollama is stubbed."""

import logging

import numpy as np
import pytest

from backend.tests._asgi import request
from backend.tests.test_demo_seed import _make_tle, demo_db  # noqa: F401  (fixture)


@pytest.fixture(autouse=True)
def _no_auth(monkeypatch):
    monkeypatch.setattr("backend.config.API_KEY", "")
    monkeypatch.setattr("backend.config.AUTH_REQUIRED", False)


def _post_seed(mode="collision"):
    from backend.main import app
    return request(app, "POST", f"/api/demo/seed?mode={mode}")


from backend.demo_seed import seed_event as _real_seed_event  # noqa: E402


def _seed_collision(proximity=False):
    """Fixture-object hints; also stands in for seed_event behind the API
    route (whose defaults name real catalog objects)."""
    return _real_seed_event(asset_hint="TEST-ASSET", proximity=False, debris_hint="TEST-DEBRIS")


def _assert_scenario_error(status, body, expected_status, reason):
    assert status == expected_status
    assert body["error"] == "demo_scenario_failed"
    assert body["reason"] == reason
    assert isinstance(body["detail"], str) and body["detail"]  # frontend shows detail as text
    assert "Traceback" not in body["detail"]


def test_missing_required_object(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.db.DB_PATH", tmp_path / "missing.db")
    from backend.db import init_db, upsert_object
    from backend.demo_seed import DemoScenarioError, seed_event

    init_db()
    l1, l2 = _make_tle(90001)
    upsert_object("90001", "TEST-ASSET-SAT", "satellite", "India", "Tier1", l1, l2)
    from backend.tests._registry_helpers import protect
    protect("90001", "TEST-ASSET-SAT")  # the asset is protected via the registry; only the counterpart is missing
    with pytest.raises(DemoScenarioError) as exc:
        seed_event(asset_hint="TEST-ASSET", proximity=True)
    assert exc.value.reason == "missing_object" and exc.value.status_code == 409
    assert "foreign_sat" in str(exc.value)

    status, _, body = _post_seed("proximity")
    _assert_scenario_error(status, body, 409, "missing_object")


def test_unparseable_catalog_tle(demo_db):
    from backend.db import upsert_object
    from backend.demo_seed import DemoScenarioError

    upsert_object("90002", "TEST-DEBRIS-FRAG", "debris", None, None, "garbage line 1", "garbage line 2")
    with pytest.raises(DemoScenarioError) as exc:
        _seed_collision()
    assert exc.value.reason == "invalid_tle"
    assert "TEST-DEBRIS-FRAG" in str(exc.value)


def test_invalid_geometry_numeric_failure(demo_db, monkeypatch):
    from backend import demo_seed
    from backend.db import list_demo_overrides

    def singular(*args, **kwargs):
        raise np.linalg.LinAlgError("SVD did not converge")
    monkeypatch.setattr(demo_seed, "_design_crossing", singular)

    with pytest.raises(demo_seed.DemoScenarioError) as exc:
        _seed_collision()
    assert exc.value.reason == "geometry_failed"
    assert list_demo_overrides() == []  # failed before any override was stored


def test_invalid_geometry_non_convergence_via_api(demo_db, monkeypatch):
    from backend import demo_seed

    def no_converge(*args, **kwargs):
        raise demo_seed.DemoScenarioError("Could not converge the demo crossing-encounter design.",
                                          reason="geometry_failed")
    monkeypatch.setattr(demo_seed, "_design_crossing", no_converge)
    monkeypatch.setattr(demo_seed, "seed_event", _seed_collision)
    status, _, body = _post_seed("collision")
    _assert_scenario_error(status, body, 502, "geometry_failed")
    assert "converge" in body["detail"]


class _FailingSatrec:
    """Wraps a real Satrec but reports an SGP4 error on every propagation
    (as SGP4 does for e.g. a decayed orbit)."""

    def __init__(self, sat):
        self._sat = sat

    def __getattr__(self, name):
        return getattr(self._sat, name)

    def sgp4(self, jd, fr):
        return 6, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)


def test_propagation_failure(demo_db, monkeypatch):
    from backend import demo_seed

    real_parse = demo_seed._parse_tle
    monkeypatch.setattr(demo_seed, "_parse_tle", lambda obj: _FailingSatrec(real_parse(obj)))
    with pytest.raises(demo_seed.DemoScenarioError) as exc:
        _seed_collision()
    assert exc.value.reason == "propagation_failed"
    assert "SGP4 propagation error 6" in str(exc.value)


def test_no_resulting_event(demo_db, monkeypatch):
    from backend import demo_seed

    monkeypatch.setattr(demo_seed, "_find_seeded_event", lambda *a, **kw: None)
    with pytest.raises(demo_seed.DemoScenarioError) as exc:
        _seed_collision()
    assert exc.value.reason == "no_event" and exc.value.status_code == 502


def test_unexpected_error_is_generic_500_and_logged(demo_db, monkeypatch, caplog):
    from backend import demo_seed

    def boom(**kwargs):
        raise KeyError("C:\\secret\\path\\internal_column")
    monkeypatch.setattr(demo_seed, "run_screening_and_briefs", boom)

    # Not swallowed at the library level: a programming error propagates.
    with pytest.raises(KeyError):
        _seed_collision()

    monkeypatch.setattr(demo_seed, "seed_event", _seed_collision)
    with caplog.at_level(logging.ERROR, logger="backend.main"):
        status, _, body = _post_seed("collision")
    assert status == 500
    assert body["error"] == "internal_error"
    assert "secret" not in body["detail"] and "KeyError" not in body["detail"]
    logged = [r for r in caplog.records if r.name == "backend.main" and r.exc_info]
    assert logged and logged[0].exc_info[0] is KeyError  # full traceback kept server-side


def test_success_via_api(demo_db, monkeypatch):
    from backend import demo_seed
    from backend.db import get_event_with_brief

    monkeypatch.setattr(demo_seed, "seed_event", _seed_collision)
    status, _, body = _post_seed("collision")
    assert status == 200
    assert body["scenario"] == "collision_demo"
    assert get_event_with_brief(body["event_id"])["is_demo"] == 1
