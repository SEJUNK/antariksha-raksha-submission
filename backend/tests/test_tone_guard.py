"""Deterministic AI tone guard (brief_agent.tone_guard_issues) and its
regenerate-once / template-fallback path in generate_and_review_brief."""

import pytest

from backend import brief_agent
from backend.tests.test_brief_agent import (
    COLLISION_EVENT,
    PROXIMITY_EVENT,
    FakeOllama,
    _run,
)

CLEAN = "CARTOSAT-3 will pass 0.84 km from FENGYUN 1C DEB."
SENSATIONAL = "A catastrophic collision between CARTOSAT-3 and FENGYUN 1C DEB is inevitable."
NUMERIC_BAD = "CARTOSAT-3 will pass 2 km from FENGYUN 1C DEB."


@pytest.fixture(autouse=True)
def _no_feedback(monkeypatch):
    monkeypatch.setattr(brief_agent, "recent_rejection_reasons", lambda limit=3: [])


# ---------- detector ----------

@pytest.mark.parametrize("term", [
    "catastrophic", "disastrous", "devastating", "inevitable", "inevitably",
    "guarantee", "guarantees", "guaranteed", "unavoidable", "unavoidably",
    "certain", "certainly", "imminent", "imminently",
])
def test_each_banned_term_is_flagged(term):
    assert brief_agent.tone_guard_issues(f"The outcome is {term} here.") == [term]
    # case-insensitive
    assert brief_agent.tone_guard_issues(f"{term.upper()}: close approach.") == [term]


@pytest.mark.parametrize("text", [
    "Critical",
    "Risk tier: Critical. Critical risk tier assigned.",
    "High priority close approach; collision indicator is elevated.",
    "An elevated indicator was computed for this close approach.",
    "There is significant uncertainty in the position estimate.",
    "The miss distance is uncertain; uncertainties grow with time.",
    "Ascertain the tracking status before TCA.",
])
def test_legitimate_terms_are_not_flagged(text):
    assert brief_agent.tone_guard_issues(text) == []


def test_uncertainty_is_not_flagged_but_certain_is():
    assert brief_agent.tone_guard_issues("uncertainty") == []
    assert brief_agent.tone_guard_issues("uncertain") == []
    assert brief_agent.tone_guard_issues("Uncertainty is high, but impact is certain.") == ["certain"]


def test_multiple_terms_reported_in_order_once():
    assert brief_agent.tone_guard_issues(SENSATIONAL + " Truly catastrophic.") == ["catastrophic", "inevitable"]


# ---------- generate_and_review_brief behaviour ----------

def test_sensational_first_draft_then_clean_redraft_keeps_llm_brief(monkeypatch):
    fake = FakeOllama(["APPROVED. Fine."], briefs=[SENSATIONAL, CLEAN])
    monkeypatch.setattr(brief_agent, "_call_ollama", fake)
    out = _run()
    assert out["generated_by"] == "llm"
    assert out["brief_text"] == CLEAN
    assert out["review_status"] == "consistent"
    assert out["reviewer_notes"] is None
    # draft, redraft, review -- the sensational draft is not sent for LLM review
    assert len(fake.prompts) == 3


def test_sensational_twice_falls_back_to_template(monkeypatch):
    fake = FakeOllama([], briefs=[SENSATIONAL, "Collision is guaranteed and imminent."])
    monkeypatch.setattr(brief_agent, "_call_ollama", fake)
    out = _run()
    assert out["generated_by"] == "fallback_template"
    assert out["review_status"] == brief_agent.TONE_GUARD_REVIEW_STATUS == "tone_guard_fallback"
    assert out["reviewer_notes"] == (
        "Tone guard: the LLM draft used unsupported sensational wording (e.g. 'guaranteed') twice; "
        "a deterministic template brief is shown."
    )
    assert out["brief_text"].startswith("CONJUNCTION ASSESSMENT:")
    assert "0.84 km" in out["brief_text"]
    assert brief_agent.tone_guard_issues(out["brief_text"]) == []
    assert "Illustrative" in out["maneuver_text"]
    assert len(fake.prompts) == 2  # no LLM review of either sensational draft


def test_sensational_twice_proximity_falls_back_to_proximity_template(monkeypatch):
    fake = FakeOllama([], briefs=[SENSATIONAL, SENSATIONAL])
    monkeypatch.setattr(brief_agent, "_call_ollama", fake)
    out = _run(event=PROXIMITY_EVENT)
    assert out["generated_by"] == "fallback_template"
    assert out["review_status"] == "tone_guard_fallback"
    assert out["brief_text"].startswith("PROXIMITY WATCH:")
    assert "'catastrophic'" in out["reviewer_notes"]


def test_fact_issue_then_sensational_redraft_falls_back_to_template(monkeypatch):
    fake = FakeOllama(["FLAGGED. Wrong."], briefs=[NUMERIC_BAD, SENSATIONAL])
    monkeypatch.setattr(brief_agent, "_call_ollama", fake)
    out = _run()
    assert out["generated_by"] == "fallback_template"
    assert out["review_status"] == "tone_guard_fallback"
    assert out["reviewer_notes"].startswith("Tone guard: the regenerated LLM draft")


def test_tone_guard_does_not_change_numeric_fact_check(monkeypatch):
    """A numeric error with clean tone still follows today's path exactly
    (flagged after one regeneration, LLM brief kept, no tone note)."""
    fake = FakeOllama(["FLAGGED. Wrong distance.", "FLAGGED. Still wrong."], briefs=[NUMERIC_BAD, NUMERIC_BAD])
    monkeypatch.setattr(brief_agent, "_call_ollama", fake)
    out = _run()
    assert out["generated_by"] == "llm"
    assert out["review_status"] == "flagged"
    assert "number '2 km' is not in the facts" in out["reviewer_notes"]
    assert "Tone guard" not in out["reviewer_notes"]
    assert len(fake.prompts) == 4
    # fact_check_brief itself is unaffected by tone words
    args = (COLLISION_EVENT, 8.1e-5, "High", None, "CARTOSAT-3", "FENGYUN 1C DEB")
    assert brief_agent.fact_check_brief(CLEAN + " It is certain.", *args) == \
        brief_agent.fact_check_brief(CLEAN, *args)


def test_sensational_then_numeric_error_redraft_is_flagged_not_template(monkeypatch):
    fake = FakeOllama(["FLAGGED. Wrong."], briefs=[SENSATIONAL, NUMERIC_BAD])
    monkeypatch.setattr(brief_agent, "_call_ollama", fake)
    out = _run()
    assert out["generated_by"] == "llm"
    assert out["review_status"] == "flagged"
    assert out["reviewer_notes"].startswith(brief_agent.REVIEW_FLAG_PREFIX)


# ---------- the template itself is clean ----------

@pytest.mark.parametrize("tier", ["Low", "Medium", "High", "Critical"])
@pytest.mark.parametrize("pc", [0.0, 8.1e-5, 7.67e-3, 0.5])
def test_collision_template_never_contains_banned_terms(tier, pc):
    out = brief_agent.template_brief(COLLISION_EVENT, pc, tier, 0.05, "CARTOSAT-3", "FENGYUN 1C DEB",
                                     object_b_type="debris", criticality="Tier1")
    assert out["generated_by"] == "fallback_template"
    assert brief_agent.tone_guard_issues(out["brief_text"]) == []
    assert brief_agent.tone_guard_issues(out["maneuver_text"]) == []
    none_dv = brief_agent.template_brief(COLLISION_EVENT, pc, tier, None, "A", "B")
    assert brief_agent.tone_guard_issues(none_dv["brief_text"] + " " + none_dv["maneuver_text"]) == []


def test_proximity_template_never_contains_banned_terms():
    out = brief_agent.template_brief(PROXIMITY_EVENT, 0.0, "High", None, "CARTOSAT-3", "SAT-X")
    assert brief_agent.tone_guard_issues(out["brief_text"] + " " + out["maneuver_text"]) == []
