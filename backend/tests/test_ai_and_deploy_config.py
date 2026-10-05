"""AI configuration (ANTARIKSHA_AI_MODE / _OLLAMA_URL / _OLLAMA_MODEL) and
deployment-friendly paths (ANTARIKSHA_DB_PATH / _TLE_CACHE_DIR). The
scientific pipeline never depends on the AI: disabled or unreachable AI
still yields computed events/risk with labelled template briefs."""

import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest
import requests

from backend.tests.test_demo_seed import demo_db  # noqa: F401  (fixture)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _fresh_config(env):
    """Import backend.config in a subprocess with `env` overrides and return
    selected values (the module reads the environment at import time)."""
    code = ("import json; from backend import config as c; print(json.dumps({"
            "'db': str(c.DB_PATH), 'cache': str(c.TLE_CACHE_DIR), 'url': c.OLLAMA_URL, "
            "'model': c.OLLAMA_MODEL, 'ai': c.AI_MODE, 'sched': c.SCHEDULER_MODE}))")
    full_env = {k: v for k, v in os.environ.items()
                if not k.startswith("ANTARIKSHA_") and k not in ("SCHEDULER_SECRET", "CRON_SECRET")}
    full_env.update(env)
    out = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT, env=full_env, capture_output=True,
                         text=True, check=True)
    import json
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_config_defaults_unchanged():
    c = _fresh_config({})
    assert Path(c["db"]) == REPO_ROOT / "data" / "antariksha.db"
    assert Path(c["cache"]) == REPO_ROOT / "data" / "tle_cache"
    assert c["url"] == "http://127.0.0.1:11434/api/generate"
    assert c["model"] == "llama3.2:3b"
    assert c["ai"] == "ollama"
    assert c["sched"] == "internal"


def test_config_env_overrides(tmp_path):
    c = _fresh_config({
        "ANTARIKSHA_DB_PATH": str(tmp_path / "vol" / "a.db"),
        "ANTARIKSHA_TLE_CACHE_DIR": str(tmp_path / "vol" / "cache"),
        "ANTARIKSHA_OLLAMA_URL": "http://ollama.internal:11434/api/generate",
        "ANTARIKSHA_OLLAMA_MODEL": "llama3.1:8b",
        "ANTARIKSHA_AI_MODE": "disabled",
        "ANTARIKSHA_SCHEDULER_MODE": "external",
    })
    assert Path(c["db"]) == tmp_path / "vol" / "a.db"
    assert Path(c["cache"]) == tmp_path / "vol" / "cache"
    assert c["url"] == "http://ollama.internal:11434/api/generate" and c["model"] == "llama3.1:8b"
    assert c["ai"] == "disabled" and c["sched"] == "external"


def test_invalid_modes_fall_back_safely():
    c = _fresh_config({"ANTARIKSHA_AI_MODE": "openai", "ANTARIKSHA_SCHEDULER_MODE": "sometimes"})
    assert c["ai"] == "ollama"       # no external provider can be selected
    assert c["sched"] == "off"       # unknown scheduler mode never polls


def _forbid_network(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("network call attempted while AI is disabled")
    monkeypatch.setattr("backend.brief_agent.requests.post", _boom)
    monkeypatch.setattr("backend.main.requests.get", _boom)


def test_ai_disabled_makes_no_network_call_and_uses_fallback(monkeypatch):
    from backend import brief_agent

    monkeypatch.setattr("backend.config.AI_MODE", "disabled")
    _forbid_network(monkeypatch)
    event = {"event_class": "collision_risk", "tca_timestamp": "2026-07-21T04:12:00+00:00",
             "miss_distance_km": 0.84, "rel_velocity_km_s": 14.2}
    out = brief_agent.generate_and_review_brief(event, 8.1e-5, "High", 0.05, "CARTOSAT-3", "DEB",
                                                object_b_type="debris", criticality="Tier2")
    assert out["generated_by"] == "fallback_template" and out["review_status"] == "not_applicable"
    assert out["brief_text"]
    with pytest.raises(brief_agent.AIDisabledError):
        brief_agent._call_ollama("prompt")


def test_ai_disabled_health_reports_fallback_without_network(monkeypatch):
    from backend.db import init_db
    from backend.main import app
    from backend.tests._asgi import request

    init_db()

    monkeypatch.setattr("backend.config.AI_MODE", "disabled")
    _forbid_network(monkeypatch)
    status, _, body = request(app, "GET", "/api/health")
    assert status == 200
    assert body["ai"] == {"mode": "disabled", "available": False, "status": "AI_FALLBACK_ACTIVE"}
    assert body["ollama_reachable"] is False


def test_ai_reachable_reports_local_ai_available(monkeypatch):
    from backend.db import init_db
    from backend.main import app
    from backend.tests._asgi import request

    init_db()

    monkeypatch.setattr("backend.main._ollama_reachable", lambda: True)
    body = request(app, "GET", "/api/health")[2]
    assert body["ai"] == {"mode": "ollama", "available": True, "status": "LOCAL_AI_AVAILABLE"}


def test_ai_disabled_pipeline_still_computes_events_and_risk(demo_db, monkeypatch):
    from backend.db import get_event_with_brief, list_events
    from backend.demo_seed import seed_event

    monkeypatch.setattr("backend.config.AI_MODE", "disabled")
    _forbid_network(monkeypatch)
    result = seed_event(asset_hint="TEST-ASSET", proximity=False, debris_hint="TEST-DEBRIS")
    event = get_event_with_brief(result["event_id"])
    assert event["event_class"] == "collision_risk"
    assert event["pc_score"] is not None and event["risk_tier"] and event["priority_score"] is not None
    assert event["generated_by"] == "fallback_template" and event["brief_text"]
    assert list_events()


def test_ai_unreachable_pipeline_still_computes_events(demo_db, monkeypatch):
    """demo_db makes every Ollama call fail with ConnectionError."""
    from backend.db import get_event_with_brief
    from backend.demo_seed import seed_event

    assert importlib.import_module("backend.config").AI_MODE == "ollama"
    result = seed_event(asset_hint="TEST-ASSET", proximity=True, foreign_hint="TEST-FOREIGN")
    event = get_event_with_brief(result["event_id"])
    assert event["event_class"] == "proximity_watch" and event["risk_tier"]
    assert event["generated_by"] == "fallback_template" and event["brief_text"]


def test_no_external_ai_provider_in_backend():
    src = "\n".join(p.read_text(encoding="utf-8") for p in (REPO_ROOT / "backend").glob("*.py"))
    for forbidden in ("api.openai.com", "generativelanguage.googleapis", "OPENAI_API_KEY"):
        assert forbidden not in src


def test_ai_mode_is_evaluated_at_call_time(monkeypatch):
    # Sanity: the disabled check is evaluated at call time (config attribute),
    # so flipping it back restores the normal (here: unreachable) path.
    from backend import brief_agent

    monkeypatch.setattr("backend.config.AI_MODE", "ollama")

    def _down(*a, **k):
        raise requests.exceptions.ConnectionError("down")
    monkeypatch.setattr("backend.brief_agent.requests.post", _down)
    with pytest.raises(requests.exceptions.ConnectionError):
        brief_agent._call_ollama("p")
