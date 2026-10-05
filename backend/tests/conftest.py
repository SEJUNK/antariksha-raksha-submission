"""Shared test setup.

- Every test gets its own throwaway SQLite file by default (tests that patch
  backend.db.DB_PATH themselves still win: autouse fixtures run first), so
  no test can read or write data/antariksha.db.
- RBAC: by default the session/CSRF dependency (backend.auth.get_principal)
  is overridden with an ADMINISTRATOR principal, so route tests written
  before authentication existed run unchanged. Tests that exercise the real
  login/session/CSRF/permission path use the `real_auth` marker or fixture,
  which removes the override.
"""

import os

import pytest

# Never start the in-process scheduler thread under tests (set before
# backend.config is imported; the autouse fixture below enforces it too).
os.environ["ANTARIKSHA_SCHEDULER_MODE"] = "off"

from backend.auth import Principal, get_principal  # noqa: E402

TEST_ADMIN = Principal(id=None, username="test-admin", display_name="Test Administrator", role="ADMINISTRATOR")


def pytest_configure(config):
    config.addinivalue_line("markers", "real_auth: run against the real session/CSRF/RBAC checks "
                                       "(no test principal override)")


@pytest.fixture(autouse=True)
def _test_isolation(request, tmp_path, monkeypatch):
    monkeypatch.setattr("backend.db.DB_PATH", tmp_path / "default_test.db")
    monkeypatch.setattr("backend.config.SCHEDULER_MODE", "off")
    monkeypatch.delenv("SCHEDULER_SECRET", raising=False)
    monkeypatch.delenv("CRON_SECRET", raising=False)
    from backend.main import app

    use_real_auth = request.node.get_closest_marker("real_auth") is not None
    if not use_real_auth:
        app.dependency_overrides[get_principal] = lambda: TEST_ADMIN
    yield
    app.dependency_overrides.pop(get_principal, None)


@pytest.fixture
def real_auth():
    """Remove the test principal override for this test."""
    from backend.main import app

    app.dependency_overrides.pop(get_principal, None)
    yield
