"""Local brief translation: disabled by default, no heavy imports, protected
tokens survive or we fall back to English, nothing is written to the DB."""

import builtins
import sqlite3

import pytest

from backend import translation
from backend.translation import (
    IndicTrans2TranslationService,
    LocalTranslationService,
    UnavailableTranslationService,
    build_translation_service,
    mask_protected,
    protected_tokens_preserved,
    unmask_protected,
)

SAMPLE_BRIEF = (
    "CARTOSAT-3 (NORAD 44804) will pass within 0.412 km of COSMOS 2251 DEB at TCA "
    "2026-07-21T04:12:00+00:00, relative velocity 11.83 km/s. Pc 2.31e-04 (1 in 4,329); "
    "risk tier Critical. SGP4 propagation from TLE data. Illustrative Δv ≈ 0.25 m/s."
)
HEAVY = ("torch", "transformers", "IndicTransToolkit")


@pytest.fixture
def block_heavy_imports(monkeypatch):
    real_import = builtins.__import__
    attempted = []

    def guarded(name, *args, **kwargs):
        if name.split(".")[0] in HEAVY:
            attempted.append(name)
            raise AssertionError(f"heavy import attempted: {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    return attempted


@pytest.fixture(autouse=True)
def _reset_service():
    translation.reset_translation_service(None)
    yield
    translation.reset_translation_service(None)


class FakeTranslator(LocalTranslationService):
    """Pretends to translate: prefixes each segment, optionally corrupting a number."""

    engine = "fake"

    def __init__(self, corrupt=False, drop_placeholder=False):
        self.corrupt = corrupt
        self.drop_placeholder = drop_placeholder
        self.calls = 0

    def status(self):
        return {"available": True, "state": "loaded", "engine": self.engine, "reason": None}

    def _translate_segments(self, segments, source_tag, target_tag):
        self.calls += 1
        out = []
        for s in segments:
            t = "अनुवाद: " + s
            if self.drop_placeholder:
                t = t.replace("<P0>", "")
            out.append(t)
        return out

    def translate(self, text, source_language="en", target_language="en", protected_terms=()):
        r = super().translate(text, source_language, target_language, protected_terms)
        return r


class CorruptingTranslator(FakeTranslator):
    """Restores placeholders but then the model output contains a changed number."""

    def _translate_segments(self, segments, source_tag, target_tag):
        return ["अनुवाद: " + s.replace("<P1>", "0.999 km") for s in segments]


def test_disabled_by_default(monkeypatch, block_heavy_imports):
    monkeypatch.delenv("LOCAL_TRANSLATION_ENABLED", raising=False)
    svc = build_translation_service()
    assert isinstance(svc, UnavailableTranslationService)
    st = svc.status()
    assert st["available"] is False
    assert st["state"] == "disabled"
    r = svc.translate(SAMPLE_BRIEF, "en", "hi")
    assert r["ok"] is False and r["status"] == "unavailable"
    assert r["text"] == SAMPLE_BRIEF
    assert block_heavy_imports == []


def test_disabled_never_imports_transformers(monkeypatch, block_heavy_imports):
    monkeypatch.setenv("LOCAL_TRANSLATION_ENABLED", "false")
    svc = translation.get_translation_service()
    svc.status()
    svc.translate_brief(SAMPLE_BRIEF, "Δv ≈ 0.25 m/s", "ta")
    assert block_heavy_imports == []


def test_enabled_but_model_absent_is_unavailable(monkeypatch, tmp_path, block_heavy_imports):
    monkeypatch.setenv("LOCAL_TRANSLATION_ENABLED", "true")
    monkeypatch.setenv("TRANSLATION_MODEL", str(tmp_path / "no-such-model"))
    monkeypatch.setattr(translation, "_deps_present", lambda: [])  # pretend deps exist
    svc = build_translation_service()
    assert isinstance(svc, IndicTrans2TranslationService)
    st = svc.status()
    assert st["available"] is False
    assert "not found locally" in st["reason"]
    r = svc.translate(SAMPLE_BRIEF, "en", "hi")
    assert r["ok"] is False and r["status"] == "unavailable" and r["text"] == SAMPLE_BRIEF
    assert block_heavy_imports == []


def test_enabled_repo_id_not_in_cache_is_unavailable(monkeypatch, tmp_path, block_heavy_imports):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    monkeypatch.setattr(translation, "_deps_present", lambda: [])
    svc = IndicTrans2TranslationService("ai4bharat/indictrans2-en-indic-dist-200M")
    assert svc.status()["available"] is False
    assert block_heavy_imports == []


def test_enabled_missing_dependencies_is_unavailable(monkeypatch, block_heavy_imports):
    monkeypatch.setattr(translation, "_deps_present", lambda: ["torch", "transformers"])
    svc = IndicTrans2TranslationService("whatever")
    st = svc.status()
    assert st["available"] is False and "not installed" in st["reason"]


def test_masking_round_trip_keeps_numbers_and_ids():
    masked, tokens = mask_protected(SAMPLE_BRIEF)
    for must in ("44804", "0.412 km", "11.83 km/s", "2.31e-04", "2026-07-21T04:12:00+00:00",
                 "SGP4", "TLE", "TCA", "Pc", "Critical", "0.25 m/s", "CARTOSAT-3", "4,329"):
        assert any(must in t for t in tokens), must
        assert must not in masked.replace("<P", "\0"), must
    import re
    assert not re.search(r"\d", re.sub(r"<P\d+>", "", masked)), masked
    assert unmask_protected(masked, tokens) == SAMPLE_BRIEF
    assert protected_tokens_preserved(SAMPLE_BRIEF, SAMPLE_BRIEF)


def test_unmask_tolerates_spacing_and_native_digits():
    masked, tokens = mask_protected("Miss 0.412 km at TCA")
    mangled = masked.replace("<P0>", "< P ० >")
    assert unmask_protected(mangled, tokens) == "Miss 0.412 km at TCA"


def test_unmask_rejects_missing_placeholder():
    masked, tokens = mask_protected("Miss 0.412 km at TCA")
    with pytest.raises(ValueError):
        unmask_protected(masked.replace("<P0>", ""), tokens)


def test_fake_translation_succeeds_and_preserves_tokens():
    svc = FakeTranslator()
    r = svc.translate(SAMPLE_BRIEF, "en", "hi")
    assert r["ok"] is True and r["status"] == "translated"
    assert "अनुवाद" in r["text"]
    assert protected_tokens_preserved(SAMPLE_BRIEF, r["text"])


def test_translator_dropping_a_token_falls_back_to_english():
    r = FakeTranslator(drop_placeholder=True).translate(SAMPLE_BRIEF, "en", "hi")
    assert r["ok"] is False and r["status"] == "failed"
    assert r["text"] == SAMPLE_BRIEF


def test_translator_corrupting_a_number_falls_back_to_english():
    r = CorruptingTranslator().translate(SAMPLE_BRIEF, "en", "hi")
    assert r["ok"] is False and r["status"] == "failed"
    assert r["text"] == SAMPLE_BRIEF


def test_brief_is_all_or_nothing():
    res = CorruptingTranslator().translate_brief(SAMPLE_BRIEF, "Δv ≈ 0.25 m/s along-track", "hi")
    assert res["status"] == "failed"
    assert res["texts"] == {"brief_text": SAMPLE_BRIEF, "maneuver_text": "Δv ≈ 0.25 m/s along-track"}


def test_english_target_is_passthrough():
    r = FakeTranslator().translate(SAMPLE_BRIEF, "en", "en")
    assert r["ok"] and r["status"] == "source_language" and r["text"] == SAMPLE_BRIEF


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------
@pytest.fixture
def tr_db(tmp_path, monkeypatch):
    db_file = tmp_path / "translation_test.db"
    monkeypatch.setattr("backend.db.DB_PATH", db_file)
    from backend.db import init_db, insert_brief, insert_event, upsert_object
    init_db()
    upsert_object("44804", "CARTOSAT-3", "satellite", "India", "Tier1", "l1", "l2")
    upsert_object("2", "DEB", "debris", None, None, "l1", "l2")
    event_id = insert_event("44804", "2", "collision_risk", "2026-07-21T04:12:00+00:00", 0.412, 11.83,
                            2.31e-4, "Critical", 600.0, is_demo=False, screening_run_id=1)
    insert_brief(event_id, SAMPLE_BRIEF, "Δv ≈ 0.25 m/s along-track", 0.25, "llm", review_status="consistent")
    return event_id, db_file


def _snapshot(db_file):
    conn = sqlite3.connect(db_file)
    try:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        return {t: conn.execute(f"SELECT * FROM {t}").fetchall() for t in tables}
    finally:
        conn.close()


def test_status_endpoint_disabled(monkeypatch):
    monkeypatch.delenv("LOCAL_TRANSLATION_ENABLED", raising=False)
    from backend.translation_api import translation_status
    st = translation_status()
    assert st["available"] is False
    assert st["persisted"] is False and st["source_of_record"] == "en"
    assert "hi" in st["supported_languages"]


def test_endpoint_returns_english_fallback_when_unavailable(monkeypatch, tr_db):
    event_id, db_file = tr_db
    monkeypatch.delenv("LOCAL_TRANSLATION_ENABLED", raising=False)
    from backend.translation_api import BriefTranslationRequest, translate_brief
    before = _snapshot(db_file)
    res = translate_brief(BriefTranslationRequest(event_id=event_id, target_language="hi"))
    assert res["translated"] is False
    assert res["status"] == "unavailable"
    assert res["brief_text"] == SAMPLE_BRIEF
    assert res["maneuver_text"] == "Δv ≈ 0.25 m/s along-track"
    assert res["persisted"] is False
    assert _snapshot(db_file) == before


def test_endpoint_translates_without_writing_db(monkeypatch, tr_db):
    event_id, db_file = tr_db
    translation.reset_translation_service(FakeTranslator())
    import backend.db as db
    # Any write helper being called would be a bug.
    for name in ("insert_brief", "insert_event", "insert_decision", "set_brief_status", "upsert_object"):
        if hasattr(db, name):
            monkeypatch.setattr(db, name, lambda *a, **k: (_ for _ in ()).throw(AssertionError("DB write")))
    from backend.translation_api import BriefTranslationRequest, translate_brief
    before = _snapshot(db_file)
    res = translate_brief(BriefTranslationRequest(event_id=event_id, target_language="ta"))
    assert res["translated"] is True and res["machine_translation"] is True
    assert "अनुवाद" in res["brief_text"]
    assert res["english"]["brief_text"] == SAMPLE_BRIEF
    assert protected_tokens_preserved(SAMPLE_BRIEF, res["brief_text"])
    assert _snapshot(db_file) == before


def test_endpoint_corrupt_translation_returns_english(tr_db):
    event_id, _ = tr_db
    translation.reset_translation_service(CorruptingTranslator())
    from backend.translation_api import BriefTranslationRequest, translate_brief
    res = translate_brief(BriefTranslationRequest(event_id=event_id, target_language="hi"))
    assert res["translated"] is False and res["status"] == "failed"
    assert res["brief_text"] == SAMPLE_BRIEF


def test_endpoint_rejects_unknown_language_and_missing_event(tr_db):
    from fastapi import HTTPException
    from backend.translation_api import BriefTranslationRequest, translate_brief
    with pytest.raises(HTTPException) as exc:
        translate_brief(BriefTranslationRequest(event_id=tr_db[0], target_language="xx"))
    assert exc.value.status_code == 400
    with pytest.raises(HTTPException) as exc:
        translate_brief(BriefTranslationRequest(event_id=999999, target_language="hi"))
    assert exc.value.status_code == 404


def test_endpoint_accepts_structured_texts():
    translation.reset_translation_service(FakeTranslator())
    from backend.translation_api import BriefTranslationRequest, translate_brief
    res = translate_brief(BriefTranslationRequest(brief_text="Miss 0.412 km at TCA.", target_language="bn"))
    assert res["translated"] is True
    assert "0.412 km" in res["brief_text"] and "TCA" in res["brief_text"]


def test_translation_modules_reference_no_external_hosts():
    import inspect
    import backend.translation_api as api_mod
    src = inspect.getsource(translation) + inspect.getsource(api_mod)
    for host in ("translate.googleapis", "microsofttranslator", "translate.amazonaws", "bhashini", "http://", "https://"):
        assert host not in src.lower()


def test_full_object_names_and_digit_led_designators_are_masked():
    from backend.translation import mask_protected

    text = "FENGYUN 1C DEB passes CARTOSAT-3; Pc 3.5×10^-5 with RISAT 2BR1."
    masked, tokens = mask_protected(text, extra_terms=["FENGYUN 1C DEB"])
    assert "FENGYUN 1C DEB" in tokens
    assert "3.5×10^-5" in tokens and "2BR1" in tokens
    # nothing of the catalog names or numbers is left for the model
    for frag in ("1C", "DEB", "2BR1", "10^-5"):
        assert frag not in masked
