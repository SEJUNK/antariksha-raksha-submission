"""Demo seeding vs /api/refresh concurrency (pipeline.PIPELINE_LOCK).

Each sequence must be atomic w.r.t. the other:
  demo:    select objects -> set demo override -> demo screening -> match event
  refresh: ingest -> clear demo overrides -> real screening -> run record
Real threads drive the real screening pipeline against the synthetic-TLE DB
from test_demo_seed.py; only ingest (network) is stubbed and Ollama is
unreachable (template briefs). Small sleeps widen the race windows so that,
without the lock, these interleavings would actually occur."""

import json
import threading
import time

from backend.tests.test_demo_seed import demo_db  # noqa: F401  (fixture)

JOIN_TIMEOUT_S = 120


def _seed():
    from backend.demo_seed import seed_event
    return seed_event(asset_hint="TEST-ASSET", proximity=False, debris_hint="TEST-DEBRIS")


def _run(target, results, key):
    def wrapper():
        try:
            results[key] = target()
        except BaseException as exc:  # re-raised in the main thread via assertions
            results[key] = exc
    t = threading.Thread(target=wrapper, name=key)
    t.start()
    return t


def _join(*threads):
    for t in threads:
        t.join(JOIN_TIMEOUT_S)
        assert not t.is_alive(), f"{t.name} did not finish (deadlock?)"


def _stub_ingest(monkeypatch, on_start=None):
    from backend import pipeline

    def fake_ingest():
        if on_start:
            on_start()
        return {"satellite": 1, "debris": 1, "foreign_sat": 1}, True
    monkeypatch.setattr(pipeline, "run_ingest", fake_ingest)


def test_refresh_in_flight_does_not_drop_demo_override(demo_db, monkeypatch):
    """Demo requested while a refresh is mid-ingest: the demo must wait for
    the whole refresh (whose screening clears overrides) instead of writing
    its override first and having it wiped before its own screening."""
    from backend import demo_seed
    from backend.db import get_event_with_brief, list_demo_overrides
    from backend.pipeline import run_full_pipeline

    order = []
    ingest_started = threading.Event()

    def slow_ingest_start():
        order.append("ingest_start")
        ingest_started.set()
        time.sleep(0.5)  # demo thread is started meanwhile
    _stub_ingest(monkeypatch, slow_ingest_start)

    real_set = demo_seed.set_demo_override

    def recording_set(**kw):
        order.append("demo_override_set")
        return real_set(**kw)
    monkeypatch.setattr(demo_seed, "set_demo_override", recording_set)

    results = {}

    def refresh():
        summary = run_full_pipeline()
        order.append("refresh_done")
        return summary

    t_refresh = _run(refresh, results, "refresh")
    assert ingest_started.wait(30)
    t_demo = _run(_seed, results, "demo")
    _join(t_refresh, t_demo)

    assert not isinstance(results["refresh"], BaseException), results["refresh"]
    assert not isinstance(results["demo"], BaseException), results["demo"]
    assert order == ["ingest_start", "refresh_done", "demo_override_set"]
    # Demo ran last and intact: its event exists, flagged demo, override kept.
    event = get_event_with_brief(results["demo"]["event_id"])
    assert event["is_demo"] == 1
    assert [o["norad_id"] for o in list_demo_overrides()] == ["90002"]
    assert results["refresh"]["mode"] == "cached"


def test_demo_in_flight_blocks_whole_refresh(demo_db, monkeypatch):
    """Refresh requested mid demo-screening: refresh (including ingest)
    waits; the override is never cleared under the demo; the demo's event is
    demo-flagged; the later refresh produces only real, non-demo events."""
    from backend import demo_seed, pipeline
    from backend.db import latest_screening_run, list_demo_overrides, list_events
    from backend.pipeline import run_full_pipeline

    order = []
    demo_screening = threading.Event()
    overrides_during_demo = []

    def ingest_start():
        order.append("ingest_start")
    _stub_ingest(monkeypatch, ingest_start)

    real_propagate = pipeline.propagate_all

    def slow_propagate(*args, **kwargs):
        objects = kwargs.get("objects") or (args[0] if args else [])
        if any(o.get("demo_adjusted") for o in objects):
            demo_screening.set()
            time.sleep(0.5)  # refresh thread is started meanwhile
            overrides_during_demo.append(len(list_demo_overrides()))
        return real_propagate(*args, **kwargs)
    monkeypatch.setattr(pipeline, "propagate_all", slow_propagate)

    matched = []
    real_find = demo_seed._find_seeded_event

    def recording_find(*args, **kwargs):
        evt = real_find(*args, **kwargs)
        matched.append(evt)
        order.append("demo_event_matched")
        return evt
    monkeypatch.setattr(demo_seed, "_find_seeded_event", recording_find)

    results = {}
    t_demo = _run(_seed, results, "demo")
    assert demo_screening.wait(60)
    t_refresh = _run(run_full_pipeline, results, "refresh")
    _join(t_demo, t_refresh)

    assert not isinstance(results["demo"], BaseException), results["demo"]
    assert not isinstance(results["refresh"], BaseException), results["refresh"]
    assert overrides_during_demo == [1]               # not lost mid demo screening
    assert order == ["demo_event_matched", "ingest_start"]  # ingest is inside the lock
    assert matched[0] is not None and matched[0]["is_demo"] == 1

    # Refresh ran last: real data only, nothing demo leaks into the live run.
    assert list_demo_overrides() == []
    events = list_events()
    assert all(e["is_demo"] == 0 for e in events)
    assert not any({e["object_a_id"], e["object_b_id"]} == {"90001", "90002"} for e in events)
    run = latest_screening_run()
    assert run["mode"] != "demo" and run["demo_scenario_json"] is None


def test_interleaved_demo_and_refresh_never_mix(demo_db, monkeypatch):
    """Several demos and refreshes fired at once: every demo succeeds,
    every demo screening run is demo-only and every normal run is real-only
    (no demo-adjusted object screened, no demo-flagged event)."""
    from backend import pipeline
    from backend.db import get_screening_run
    from backend.pipeline import run_full_pipeline

    _stub_ingest(monkeypatch, lambda: time.sleep(0.05))

    screened = []  # (thread name, any demo-adjusted object screened)
    real_propagate = pipeline.propagate_all

    def recording_propagate(*args, **kwargs):
        objects = kwargs.get("objects") or (args[0] if args else [])
        screened.append((threading.current_thread().name, any(o.get("demo_adjusted") for o in objects)))
        return real_propagate(*args, **kwargs)
    monkeypatch.setattr(pipeline, "propagate_all", recording_propagate)

    inserted = []  # (screening_run_id, is_demo)
    real_insert = pipeline.insert_event

    def recording_insert(**kw):
        inserted.append((kw["screening_run_id"], bool(kw["is_demo"])))
        return real_insert(**kw)
    monkeypatch.setattr(pipeline, "insert_event", recording_insert)

    results, threads = {}, []
    for i in range(3):
        threads.append(_run(_seed, results, f"demo-{i}"))
        threads.append(_run(run_full_pipeline, results, f"refresh-{i}"))
    _join(*threads)

    for key, value in results.items():
        assert not isinstance(value, BaseException), f"{key}: {value!r}"
    assert len(screened) == 6
    for name, had_demo_object in screened:
        assert had_demo_object is name.startswith("demo-"), (name, had_demo_object)

    for run_id, is_demo in inserted:
        run = get_screening_run(run_id)
        assert (run["mode"] == "demo") is is_demo
        if not is_demo:
            assert run["demo_scenario_json"] is None
        else:
            assert json.loads(run["demo_scenario_json"])["scenario"] == "collision_demo"
    assert any(d for _, d in inserted)  # demos did produce demo events


def test_pipeline_lock_is_reentrant():
    """seed_event / run_full_pipeline hold PIPELINE_LOCK and then call
    run_screening_and_briefs, which takes it again on the same thread."""
    from backend.pipeline import PIPELINE_LOCK

    with PIPELINE_LOCK:
        assert PIPELINE_LOCK.acquire(timeout=1)
        PIPELINE_LOCK.release()
