"""Railway / container deployment configuration (Dockerfile, railway.json,
.dockerignore) and the deploy-DB preparation script. Configuration checks
only: no science, auth or scheduler behaviour is changed by these files."""

import importlib.util
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend import db

REPO = Path(__file__).resolve().parents[2]


def _load_prepare():
    spec = importlib.util.spec_from_file_location("prepare_deploy_db", REPO / "scripts" / "prepare_deploy_db.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_dockerfile_single_worker_platform_port_all_interfaces():
    src = (REPO / "Dockerfile").read_text(encoding="utf-8")
    assert "--host 0.0.0.0" in src
    assert "${PORT:-8000}" in src  # platform PORT, local default 8000
    assert "--workers 1" in src  # one process -> one internal scheduler
    assert "COPY frontend" not in src  # backend only
    assert not [ln for ln in src.splitlines() if ln.startswith("COPY") and ".db" in ln]  # never bake in a DB


def test_dockerignore_excludes_databases_secrets_and_venv():
    lines = (REPO / ".dockerignore").read_text(encoding="utf-8").splitlines()
    for pattern in ("**/*.db", "**/.env", "backend/venv/", "!data/working_set.json"):
        assert pattern in lines


def test_railway_json_one_replica_and_health_check():
    cfg = json.loads((REPO / "railway.json").read_text(encoding="utf-8"))
    assert cfg["build"]["builder"] == "DOCKERFILE"
    assert cfg["deploy"]["numReplicas"] == 1
    assert cfg["deploy"]["healthcheckPath"] == "/api/health"


def test_prepare_deploy_db_keeps_users_and_audit_but_drops_sessions(tmp_path, monkeypatch):
    from backend.auth import create_user_account

    source = tmp_path / "source.db"
    monkeypatch.setattr("backend.db.DB_PATH", source)
    db.init_db()
    user = create_user_account("deploy-admin", "ADMINISTRATOR", "a-long-test-password-123")
    db.create_session("0" * 64, user["id"], (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat())
    with sqlite3.connect(source) as c:
        before = {t: c.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in ("users", "sessions", "governance_audit")}
        hash_before = c.execute("SELECT password_hash FROM users WHERE username='deploy-admin'").fetchone()[0]
    assert before["sessions"] == 1 and before["governance_audit"] >= 1

    out = tmp_path / "deploy.db"
    result = _load_prepare().prepare(source, out)
    assert result["integrity"] == "ok"
    assert result["counts"]["sessions"] == 0
    assert result["counts"]["users"] == before["users"]
    assert result["counts"]["governance_audit"] == before["governance_audit"]
    assert {"username": "deploy-admin", "role": "ADMINISTRATOR", "active": True} in result["users"]
    assert all("password" not in k for u in result["users"] for k in u)  # nothing secret reported
    with sqlite3.connect(out) as c:
        assert c.execute("SELECT password_hash FROM users WHERE username='deploy-admin'").fetchone()[0] == hash_before
    with sqlite3.connect(source) as c:  # source untouched
        assert c.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1


def test_prepare_deploy_db_refuses_overwrite_and_missing_source(tmp_path):
    prepare = _load_prepare().prepare
    with pytest.raises(SystemExit):
        prepare(tmp_path / "missing.db", tmp_path / "out.db")
    existing = tmp_path / "exists.db"
    sqlite3.connect(existing).close()
    with pytest.raises(SystemExit):
        prepare(existing, existing)
