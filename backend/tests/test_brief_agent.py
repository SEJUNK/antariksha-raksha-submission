"""Tests for brief_agent and maneuver (no network: Ollama mocked)."""

import pytest

from backend import brief_agent, maneuver

COLLISION_EVENT = {
    "event_class": "collision_risk",
    "tca_timestamp": "2026-07-21T04:12:00+00:00",
    "miss_distance_km": 0.84,
    "rel_velocity_km_s": 14.2,
    "pos_a_tca_km": [7000.0, 1.0, 2.0],
    "pos_b_tca_km": [7000.5, 1.0, 2.0],
}
PROXIMITY_EVENT = {
    "event_class": "proximity_watch",
    "tca_timestamp": "2026-07-21T04:12:00+00:00",
    "min_distance_km": 3.21,
    "dwell_minutes": 42.0,
    "geometry": "co-planar",
    "watch_km": 25.0,
}
DV = maneuver.estimate_delta_v(14.2)


@pytest.fixture(autouse=True)
def _no_feedback(monkeypatch):
    monkeypatch.setattr(brief_agent, "recent_rejection_reasons", lambda limit=3: [])


def _run(pc=8.1e-5, event=COLLISION_EVENT, **kw):
    return brief_agent.generate_and_review_brief(
        event, pc, "High", DV if event["event_class"] == "collision_risk" else None,
        "CARTOSAT-3", "FENGYUN 1C DEB", object_b_type="debris", **kw,
    )


class FakeOllama:
    """Scripted _call_ollama: drafter calls return a brief, reviewer calls pop from `reviews`."""

    def __init__(self, reviews, briefs=None):
        self.reviews = list(reviews)
        self.briefs = list(briefs or [])
        self.prompts = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        if prompt.startswith("You are performing a consistency check"):
            r = self.reviews.pop(0)
            if isinstance(r, Exception):
                raise r
            return r
        return self.briefs.pop(0) if self.briefs else "Drafted brief text."


def _ollama_down(*a, **k):
    raise ConnectionError("ollama down")


# ---------- fallback path ----------

def test_fallback_collision(monkeypatch):
    monkeypatch.setattr(brief_agent.requests, "post", _ollama_down)
    out = _run()
    assert out["generated_by"] == "fallback_template"
    assert out["review_status"] == "not_applicable"
    assert out["reviewer_notes"] is None
    assert "0.84 km" in out["brief_text"]
    assert "8.10e-05" in out["brief_text"]
    assert "1 in 12,346" in out["brief_text"]
    assert "14.20 km/s" in out["brief_text"]
    assert "human operator" in out["brief_text"]
    assert "radial burn" not in out["brief_text"].lower()
    assert "recommend" not in out["brief_text"].lower()
    assert out["maneuver_text"] == maneuver.maneuver_for_event("collision_risk", 14.2)["maneuver_text"]


def test_fallback_pc_zero_is_numerically_zero_not_resolution(monkeypatch):
    """The analytic indicator is continuous: 0 only means below double
    precision, never 'below a Monte Carlo resolution'."""
    monkeypatch.setattr(brief_agent.requests, "post", _ollama_down)
    out = _run(pc=0.0)
    assert "effectively zero (below double-precision range)" in out["brief_text"]
    assert "Monte Carlo" not in out["brief_text"]


def test_fallback_proximity_intent_neutral(monkeypatch):
    monkeypatch.setattr(brief_agent.requests, "post", _ollama_down)
    out = _run(event=PROXIMITY_EVENT)
    text = out["brief_text"].lower()
    assert out["generated_by"] == "fallback_template"
    assert out["review_status"] == "not_applicable"
    assert "3.21 km" in out["brief_text"]
    assert "nearby satellite" in text
    for word in ("hostile", "suspicious", "threat", "accus"):
        assert word not in text
    assert out["maneuver_text"] == maneuver.NO_MANEUVER_TEXT


# ---------- LLM + consistency review ----------

def test_llm_reviewer_approved(monkeypatch):
    fake = FakeOllama(["APPROVED. Matches data."])
    monkeypatch.setattr(brief_agent, "_call_ollama", fake)
    out = _run()
    assert out["generated_by"] == "llm"
    assert out["review_status"] == "consistent"
    assert out["reviewer_notes"] is None
    assert "Illustrative" in out["maneuver_text"]
    # reviewer sees the drafter's data block (no bulky position lists), incl. Pc / tier
    review_prompt = fake.prompts[1]
    assert "pos_a_tca_km" not in review_prompt
    assert "Miss distance: 0.84 km" in review_prompt
    assert "Risk tier: High" in review_prompt


def test_fact_check_issue_twice_is_flagged(monkeypatch):
    bad = "CARTOSAT-3 will pass 2 km from FENGYUN 1C DEB."
    fake = FakeOllama(["FLAGGED. Wrong distance.", "FLAGGED. Still wrong."], briefs=[bad, bad])
    monkeypatch.setattr(brief_agent, "_call_ollama", fake)
    out = _run()
    assert out["generated_by"] == "llm"
    assert out["review_status"] == "flagged"
    assert out["reviewer_notes"].startswith(brief_agent.REVIEW_FLAG_PREFIX + brief_agent.FACT_CHECK_NOTE_PREFIX)
    assert "number '2 km' is not in the facts" in out["reviewer_notes"]
    assert "Still wrong" in out["reviewer_notes"]  # LLM note kept for context
    assert len(fake.prompts) == 4  # draft, review, redraft, review


def test_fact_check_issue_then_clean_redraft_is_consistent(monkeypatch):
    bad = "CARTOSAT-3 will pass 2 km from FENGYUN 1C DEB."
    good = "CARTOSAT-3 will pass 0.84 km from FENGYUN 1C DEB."
    fake = FakeOllama(["FLAGGED. Wrong.", "APPROVED. Fine."], briefs=[bad, good])
    monkeypatch.setattr(brief_agent, "_call_ollama", fake)
    out = _run()
    assert out["review_status"] == "consistent"
    assert out["reviewer_notes"] is None
    assert out["brief_text"] == good


def test_llm_flag_without_fact_issue_is_advisory_not_regenerated(monkeypatch):
    fake = FakeOllama(["FLAGGED. Wrong.", "APPROVED. Fine."])
    monkeypatch.setattr(brief_agent, "_call_ollama", fake)
    out = _run()
    assert out["review_status"] == "consistent"
    assert out["reviewer_notes"] == brief_agent.REVIEW_ADVISORY_PREFIX + "FLAGGED. Wrong."
    assert len(fake.prompts) == 2


def test_llm_reviewer_unreachable_is_skipped(monkeypatch):
    fake = FakeOllama([ConnectionError("down during review")])
    monkeypatch.setattr(brief_agent, "_call_ollama", fake)
    out = _run()
    assert out["generated_by"] == "llm"
    assert out["review_status"] == "skipped"
    assert out["reviewer_notes"] == "Consistency review skipped: local LLM unreachable during review."


# ---------- prompts ----------

def test_collision_prompt_content():
    prompt = brief_agent.build_prompt(COLLISION_EVENT, 8.1e-5, "High", DV, "CARTOSAT-3", "FENGYUN 1C DEB", "debris")
    assert "2026-07-21T04:12:00+00:00" in prompt
    assert "0.84 km" in prompt
    assert "14.20 km/s" in prompt
    assert "8.10e-05 (1 in 12,346)" in prompt
    assert "Risk tier: High" in prompt
    assert f"~{DV:.2f} m/s" in prompt
    assert "recommend an avoidance maneuver" not in prompt.lower()
    assert "recommending an avoidance maneuver" not in prompt.lower()
    assert "illustrative" in prompt.lower()
    assert "human operator" in prompt
    assert "Do not invent numbers" in prompt
    assert "Do not state or alter the TCA, miss distance, collision probability or risk tier" in prompt
    assert "space situational awareness (SSA) analyst" in prompt
    assert "defense watch" not in prompt.lower()
    assert "pos_a_tca_km" not in prompt


def test_collision_prompt_rel_velocity_missing():
    evt = {**COLLISION_EVENT, "rel_velocity_km_s": None}
    prompt = brief_agent.build_prompt(evt, 8.1e-5, "High", DV, "A", "B")
    assert "Relative velocity: not available" in prompt


def test_pc_zero_formatting():
    assert brief_agent._pc_odds(0.0) == "effectively zero (below double-precision range)"
    # Legacy Monte Carlo scores (pc_samples given) keep their resolution wording.
    assert brief_agent._pc_odds(0.0, pc_samples=20000) == "below the Monte Carlo resolution of 1 in 20,000 (reported as 0)"
    prompt = brief_agent.build_prompt(COLLISION_EVENT, 0.0, "Low", DV, "A", "B")
    assert "effectively zero (below double-precision range)" in prompt


def test_tiny_pc_formatting_is_bounded():
    assert brief_agent._pc_odds(1.9e-138) == "less than 1 in 1,000,000,000,000"
    assert brief_agent._pc_odds(5.1e-5) == "1 in 19,608"


def test_proximity_prompt_neutral():
    prompt = brief_agent.build_prompt(PROXIMITY_EVENT, None, "Medium", None, "CARTOSAT-3", "SAT-X")
    assert "Nearby satellite: SAT-X" in prompt
    assert "foreign" not in prompt.lower()
    assert "Do not speculate about intent" in prompt
    assert "3.21 km" in prompt


def test_rejection_feedback_in_context(monkeypatch):
    monkeypatch.setattr(brief_agent, "recent_rejection_reasons",
                        lambda limit=3: ["Too alarmist", "Mentioned wrong asset"])
    prompt = brief_agent.build_prompt(COLLISION_EVENT, 8.1e-5, "High", DV, "A", "B")
    assert "In-context feedback from previous operator decisions" in prompt
    assert "no model weights are modified" in prompt
    assert "- Too alarmist" in prompt and "- Mentioned wrong asset" in prompt


def test_module_descriptions():
    assert "no model weights are modified" in brief_agent.FEEDBACK_MECHANISM_DESCRIPTION
    assert "Deterministic fact check" in brief_agent.REVIEW_DESCRIPTION
    assert "same local LLM" in brief_agent.REVIEW_DESCRIPTION
    assert "not independent validation" in brief_agent.REVIEW_DESCRIPTION
    assert "advisory" in brief_agent.REVIEW_DESCRIPTION


# ---------- maneuver ----------

def test_maneuver_collision_text():
    m = maneuver.maneuver_for_event("collision_risk", 14.2)
    assert set(m) == {"maneuver_text", "delta_v_m_s", "label"}
    assert m["delta_v_m_s"] == pytest.approx(1000 / (6 * 3600))
    assert m["label"] == "Illustrative Δv estimate"
    assert m["maneuver_text"].startswith("Illustrative delta-v estimate: ~0.05 m/s")
    assert "Not a maneuver plan" in m["maneuver_text"]
    assert "Recommend" not in m["maneuver_text"]
    assert "radial burn" not in m["maneuver_text"].lower()


def test_maneuver_proximity():
    m = maneuver.maneuver_for_event("proximity_watch", None)
    assert m["delta_v_m_s"] is None
    assert m["maneuver_text"] == "No delta-v estimate — proximity watch: continue enhanced tracking and analyst review."
    assert "Recommend" not in m["maneuver_text"]


# Telegram notification tests live in test_telegram_notify.py.


def test_reviewer_sees_exactly_the_drafter_event_data():
    """The consistency reviewer must check the brief against the same values
    (and formatting) the drafter was given -- otherwise it flags rounding and
    drafter-provided context (e.g. criticality) as 'invented'."""
    from unittest.mock import patch
    from backend import brief_agent

    event = {
        "event_class": "collision_risk", "tca_timestamp": "2026-10-02T18:59:59+00:00",
        "miss_distance_km": 0.0197712, "rel_velocity_km_s": 0.1998025,
        "pos_a_tca_km": [1.0, 2.0, 3.0], "pos_b_tca_km": [1.0, 2.0, 3.02],
    }
    prompts = []

    def fake_ollama(prompt):
        prompts.append(prompt)
        return "APPROVED consistent." if len(prompts) > 1 else "Draft brief text."

    with patch.object(brief_agent, "_call_ollama", side_effect=fake_ollama), \
         patch.object(brief_agent, "recent_rejection_reasons", return_value=[]):
        result = brief_agent.generate_and_review_brief(
            event, 6e-4, "Critical", 0.046, "CARTOSAT-3", "FENGYUN 1C",
            object_b_type="debris", criticality="Tier2", pc_samples=5000,
        )

    assert result["review_status"] == "consistent"
    drafter_block = brief_agent._drafter_data_block(prompts[0])
    assert drafter_block and "criticality: Tier2" in drafter_block
    assert drafter_block in prompts[1]
    assert "0.0197712" not in prompts[1]  # raw unrounded float not shown to the reviewer


# ---------- reviewer prompt + verdict parsing ----------

VALID_COLLISION_BRIEF = (
    "CARTOSAT-3 has a predicted close approach with FENGYUN 1C DEB at 2026-07-21T04:12:00+00:00, "
    "with a miss distance of 0.84 km and a collision probability of 8.10e-05 (risk tier High). "
    "Any maneuver decision lies with the human operator, and the ~0.05 m/s delta-v is only an "
    "illustrative estimate rather than a maneuver plan."
)


def test_valid_brief_with_reworded_closing_is_consistent(monkeypatch):
    fake = FakeOllama(["**APPROVED** - consistent"] * 2)
    monkeypatch.setattr(brief_agent, "_call_ollama", fake)
    review = brief_agent.review_brief(VALID_COLLISION_BRIEF, COLLISION_EVENT, 8.1e-5, "High", DV)
    assert review["status"] == "consistent" and review["approved"] is True
    out = _run()
    assert out["review_status"] == "consistent"


def test_genuine_contradiction_flagged_after_retry(monkeypatch):
    reason = "FLAGGED: miss distance stated as 2 km but facts say 0.84 km"
    bad = "CARTOSAT-3 will pass 2 km from FENGYUN 1C DEB."
    fake = FakeOllama([reason, reason], briefs=[bad, bad])
    monkeypatch.setattr(brief_agent, "_call_ollama", fake)
    out = _run()
    assert out["review_status"] == "flagged"
    assert out["reviewer_notes"].startswith(brief_agent.REVIEW_FLAG_PREFIX)
    assert "2 km" in out["reviewer_notes"] and reason in out["reviewer_notes"]
    assert len(fake.prompts) == 4


@pytest.mark.parametrize("response", [
    "APPROVED", "**APPROVED**", "APPROVED:", "**APPROVED** - ok", "  approved.", '"APPROVED"',
    "# APPROVED", "`APPROVED` fine", "\n\nApproved - matches the facts.",
])
def test_parse_approved_variants(response):
    assert brief_agent._parse_review_verdict(response) == "APPROVED"


@pytest.mark.parametrize("response", [
    "FLAGGED", "**FLAGGED**", "FLAGGED - x", "flagged: wrong tier",
    "FLAGGED. The brief is otherwise approved later in review.",
])
def test_parse_flagged_variants(response):
    assert brief_agent._parse_review_verdict(response) == "FLAGGED"


@pytest.mark.parametrize("response", ["", "   ", "I think it's fine", "maybe", "APPROVEDISH", None])
def test_parse_malformed_is_none(response):
    assert brief_agent._parse_review_verdict(response) is None


@pytest.mark.parametrize("response", ["", "I think it's fine", "maybe"])
def test_malformed_reviewer_response_is_never_consistent(monkeypatch, response):
    fake = FakeOllama([response] * 3)
    monkeypatch.setattr(brief_agent, "_call_ollama", fake)
    # LLM layer alone: unparseable is never 'consistent'
    review = brief_agent.review_brief("x", COLLISION_EVENT, 8.1e-5, "High", DV)
    assert review["status"] == "flagged" and review["approved"] is False
    assert review["notes"].startswith(brief_agent.REVIEW_UNPARSEABLE_PREFIX)
    # combined: clean fact check -> consistent with the unparseable note as advisory
    out = _run()
    assert out["review_status"] == "consistent"
    assert out["reviewer_notes"].startswith(brief_agent.REVIEW_ADVISORY_PREFIX + brief_agent.REVIEW_UNPARSEABLE_PREFIX)


def test_unparseable_raw_text_is_truncated(monkeypatch):
    monkeypatch.setattr(brief_agent, "_call_ollama", lambda p: "x" * 5000)
    review = brief_agent.review_brief("x", COLLISION_EVENT, 8.1e-5, "High", DV)
    assert review["status"] == "flagged"
    assert len(review["notes"]) < len(brief_agent.REVIEW_UNPARSEABLE_PREFIX) + 320


def test_reviewer_unavailable_direct_is_skipped(monkeypatch):
    monkeypatch.setattr(brief_agent, "_call_ollama", _ollama_down)
    review = brief_agent.review_brief("x", COLLISION_EVENT, 8.1e-5, "High", DV)
    assert review == {"status": "skipped", "approved": False, "notes": brief_agent.REVIEW_SKIPPED_NOTE}


def _review_prompt_for(event, risk_tier, dv):
    fake = FakeOllama(["APPROVED"])
    drafter = brief_agent.build_prompt(event, 8.1e-5, risk_tier, dv, "CARTOSAT-3", "SAT-X")
    facts = brief_agent._drafter_data_block(drafter)
    brief_agent._call_ollama, orig = fake, brief_agent._call_ollama
    try:
        brief_agent.review_brief("BRIEF", event, 8.1e-5, risk_tier, dv, source_data=facts)
    finally:
        brief_agent._call_ollama = orig
    return facts, fake.prompts[0]


def test_collision_review_prompt_contents():
    facts, prompt = _review_prompt_for(COLLISION_EVENT, "High", DV)
    assert facts in prompt
    assert brief_agent.HUMAN_DECISION_SENTENCE in prompt
    assert brief_agent.PROXIMITY_HUMAN_SENTENCE not in prompt
    assert "must NOT be flagged" in prompt
    assert "Do not flag omissions" in prompt
    assert "Do NOT recompute" in prompt
    assert "Rounded" in prompt
    assert "stating that no action is needed" in prompt  # invented "no action" still flaggable
    assert "likely necessary or advisable" in prompt
    assert "intent" in prompt
    assert "APPROVED or FLAGGED" in prompt


def test_proximity_review_prompt_contents():
    facts, prompt = _review_prompt_for(PROXIMITY_EVENT, "Medium", None)
    assert facts in prompt and "Minimum separation: 3.21 km" in prompt
    assert brief_agent.PROXIMITY_HUMAN_SENTENCE in prompt
    assert brief_agent.HUMAN_DECISION_SENTENCE not in prompt
    assert "no assessment of intent" in prompt
    assert "Do not flag omissions" in prompt
    assert "Do NOT recompute" in prompt


# ---------- deterministic fact check ----------

FC_COLLISION = {"event_class": "collision_risk", "tca_timestamp": "2026-10-02T18:59:59+00:00",
                "miss_distance_km": 0.0197712, "rel_velocity_km_s": 0.1998025}
FC_PROXIMITY = {"event_class": "proximity_watch", "tca_timestamp": "2026-10-03T02:10:00+00:00",
                "min_distance_km": 9.975, "dwell_minutes": 63.5, "geometry": "co-planar", "watch_km": 25.0}


def _fc(text, event=FC_COLLISION):
    if event["event_class"] == "collision_risk":
        return brief_agent.fact_check_brief(text, event, 7.67e-3, "Critical", 0.05,
                                            "CARTOSAT-3", "FENGYUN 1C DEB", "Tier2")
    return brief_agent.fact_check_brief(text, event, 0.0, "High", None, "CARTOSAT-3", "SAT-X", "Tier2")


FC_VALID_COLLISION = (
    "CARTOSAT-3 (Tier2 criticality) will pass about 20 metres (0.02 km) from debris FENGYUN 1C DEB at "
    "18:59 UTC on October 2nd, 2026 (2026-10-02T18:59:59+00:00), at 0.20 km/s (200 m/s). The collision "
    "probability is 7.67e-03 (about 0.77%, roughly 1 in 130; 7.7 x 10^-3), placing it in the Critical "
    "risk tier. The very small miss distance makes this a critical-risk event that warrants close "
    "attention and continued tracking. Any maneuver decision rests with the human operator; the "
    "~0.05 m/s delta-v figure is an illustrative estimate (simplified 1 km / 6 h figure), not a maneuver plan."
)
FC_VALID_PROXIMITY = (
    "On 2026-10-03T02:10:00+00:00, CARTOSAT-3 reached a minimum separation of 9.97 km (about 10 km) from "
    "nearby satellite SAT-X in a co-planar geometry. SAT-X remained within 25 km for 63.5 minutes (about "
    "1.06 hours). " + brief_agent.PROXIMITY_HUMAN_SENTENCE
)


def test_fact_check_valid_collision_rounded_forms():
    assert _fc(FC_VALID_COLLISION) == []


def test_fact_check_valid_proximity():
    assert _fc(FC_VALID_PROXIMITY, FC_PROXIMITY) == []


@pytest.mark.parametrize("text, needle", [
    ("CARTOSAT-3 will pass 2 km from FENGYUN 1C DEB.", "2 km"),
    ("The closest approach occurs within 6 hours.", "6 hours"),
    ("The relative velocity is 14.2 km/s.", "14.2 km/s"),
    ("The collision probability is 1 in 1,000.", "1 in 1,000"),
    ("The collision probability is 5%.", "5%"),
    ("Closest approach is at 2026-10-03T18:59:59+00:00.", "timestamp"),
    ("Closest approach is at 21:15 UTC.", "time '21:15'"),
    ("The asset is Tier 1 critical.", "criticality"),
])
def test_fact_check_contradicting_numbers(text, needle):
    issues = _fc(text)
    assert issues and any(needle in i for i in issues), issues


@pytest.mark.parametrize("text", [
    "This is a high-risk event.",
    "The risk tier is High.",
    "The event has a moderate to high risk of collision.",
    "This is a low risk conjunction.",
])
def test_fact_check_wrong_risk_tier(text):
    assert any("risk tier" in i for i in _fc(text))


def test_fact_check_critical_asset_wording_is_not_a_tier_claim():
    assert _fc("CARTOSAT-3 is a critical asset and part of critical infrastructure; the risk tier is Critical.") == []


@pytest.mark.parametrize("text", [
    "The miss distance is 0.02 km, so no immediate action is required.",
    "A maneuver to avoid the collision is likely necessary.",
    "An avoidance maneuver is recommended.",
    "We recommend a radial burn.",
    "The operator should perform a burn before TCA.",
    "The event does not require any action.",
    "A burn 2 hours before TCA is advisable.",
    "The satellite operator should take immediate action to assess and mitigate this risk.",
    "The delta-v estimate underscores the need for prompt consideration of a potential maneuver.",
    "There is no need for a maneuver.",
    "Given the critical risk tier, prompt action is essential.",
    "The operator should carefully consider mitigation options.",
])
def test_fact_check_invented_recommendations(text):
    assert any("recommendation" in i for i in _fc(text)), _fc(text)


@pytest.mark.parametrize("text", [
    brief_agent.HUMAN_DECISION_SENTENCE,
    "Any maneuver decision rests with the human operator, and the illustrative delta-v estimate of ~0.05 m/s "
    "is not a recommended course of action.",
    "Note: Any maneuver decision to avoid the conjunction event is solely the responsibility of the human "
    "operator, and the illustrative delta-v estimate is intended to provide context, not as a formal maneuver plan.",
    "Whether a maneuver is required is a decision for the human operator; the delta-v is an illustrative "
    "estimate, not a recommended maneuver or maneuver plan.",
])
def test_fact_check_required_closing_variants_pass(text):
    assert _fc(text) == []


@pytest.mark.parametrize("text", [
    "SAT-X appears to be stalking CARTOSAT-3.",
    "The behaviour of SAT-X is suspicious.",
    "SAT-X is a hostile satellite.",
    "SAT-X poses a threat to CARTOSAT-3.",
    "The approach appears deliberate.",
    "SAT-X may be a surveillance satellite.",
    "SAT-X could be conducting surveillance of CARTOSAT-3.",
])
def test_fact_check_proximity_intent_language(text):
    assert any("intent" in i for i in _fc(text, FC_PROXIMITY))


def test_fact_check_tracking_surveillance_wording_passes():
    assert _fc("Continued surveillance and analyst review of the pair are advised.", FC_PROXIMITY) == []


def test_fact_check_proximity_negated_intent_and_required_sentence_pass():
    assert _fc("There is no indication of hostile intent. " + brief_agent.PROXIMITY_HUMAN_SENTENCE,
               FC_PROXIMITY) == []


# ---------- combined deterministic + LLM review (pipeline) ----------

def _run_fc(fake, event=FC_COLLISION):
    if event["event_class"] == "collision_risk":
        return brief_agent.generate_and_review_brief(event, 7.67e-3, "Critical", 0.05, "CARTOSAT-3",
                                                     "FENGYUN 1C DEB", object_b_type="debris", criticality="Tier2")
    return brief_agent.generate_and_review_brief(event, 0.0, "High", None, "CARTOSAT-3", "SAT-X",
                                                 criticality="Tier2")


def test_valid_brief_with_llm_flag_noise_is_consistent_advisory(monkeypatch):
    fake = FakeOllama(["FLAGGED\n\nThe brief adds a statement about the illustrative delta-v estimate."],
                      briefs=[FC_VALID_COLLISION])
    monkeypatch.setattr(brief_agent, "_call_ollama", fake)
    out = _run_fc(fake)
    assert out["review_status"] == "consistent"
    assert out["reviewer_notes"].startswith(brief_agent.REVIEW_ADVISORY_PREFIX + "FLAGGED")
    assert len(fake.prompts) == 2  # not regenerated


def test_valid_brief_llm_approved_is_consistent(monkeypatch):
    fake = FakeOllama(["**APPROVED** - consistent"], briefs=[FC_VALID_PROXIMITY])
    monkeypatch.setattr(brief_agent, "_call_ollama", fake)
    out = _run_fc(fake, FC_PROXIMITY)
    assert out["review_status"] == "consistent" and out["reviewer_notes"] is None


def test_contradiction_with_llm_approved_is_flagged(monkeypatch):
    bad = FC_VALID_COLLISION.replace("(0.02 km)", "(2 km)")
    fake = FakeOllama(["APPROVED", "APPROVED"], briefs=[bad, bad])
    monkeypatch.setattr(brief_agent, "_call_ollama", fake)
    out = _run_fc(fake)
    assert out["review_status"] == "flagged"
    assert brief_agent.FACT_CHECK_NOTE_PREFIX in out["reviewer_notes"]
    assert "number '2 km' is not in the facts" in out["reviewer_notes"]
    assert len(fake.prompts) == 4


def test_unparseable_llm_with_clean_brief_is_consistent_advisory(monkeypatch):
    fake = FakeOllama(["I think it's fine"], briefs=[FC_VALID_COLLISION])
    monkeypatch.setattr(brief_agent, "_call_ollama", fake)
    out = _run_fc(fake)
    assert out["review_status"] == "consistent"
    assert brief_agent.REVIEW_UNPARSEABLE_PREFIX in out["reviewer_notes"]
    assert out["reviewer_notes"].startswith(brief_agent.REVIEW_ADVISORY_PREFIX)


def test_llm_down_with_clean_brief_is_skipped(monkeypatch):
    fake = FakeOllama([ConnectionError("down")], briefs=[FC_VALID_COLLISION])
    monkeypatch.setattr(brief_agent, "_call_ollama", fake)
    out = _run_fc(fake)
    assert out["review_status"] == "skipped"
    assert out["reviewer_notes"] == brief_agent.REVIEW_SKIPPED_NOTE


def test_llm_down_with_bad_brief_is_flagged(monkeypatch):
    bad = "A maneuver is likely necessary for CARTOSAT-3."
    fake = FakeOllama([ConnectionError("down"), ConnectionError("down")], briefs=[bad, bad])
    monkeypatch.setattr(brief_agent, "_call_ollama", fake)
    out = _run_fc(fake)
    assert out["review_status"] == "flagged"
    assert "LLM consistency review skipped" in out["reviewer_notes"]


def test_template_fallback_not_fact_checked(monkeypatch):
    monkeypatch.setattr(brief_agent.requests, "post", _ollama_down)
    called = []
    monkeypatch.setattr(brief_agent, "fact_check_brief", lambda *a, **k: called.append(1) or [])
    out = _run_fc(None)
    assert out["review_status"] == "not_applicable" and not called


def test_ollama_url_uses_ipv4_loopback():
    """Same local Ollama service; 127.0.0.1 avoids the ~2 s Windows IPv6
    'localhost' fallback on every brief/review call and health check."""
    from backend import config
    assert config.OLLAMA_URL == "http://127.0.0.1:11434/api/generate"
