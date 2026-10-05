"""Local-LLM brief drafter + second-pass LLM consistency review + deterministic
fallback (blueprint Section 6.7, prompts in Section 8).

Honesty notes for reviewers of this prototype:
- Every LLM-drafted brief gets two checks: a deterministic fact check
  (numbers, risk tier, recommendation and intent language compared with the
  facts the drafter was given) and a second-pass review by the SAME local
  Ollama model. Neither is independent scientific validation. Only the
  deterministic check can mark a brief 'flagged'; LLM flags it does not
  confirm are kept as advisory notes.
- Operator rejection reasons are injected into the prompt as in-context
  feedback. No model weights are modified (no training, fine-tuning or RL).
- The delta-v figure is an illustrative estimate from backend.maneuver, not a
  maneuver plan. The LLM is never asked to recommend a maneuver; any maneuver
  decision rests with the human operator.
"""

import logging
import re
from datetime import datetime

import requests

from backend import config
from backend.config import OLLAMA_MODEL, OLLAMA_TIMEOUT_SECONDS, OLLAMA_URL
from backend.db import recent_rejection_reasons
from backend.maneuver import NO_MANEUVER_TEXT, collision_estimate_text

logger = logging.getLogger(__name__)

FEEDBACK_MECHANISM_DESCRIPTION = (
    "In-context feedback from previous operator decisions — no model weights are "
    "modified (no training, fine-tuning or RL)"
)
REVIEW_DESCRIPTION = (
    "Deterministic fact check (numbers, risk tier, recommendation and intent language) plus a "
    "second-pass consistency review by the same local LLM — not independent validation; LLM flags "
    "not confirmed by the fact check are recorded as advisory notes."
)

REVIEW_FLAG_PREFIX = "CONSISTENCY REVIEW FLAG (deterministic fact check, not independent validation): "
REVIEW_SKIPPED_NOTE = "Consistency review skipped: local LLM unreachable during review."

HUMAN_DECISION_SENTENCE = (
    "Any maneuver decision rests with the human operator; the delta-v figure is an "
    "illustrative estimate, not a maneuver plan."
)
PROXIMITY_HUMAN_SENTENCE = (
    "This is a geometric observation flagged for human analyst review; it makes no "
    "assessment of intent. Continued enhanced tracking and analyst review are advised."
)

# Keys that are bulky or internal and should not be dumped into the reviewer prompt.
_REVIEW_EXCLUDED_KEYS = {
    "pos_a_tca_km", "pos_b_tca_km", "positions", "positions_a", "positions_b",
    "vel_a_tca_km_s", "vel_b_tca_km_s", "covariance", "samples", "track",
}

COLLISION_PROMPT_TEMPLATE = """You are a space situational awareness (SSA) analyst. Given the following conjunction event data, write a short, plain-language conjunction brief (3-4 sentences) for a satellite operator: summarize the event and explain why it matters. Then end with exactly one sentence stating that any maneuver decision rests with the human operator and that the delta-v figure is an illustrative estimate, not a maneuver plan.

Event data:
- Protected asset: {object_a_name} (criticality: {criticality})
- Approaching object: {object_b_name} ({object_b_type})
- Time of closest approach (TCA): {tca_timestamp}
- Miss distance: {miss_distance_km} km
- Relative velocity: {rel_velocity}
- Collision probability: {pc_text}
- Risk tier: {risk_tier}
- Illustrative delta-v estimate (simplified 1 km / 6 h figure, NOT a maneuver plan): {delta_v_m_s}

Strict rules: Do not invent numbers not given above. Refer only to the data provided. Do not state or alter the TCA, miss distance, collision probability or risk tier other than exactly as given. Do not propose a specific burn, burn direction or burn timing. Write in a calm, professional, neutral tone."""

PROXIMITY_PROMPT_TEMPLATE = """You are a space situational awareness (SSA) analyst. Given the following proximity event data, write a short, factual proximity brief (3-4 sentences) describing only the measured geometry, followed by one sentence advising continued enhanced tracking and human analyst review.

Event data:
- Protected asset: {object_a_name} (criticality: {criticality})
- Nearby satellite: {object_b_name}
- Time of minimum separation: {tca_timestamp}
- Minimum separation: {min_distance_km} km
- Dwell time within {watch_km} km: {dwell_minutes} minutes
- Approach geometry: {geometry}

Strict rules: Do not invent numbers not given above. Do not speculate about intent or purpose. Do not use accusatory, hostile, threatening or suspicious language (for example, do not describe the satellite as hostile, suspicious, aggressive, stalking or a threat). Close approaches can have benign explanations such as orbital mechanics or shared orbital regimes. Describe only the measured distances, durations and geometry given above."""

REVIEWER_PROMPT_TEMPLATE = """You are performing a consistency check on a drafted satellite brief before it reaches a human operator. Check ONLY whether the brief is consistent with the authoritative facts below. Do NOT recompute or re-derive orbits, time of closest approach, miss distance, collision probability, delta-v or risk, and do NOT judge whether the underlying science is correct. Treat the facts as correct.

Authoritative facts (exactly what the drafter was given):
{key_value_dump}

Expected brief structure:
{expected_structure}

Required closing statement (the drafter was instructed to include it; it is EXPECTED and must NOT be flagged, even though it is not in the facts; its wording may vary):
{required_closing}

Drafted brief:
\"\"\"
{brief_text}
\"\"\"

Acceptable (do NOT flag):
- Rounded or reformatted presentations of the facts: fewer decimal places, a probability given only as odds or only in scientific notation, or the same time written in another format (for example "UTC" instead of "+00:00", or the date spelled out).
- Qualitative context drawn from the facts (for example that the event warrants attention or monitoring).
- Omitting optional details (for example criticality, relative velocity, odds or the delta-v figure). Omission is NOT a contradiction. Do not flag omissions.
- Harmless differences in wording, tone or formatting.
- The required closing statement in any wording, and advice to continue tracking, monitoring or human/analyst review (this is part of the required statement, not an added recommendation).

FLAG the brief if it does any of these:
  1. contradicts the facts: a wrong or altered number (for example a different distance, probability or velocity), wrong object name, wrong time, wrong criticality or risk tier, or a number that is not in the facts;
  2. adds a recommendation or maneuver instruction beyond the required closing statement, such as saying a maneuver or burn is needed, likely necessary or advisable, giving a burn direction or timing, or stating that no action is needed;
  3. speculates about the intent, purpose or hostility of any object.

Before answering, compare every number and name in the brief with the facts, and check every sentence other than the required closing statement for recommendations. Respond with exactly one word, APPROVED or FLAGGED, followed by a one-sentence explanation."""

REVIEW_EXPECTED_STRUCTURE = {
    "collision_risk": (
        "3-4 plain-language sentences summarising the conjunction from the facts (objects, time of "
        "closest approach, miss distance, relative velocity, collision probability, risk tier) and why "
        "it matters, followed by the required closing statement. Qualitative context drawn from the "
        "facts (for example that the risk tier or relative velocity warrants attention) and advising "
        "continued tracking or monitoring are acceptable."
    ),
    "proximity_watch": (
        "3-4 factual sentences describing only the measured geometry from the facts (minimum "
        "separation, its time, dwell time, approach geometry), followed by the required closing statement."
    ),
}
REVIEW_REQUIRED_CLOSING = {
    "collision_risk": (
        "One sentence stating that any maneuver decision rests with the human operator and that the "
        f'delta-v figure is an illustrative estimate, not a maneuver plan. Reference wording: "{HUMAN_DECISION_SENTENCE}"'
    ),
    "proximity_watch": (
        "One or two sentences stating that this is a geometric observation flagged for human analyst "
        "review with no assessment of intent, and advising continued enhanced tracking and analyst "
        f'review. Reference wording: "{PROXIMITY_HUMAN_SENTENCE}"'
    ),
}

REVIEW_UNPARSEABLE_PREFIX = (
    "Consistency reviewer response could not be parsed as APPROVED or FLAGGED; treated as not "
    "approved. Raw reviewer response: "
)
_REVIEW_RAW_MAX_CHARS = 300
# Leading characters a model may wrap the verdict in: whitespace, markdown
# emphasis/headings/code/quote markers and quotation marks.
_VERDICT_RE = re.compile(r"""^[\s*_#`>"'“”‘’]*(APPROVED|FLAGGED)(?![A-Za-z0-9])""", re.IGNORECASE)


class AIDisabledError(RuntimeError):
    """ANTARIKSHA_AI_MODE=disabled: no network call is ever made."""


def ai_enabled():
    """False when ANTARIKSHA_AI_MODE=disabled (read at call time)."""
    return config.AI_MODE != "disabled"


def _call_ollama(prompt):
    if not ai_enabled():
        raise AIDisabledError("local AI disabled by configuration (ANTARIKSHA_AI_MODE=disabled)")
    resp = requests.post(
        OLLAMA_URL,
        json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
        timeout=OLLAMA_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    return resp.json()["response"].strip()


def _pc_odds(pc, pc_samples=None):
    """Odds phrase for a probability. pc_samples is only passed for legacy
    Monte Carlo scores, where Pc <= 0 meant "below the sampling resolution";
    the analytic indicator is continuous, so 0 there means numerically zero."""
    if pc is None:
        return "not available"
    if pc <= 0:
        if pc_samples:
            return f"below the Monte Carlo resolution of 1 in {pc_samples:,} (reported as 0)"
        return "effectively zero (below double-precision range)"
    if pc < 1e-12:
        return "less than 1 in 1,000,000,000,000"
    return f"1 in {int(round(1 / pc)):,}"


def _format_pc(pc, pc_samples=None):
    if pc is None:
        return "not available"
    if pc <= 0:
        return _pc_odds(pc, pc_samples)
    return f"{pc:.2e} ({_pc_odds(pc, pc_samples)})"


def _format_rel_velocity(event):
    rv = event.get("rel_velocity_km_s")
    return f"{rv:.2f} km/s" if rv is not None else "not available"


def _collision_maneuver_text(delta_v_m_s):
    return collision_estimate_text(delta_v_m_s)


def _fallback_collision_brief(event, pc, risk_tier, delta_v_m_s, object_a_name, object_b_name,
                              criticality, object_b_type="foreign_sat", pc_samples=None):
    rv = event.get("rel_velocity_km_s")
    rv_clause = f" at a relative velocity of {rv:.2f} km/s" if rv is not None else ""
    maneuver_text = _collision_maneuver_text(delta_v_m_s)
    text = (
        f"CONJUNCTION ASSESSMENT: {object_a_name} (criticality {criticality}) has a predicted close "
        f"approach with {object_b_name} ({object_b_type}) at {event['tca_timestamp']}. "
        f"Predicted miss distance is {event['miss_distance_km']:.2f} km{rv_clause}, with an estimated "
        f"collision probability of {_format_pc(pc, pc_samples)} (risk tier: {risk_tier}). "
        f"Continued tracking until time of closest approach is advised. "
        f"{maneuver_text} {HUMAN_DECISION_SENTENCE}"
    )
    return {"brief_text": text, "maneuver_text": maneuver_text, "generated_by": "fallback_template"}


def _fallback_proximity_brief(event, object_a_name, object_b_name):
    text = (
        f"PROXIMITY WATCH: nearby satellite {object_b_name} remained within "
        f"{event.get('watch_km', 25.0):.0f} km of {object_a_name} for {event['dwell_minutes']:.1f} minutes, "
        f"with a measured minimum separation of {event['min_distance_km']:.2f} km and a "
        f"{event['geometry']} approach geometry. {PROXIMITY_HUMAN_SENTENCE}"
    )
    return {"brief_text": text, "maneuver_text": NO_MANEUVER_TEXT, "generated_by": "fallback_template"}


def build_prompt(event, pc, risk_tier, delta_v_m_s, object_a_name, object_b_name,
                 object_b_type="foreign_sat", criticality="Tier2", pc_samples=None):
    """Build the drafter prompt for the event class, including in-context
    operator feedback (no model weights are modified)."""
    if event["event_class"] == "collision_risk":
        prompt = COLLISION_PROMPT_TEMPLATE.format(
            object_a_name=object_a_name,
            criticality=criticality,
            object_b_name=object_b_name,
            object_b_type=object_b_type,
            tca_timestamp=event["tca_timestamp"],
            miss_distance_km=f"{event['miss_distance_km']:.2f}",
            rel_velocity=_format_rel_velocity(event),
            pc_text=_format_pc(pc, pc_samples),
            risk_tier=risk_tier,
            delta_v_m_s=f"~{delta_v_m_s:.2f} m/s" if delta_v_m_s is not None else "not available",
        )
    else:
        prompt = PROXIMITY_PROMPT_TEMPLATE.format(
            object_a_name=object_a_name,
            criticality=criticality,
            object_b_name=object_b_name,
            tca_timestamp=event.get("tca_timestamp", "not available"),
            min_distance_km=f"{event['min_distance_km']:.2f}",
            watch_km=f"{event.get('watch_km', 25.0):.0f}",
            dwell_minutes=f"{event['dwell_minutes']:.1f}",
            geometry=event["geometry"],
        )

    # In-context feedback from previous operator decisions — no model weights
    # are modified (no training, fine-tuning or RL). Up to 3 recent rejection
    # reasons are appended to the prompt.
    try:
        feedback = recent_rejection_reasons(limit=3)
    except Exception:
        feedback = []
    if feedback:
        prompt += (
            "\n\nIn-context feedback from previous operator decisions (no model weights are modified) "
            "-- reasons past briefs were REJECTED. Address these concerns in tone and framing, but "
            "never invent or alter data to do so:\n"
            + "\n".join(f"- {reason}" for reason in feedback)
        )
    return prompt


def generate_brief(event, pc, risk_tier, delta_v_m_s, object_a_name, object_b_name,
                   object_b_type="foreign_sat", criticality="Tier2", pc_samples=None):
    """Call the local LLM with the event-class prompt and return
    {brief_text, maneuver_text, generated_by}. maneuver_text always comes from
    backend.maneuver wording (never from the LLM). Falls back to a deterministic
    template if Ollama is unreachable/times out -- the pipeline must never
    hard-fail during a live demo."""
    if not ai_enabled():
        # Configured off: deterministic template immediately, no network call.
        return template_brief(event, pc, risk_tier, delta_v_m_s, object_a_name, object_b_name,
                              object_b_type, criticality, pc_samples)
    is_collision = event["event_class"] == "collision_risk"
    prompt = build_prompt(event, pc, risk_tier, delta_v_m_s, object_a_name, object_b_name,
                          object_b_type, criticality, pc_samples)
    try:
        brief_text = _call_ollama(prompt)
        maneuver_text = _collision_maneuver_text(delta_v_m_s) if is_collision else NO_MANEUVER_TEXT
        return {"brief_text": brief_text, "maneuver_text": maneuver_text, "generated_by": "llm"}
    except Exception as exc:
        logger.warning("Ollama unreachable/failed (%s); using fallback template brief.", exc)
        return template_brief(event, pc, risk_tier, delta_v_m_s, object_a_name, object_b_name,
                              object_b_type, criticality, pc_samples)


def template_brief(event, pc, risk_tier, delta_v_m_s, object_a_name, object_b_name,
                   object_b_type="foreign_sat", criticality="Tier2", pc_samples=None):
    """Deterministic template brief for the event class (no LLM call)."""
    if event["event_class"] == "collision_risk":
        return _fallback_collision_brief(event, pc, risk_tier, delta_v_m_s, object_a_name,
                                         object_b_name, criticality, object_b_type, pc_samples)
    return _fallback_proximity_brief(event, object_a_name, object_b_name)


def _review_key_values(event, pc, risk_tier=None, delta_v_m_s=None, pc_samples=None):
    lines = []
    for k, v in event.items():
        if k in _REVIEW_EXCLUDED_KEYS or k.startswith("_") or isinstance(v, (list, tuple, dict)):
            continue
        lines.append(f"- {k}: {v}")
    if event.get("event_class") == "collision_risk":
        lines.append(f"- collision_probability: {_format_pc(pc, pc_samples)}")
        if risk_tier is not None:
            lines.append(f"- risk_tier: {risk_tier}")
        if delta_v_m_s is not None:
            lines.append(f"- illustrative_delta_v_estimate_m_s (not a maneuver plan): {delta_v_m_s:.2f}")
    elif risk_tier is not None:
        lines.append(f"- risk_tier: {risk_tier}")
    return "\n".join(lines)


def _drafter_data_block(prompt):
    """The 'Event data:' section of the drafter prompt, so the reviewer checks
    the brief against exactly the values (and formatting) the drafter saw."""
    marker = "data:\n"
    start = prompt.find(marker)
    if start < 0:
        return None
    block = prompt[start + len(marker):]
    end = block.find("\n\n")
    return block if end < 0 else block[:end]


# ---------------------------------------------------------------------------
# Deterministic fact check (runs alongside the LLM consistency review)
# ---------------------------------------------------------------------------
# A small local LLM cannot reliably judge consistency on its own (it both
# misses contradictions and invents them), so every LLM-drafted brief is also
# checked by plain code against the SAME inputs the drafter prompt is built
# from (build_prompt arguments). This layer never recomputes physics: it only
# compares what the brief says with the given facts.

FACT_CHECK_NOTE_PREFIX = "Deterministic fact check: "
REVIEW_ADVISORY_PREFIX = "Advisory (LLM consistency review, not confirmed by deterministic fact check): "

_REL_TOL = 0.06          # generic relative tolerance for rounded presentations
_ODDS_REL_TOL = 0.10     # "1 in N" odds tolerance
_TIME_TOL_MIN = 1.0      # clock times may be rounded to the minute

_SUPERSCRIPT = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺−",
                             "0123456789-+-")

_NUM_RE = re.compile(
    r"(?<![\w.,/])"
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+)"
    r"(?:\s*[eE]\s*(?P<exp1>[+-]?\d+)|\s*(?:x|×|\*)\s*10\s*\^?\s*(?P<exp2>[+-]?\d+))?"
)
_UNIT_RE = re.compile(
    r"\s*(?:(?P<vel_km>km\s*/\s*s(?:ec)?\b|kilomet(?:er|re)s?\s+per\s+second)"
    r"|(?P<vel_m>m\s*/\s*s(?:ec)?\b|met(?:er|re)s?\s+per\s+second)"
    r"|(?P<dist_km>km\b|kilomet(?:er|re)s?\b)"
    r"|(?P<dist_m>m\b|met(?:er|re)s?\b)"
    r"|(?P<pct>%|percent\b|per\s+cent\b)"
    r"|(?P<hours>hours?\b|hrs?\b|h\b)"
    r"|(?P<minutes>minutes?\b|mins?\b)"
    r"|(?P<seconds>seconds?\b|secs?\b)"
    r"|(?P<ordinal>st\b|nd\b|rd\b|th\b))",
    re.IGNORECASE,
)
_ISO_RE = re.compile(
    r"\b(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?(?:\.\d+)?)?(?:Z|\s*[+-]\d{2}:?\d{2})?"
)
_TZ_RE = re.compile(r"(?:\b(?:UTC|GMT)\s*)?[+-]\s*0{1,2}:?00\b|\(?\b(?:UTC|GMT|Z)\b\)?")
_CLOCK_RE = re.compile(r"\b(\d{1,2}):(\d{2})(?::(\d{2}))?(?:\s*([AaPp])\.?\s*[Mm]\.?)?")
_ODDS_RE = re.compile(r"\b1\s+(?:in|out\s+of)\s+(\d{1,3}(?:,\d{3})+|\d+)")
_TIER_NUM_RE = re.compile(r"(?<!risk )\btier\s*-?\s*(\d)\b", re.IGNORECASE)

_TIER_SYNONYMS = {"low": "low", "medium": "medium", "moderate": "medium", "high": "high",
                  "critical": "critical", "elevated": "high", "severe": "critical"}
_TIER_WORD = r"(low|medium|moderate|high|critical|elevated|severe)"
_RISK_TIER_RES = (
    re.compile(r"\b" + _TIER_WORD + r"[\s-]+(?:collision\s+)?(?:risk|priority)\b", re.IGNORECASE),
    re.compile(r"\brisk(?:\s+(?:tier|level|rating|category|classification))?\s*"
               r"(?:is|of|:|=|at|as|rated(?:\s+as)?|classified\s+as)?\s*(?:a|an|the)?\s*[\"'(]?"
               + _TIER_WORD + r"\b", re.IGNORECASE),
    re.compile(r"\b" + _TIER_WORD + r"[\s-]+(?:risk\s+)?(?:tier|level)\b", re.IGNORECASE),
)

_NEGATION_RE = re.compile(r"\b(?:no|not|never|nor|without|any|whether|if|neither)\b|n't\b", re.IGNORECASE)
_ACTION_NOUN = (r"(?:maneuvers?|manoeuvres?|burns?|avoidance\s+(?:action|maneuver|manoeuvre)s?"
                r"|evasive\s+action|thrust(?:ing)?|actions?|mitigation(?:\s+(?:options|measures|steps|actions?))?)")
_RECOMMENDATION_RES = (
    re.compile(r"\b" + _ACTION_NOUN + r"\b[^.;:]{0,40}?\b(?:is|are|was|would\s+be|will\s+be|may\s+be|might\s+be"
               r"|could\s+be|seems|appears|be)?\s*(?:likely\s+|probably\s+|strongly\s+|highly\s+|therefore\s+)?"
               r"(?:needed|necessary|required|advisable|recommended|warranted|essential|advised|prudent)\b",
               re.IGNORECASE),
    re.compile(r"\brecommend(?:s|ed|ing|ation)?\b[^.;:]{0,30}?" + _ACTION_NOUN, re.IGNORECASE),
    re.compile(r"\b(?:should|must|needs?\s+to|ought\s+to)\s+(?:\w+\s+){0,2}?(?:perform|execute|conduct|carry\s+out"
               r"|initiate|plan|schedule|prepare|consider)\b[^.;:]{0,25}?" + _ACTION_NOUN, re.IGNORECASE),
    re.compile(r"\b(?:perform|execute|conduct|carry\s+out|initiate|schedule)(?:s|ed|ing)?\s+(?:a|an|the)?\s*"
               r"(?:\w+\s+)?" + _ACTION_NOUN, re.IGNORECASE),
    re.compile(r"\b(?:radial|anti-radial|prograde|retrograde|along-track|in-track|cross-track|posigrade)\s+"
               r"(?:burn|maneuver|manoeuvre|thrust|direction|delta-v)", re.IGNORECASE),
    re.compile(_ACTION_NOUN + r"[^.;:]{0,30}?\d+(?:\.\d+)?\s*(?:hours?|hrs?|h|minutes?|mins?)\s+"
               r"(?:before|prior\s+to|ahead\s+of)", re.IGNORECASE),
    re.compile(r"\b(?:should|must|needs?\s+to|ought\s+to|(?:is|are)\s+advised\s+to)\s+(?:\w+\s+){0,2}?take\s+"
               r"(?:\w+\s+)?(?:action|measures|steps|precautions)\b", re.IGNORECASE),
    re.compile(r"\btake\s+(?:immediate|prompt|urgent|swift|necessary|evasive|corrective|mitigating)\s+"
               r"(?:action|measures|steps|precautions)\b", re.IGNORECASE),
    re.compile(r"\b(?:need|necessity|urgency)\s+(?:for|of|to)\b[^.;:]{0,40}?" + _ACTION_NOUN, re.IGNORECASE),
)
_NO_ACTION_RES = (
    re.compile(r"\bno\s+(?:immediate\s+|further\s+|urgent\s+|additional\s+|operator\s+)?(?:action|response"
               r"|intervention|maneuver|manoeuvre|avoidance)\s+(?:is\s+|are\s+|will\s+be\s+)?(?:currently\s+)?"
               r"(?:required|needed|necessary|warranted|called\s+for)", re.IGNORECASE),
    re.compile(r"\b(?:does|do|did|will)\s*(?:not|n't)\s+(?:currently\s+)?(?:require|need|warrant)\s+(?:any\s+)?"
               r"(?:immediate\s+|further\s+)?(?:action|maneuver|manoeuvre|intervention|response)", re.IGNORECASE),
    re.compile(r"\bno\s+need\s+(?:for|to)\b[^.;:]{0,30}?(?:action|maneuver|manoeuvre|burn|intervention|act)\b",
               re.IGNORECASE),
    re.compile(r"\bnothing\s+(?:needs|has)\s+to\s+be\s+done\b|\bcan\s+(?:safely\s+)?be\s+ignored\b", re.IGNORECASE),
)
_INTENT_RE = re.compile(
    r"\b(?:hostile|hostility|suspicious(?:ly)?|aggressive(?:ly)?|aggression|stalk(?:s|ed|ing|er)?|spy(?:ing)?|spies"
    r"|espionage|surveil(?:s|led|ling)?|(?:conduct|perform|carry)(?:s|ed|ing)?\s+(?:out\s+)?(?:\w+\s+)?surveillance"
    r"|surveillance\s+(?:satellite|spacecraft|mission|activit(?:y|ies)|purposes?|operations?|platform|role)"
    r"|deliberate(?:ly)?|intentional(?:ly)?|on\s+purpose"
    r"|adversar(?:y|ial)|malicious(?:ly)?|provocative|menacing)\b", re.IGNORECASE)
_THREAT_RE = re.compile(r"\bthreat(?:s|en|ens|ening|ened)?\b", re.IGNORECASE)


def _sentences(text):
    return [s for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]


def _negated(sentence, start, window=30):
    return bool(_NEGATION_RE.search(sentence[max(0, start - window):start]))


def _parse_tca(event):
    ts = event.get("tca_timestamp")
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None


def _close(value, fact, decimals, rel_tol=_REL_TOL):
    """value (as written in the brief, with `decimals` decimal places) matches
    fact within a relative tolerance or as a correctly rounded presentation."""
    if fact is None:
        return False
    if fact == 0:
        return abs(value) < 1e-12
    if abs(value - fact) <= rel_tol * abs(fact):
        return True
    return decimals is not None and abs(round(fact, decimals) - value) < 10 ** (-(decimals + 3))


def _fact_values(event, pc, delta_v_m_s, criticality, object_a_name, object_b_name, tca):
    """Fact-derived numbers grouped by quantity, from the same inputs that
    build_prompt formats into the drafter prompt."""
    dist_m, vel_m_s, time_min, unitless, pct = [], [], [], [], []
    if event.get("event_class") == "collision_risk":
        miss = event.get("miss_distance_km")
        rv = event.get("rel_velocity_km_s")
        if miss is not None:
            dist_m.append(miss * 1000)
            unitless.append(miss)
        if rv is not None:
            vel_m_s.append(rv * 1000)
            unitless.append(rv)
        if delta_v_m_s is not None:
            vel_m_s.append(delta_v_m_s)
            unitless.append(delta_v_m_s)
        if pc is not None and pc > 0:
            unitless.append(pc)
            pct.append(pc * 100)
    else:
        for key, default in (("min_distance_km", None), ("watch_km", 25.0)):
            v = event.get(key, default)
            if v is not None:
                dist_m.append(v * 1000)
                unitless.append(v)
        dwell = event.get("dwell_minutes")
        if dwell is not None:
            time_min.append(dwell)
            unitless.append(dwell)
    if tca is not None:
        unitless += [tca.year, tca.month, tca.day, tca.hour, tca.hour % 12 or 12, tca.minute, tca.second]
    for name in (criticality, object_a_name, object_b_name):
        unitless += [float(d) for d in re.findall(r"\d+", str(name or ""))]
    return {"dist_m": dist_m, "vel_m_s": vel_m_s, "time_min": time_min, "unitless": unitless, "pct": pct}


# The drafter prompt describes the delta-v as a "simplified 1 km / 6 h figure";
# those two numbers are only accepted in a sentence about the delta-v estimate.
_DV_CONTEXT_RE = re.compile(r"delta[\s-]*v|\u0394\s*v|estimate|illustrat|simplif", re.IGNORECASE)
_DV_CONTEXT_FACTS = {"dist_m": [1000.0], "time_min": [360.0], "unitless": [1.0, 6.0]}


def _check_times(text, tca, issues):
    """Validate ISO dates/timestamps and clock times against the TCA, then
    blank them out so their digits are not re-checked as loose numbers."""
    tca_min = None if tca is None else tca.hour * 60 + tca.minute + tca.second / 60

    def iso(m):
        if tca is not None:
            ok = (int(m.group(1)), int(m.group(2)), int(m.group(3))) == (tca.year, tca.month, tca.day)
            if ok and m.group(4):
                minutes = int(m.group(4)) * 60 + int(m.group(5)) + int(m.group(6) or 0) / 60
                ok = abs(minutes - tca_min) <= _TIME_TOL_MIN
            if not ok:
                issues.append(f"timestamp '{m.group(0).strip()}' does not match the TCA {tca.isoformat()}")
        return " "

    text = _ISO_RE.sub(iso, text)
    text = _TZ_RE.sub(" ", text)

    def clock(m):
        if tca is not None:
            h, mi, s = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
            ampm = (m.group(4) or "").lower()
            if ampm == "p" and h < 12:
                h += 12
            elif ampm == "a" and h == 12:
                h = 0
            if abs(h * 60 + mi + s / 60 - tca_min) > _TIME_TOL_MIN:
                issues.append(f"time '{m.group(0).strip()}' does not match the TCA time {tca.strftime('%H:%M:%S')}")
        return " "

    return _CLOCK_RE.sub(clock, text)


def _tier_number(m, criticality, issues):
    expected = re.findall(r"\d", str(criticality or ""))
    if expected and m.group(1) != expected[0]:
        issues.append(f"criticality '{m.group(0)}' contradicts the facts ({criticality})")
    return " "


def _check_numbers(text, event, pc, delta_v_m_s, criticality, object_a_name, object_b_name, issues):
    tca = _parse_tca(event)
    facts = _fact_values(event, pc, delta_v_m_s, criticality, object_a_name, object_b_name, tca)
    text = text.translate(_SUPERSCRIPT)
    # object names may contain digits (CARTOSAT-3, COSMOS 2251): remove them first
    for name in sorted({n for n in (object_a_name, object_b_name) if n}, key=len, reverse=True):
        text = re.sub(re.escape(name), " ", text, flags=re.IGNORECASE)
    text = _check_times(text, tca, issues)

    is_collision = event.get("event_class") == "collision_risk"

    def odds(m):
        n = float(m.group(1).replace(",", ""))
        if not (is_collision and pc and pc > 0 and _close(n, 1 / pc, None, _ODDS_REL_TOL)):
            issues.append(f"odds '1 in {m.group(1)}' not consistent with the facts")
        return " "

    text = _ODDS_RE.sub(odds, text)
    text = _TIER_NUM_RE.sub(lambda m: _tier_number(m, criticality, issues), text)

    for sentence in _sentences(text):
        sentence_facts = facts
        if is_collision and _DV_CONTEXT_RE.search(sentence):
            sentence_facts = {k: v + _DV_CONTEXT_FACTS.get(k, []) for k, v in facts.items()}
        _check_sentence_numbers(sentence, sentence_facts, issues)


def _check_sentence_numbers(text, facts, issues):
    for m in _NUM_RE.finditer(text):
        raw = m.group("num")
        before = text[max(0, m.start() - 1):m.start()]
        if before and (before.isalpha() or before in "-_"):
            continue  # part of an identifier such as "SAT-7"
        digits = raw.replace(",", "")
        value = float(digits)
        decimals = len(digits.split(".")[1]) if "." in digits else 0
        exp = m.group("exp1") or m.group("exp2")
        if exp is not None:
            value *= 10 ** int(exp)
            decimals = max(0, decimals - int(exp))
        rest = text[m.end():]
        unit = _UNIT_RE.match(rest)
        if not unit and rest[:1].isalpha():
            continue  # identifier such as "1C" or "3D"
        kind = unit.lastgroup if unit else None
        if kind == "ordinal":
            kind = None
        if kind in ("dist_km", "dist_m", "vel_km", "vel_m", "hours", "minutes", "seconds"):
            factor, pool = {
                "dist_km": (1000.0, facts["dist_m"]), "dist_m": (1.0, facts["dist_m"]),
                "vel_km": (1000.0, facts["vel_m_s"]), "vel_m": (1.0, facts["vel_m_s"]),
                "hours": (60.0, facts["time_min"]), "minutes": (1.0, facts["time_min"]),
                "seconds": (1 / 60, facts["time_min"]),
            }[kind]
            ok = any(_close(value * factor, f, None) or _close(value, f / factor, decimals) for f in pool)
        elif kind == "pct":
            ok = any(_close(value, f, decimals) for f in facts["pct"])
        else:
            pool = facts["unitless"] + facts["dist_m"] + facts["vel_m_s"] + facts["time_min"] + facts["pct"]
            ok = any(_close(value, f, decimals) for f in pool)
        if not ok:
            shown = raw + (unit.group(0) if unit and kind else "")
            issues.append(f"number '{shown.strip()}' is not in the facts")


def _check_risk_tier(text, risk_tier, issues):
    expected = _TIER_SYNONYMS.get(str(risk_tier or "").strip().lower())
    if expected is None:
        return
    for rx in _RISK_TIER_RES:
        for m in rx.finditer(text):
            if _TIER_SYNONYMS[m.group(1).lower()] != expected:
                issues.append(f"risk tier stated as '{m.group(0).strip()}' but the facts say {risk_tier}")


def _check_recommendations(text, issues):
    for sentence in _sentences(text):
        for rx in _NO_ACTION_RES:
            m = rx.search(sentence)
            if m:
                issues.append(f"invented recommendation: '{m.group(0)}'")
        for rx in _RECOMMENDATION_RES:
            for m in rx.finditer(sentence):
                if not _negated(sentence, m.start()):
                    issues.append(f"invented recommendation or maneuver instruction: '{m.group(0)}'")


def _check_intent(text, event, issues):
    patterns = [_INTENT_RE]
    if event.get("event_class") != "collision_risk":
        patterns.append(_THREAT_RE)  # for debris conjunctions "threat" means collision hazard, not intent
    for sentence in _sentences(text):
        for rx in patterns:
            for m in rx.finditer(sentence):
                if not _negated(sentence, m.start(), window=20):
                    issues.append(f"speculative or intent language: '{m.group(0)}'")


def fact_check_brief(brief_text, event, pc, risk_tier, delta_v_m_s, object_a_name=None,
                     object_b_name=None, criticality=None):
    """Deterministic consistency check of a drafted brief against the facts the
    drafter was given (same inputs as build_prompt). Returns a list of issue
    strings; empty means no issue found. Pure function: no LLM, no physics."""
    text = brief_text or ""
    issues = []
    _check_numbers(text, event, pc, delta_v_m_s, criticality, object_a_name, object_b_name, issues)
    _check_risk_tier(text, risk_tier, issues)
    _check_recommendations(text, issues)
    _check_intent(text, event, issues)
    return list(dict.fromkeys(issues))


# ---------------------------------------------------------------------------
# Deterministic tone guard: unsupported sensational certainty. A screening
# indicator never justifies words like "catastrophic" or "inevitable".
# Whole-word, case-insensitive; "uncertain"/"uncertainty" never match
# (\b cannot sit inside a word). Legitimate terms such as "Critical",
# "High priority", "close approach" or "collision indicator" are not listed.
# ---------------------------------------------------------------------------
TONE_GUARD_REVIEW_STATUS = "tone_guard_fallback"
TONE_GUARD_TERMS = (
    "catastrophic", "disastrous", "devastating",
    "inevitable", "inevitably",
    "guarantee", "guarantees", "guaranteed",
    "unavoidable", "unavoidably",
    "certain", "certainly",
    "imminent", "imminently",
)
_TONE_GUARD_RES = tuple(
    re.compile(r"\b" + re.escape(term) + r"\b", re.IGNORECASE) for term in TONE_GUARD_TERMS
)


def tone_guard_issues(text):
    """Return the banned sensational terms found in `text` (lower-cased, in
    order of first appearance, de-duplicated). Empty list = clean. Pure,
    deterministic, no LLM."""
    found = []
    for m in sorted((m for rx in _TONE_GUARD_RES for m in rx.finditer(text or "")),
                    key=lambda m: m.start()):
        term = m.group(0).lower()
        if term not in found:
            found.append(term)
    return found


def tone_guard_note(term, twice=True):
    if twice:
        return (f"Tone guard: the LLM draft used unsupported sensational wording (e.g. '{term}') twice; "
                "a deterministic template brief is shown.")
    return (f"Tone guard: the regenerated LLM draft used unsupported sensational wording (e.g. '{term}'); "
            "a deterministic template brief is shown.")


def _parse_review_verdict(response):
    """Return 'APPROVED', 'FLAGGED' or None from a reviewer response.

    Only the first word decides. Tolerates leading whitespace, markdown
    emphasis (*, _, #, `), quotes and trailing punctuation (e.g. '**APPROVED**',
    'APPROVED:', '"approved."'). Anything else -> None (caller treats it as
    not approved)."""
    if not response:
        return None
    m = _VERDICT_RE.match(response)
    return m.group(1).upper() if m else None


def build_review_prompt(brief_text, event, pc, risk_tier=None, delta_v_m_s=None, pc_samples=None,
                        source_data=None):
    event_class = "collision_risk" if event.get("event_class") == "collision_risk" else "proximity_watch"
    return REVIEWER_PROMPT_TEMPLATE.format(
        key_value_dump=source_data or _review_key_values(event, pc, risk_tier, delta_v_m_s, pc_samples),
        expected_structure=REVIEW_EXPECTED_STRUCTURE[event_class],
        required_closing=REVIEW_REQUIRED_CLOSING[event_class],
        brief_text=brief_text,
    )


def review_brief(brief_text, event, pc, risk_tier=None, delta_v_m_s=None, pc_samples=None,
                 source_data=None):
    """LLM layer only: second-pass consistency review by the same local model
    as the drafter (an additional LLM check, not independent validation).

    Returns {status, approved, notes} with status in
    {'consistent', 'flagged', 'skipped'}. Never raises: if the local LLM is
    unreachable the status is 'skipped'. An unparseable reviewer response is
    'flagged', never 'consistent'. check_brief() combines this with the
    deterministic fact check."""
    prompt = build_review_prompt(brief_text, event, pc, risk_tier, delta_v_m_s, pc_samples, source_data)
    try:
        response = _call_ollama(prompt)
    except Exception as exc:
        logger.warning("Consistency review call failed (%s); marking review as skipped.", exc)
        return {"status": "skipped", "approved": False, "notes": REVIEW_SKIPPED_NOTE}
    verdict = _parse_review_verdict(response)
    if verdict == "APPROVED":
        return {"status": "consistent", "approved": True, "notes": response}
    if verdict == "FLAGGED":
        return {"status": "flagged", "approved": False, "notes": response}
    raw = (response or "").strip()
    if len(raw) > _REVIEW_RAW_MAX_CHARS:
        raw = raw[:_REVIEW_RAW_MAX_CHARS] + "..."
    return {"status": "flagged", "approved": False, "notes": f"{REVIEW_UNPARSEABLE_PREFIX}{raw!r}"}


def check_brief(brief_text, event, pc, risk_tier, delta_v_m_s, object_a_name=None, object_b_name=None,
                criticality=None, pc_samples=None, source_data=None):
    """Combine the deterministic fact check with the LLM review.

    Returns {status, notes, fact_check_issues, llm}:
      - fact check finds issues          -> 'flagged'; notes list the issues (+ LLM note for context)
      - fact check clean, LLM call fails -> 'skipped' (REVIEW_SKIPPED_NOTE)
      - fact check clean, LLM APPROVED   -> 'consistent', notes None
      - fact check clean, LLM FLAGGED or unparseable
                                         -> 'consistent', notes = advisory LLM note
    """
    issues = fact_check_brief(brief_text, event, pc, risk_tier, delta_v_m_s,
                              object_a_name, object_b_name, criticality)
    llm = review_brief(brief_text, event, pc, risk_tier, delta_v_m_s, pc_samples, source_data)
    if issues:
        if llm["status"] == "skipped":
            llm_part = "LLM consistency review skipped (local LLM unreachable)."
        else:
            llm_part = "LLM consistency review: " + " ".join((llm["notes"] or "").split())
        notes = f"{FACT_CHECK_NOTE_PREFIX}{'; '.join(issues)}. | {llm_part}"
        return {"status": "flagged", "notes": notes, "fact_check_issues": issues, "llm": llm}
    if llm["status"] == "skipped":
        return {"status": "skipped", "notes": REVIEW_SKIPPED_NOTE, "fact_check_issues": [], "llm": llm}
    if llm["status"] == "consistent":
        return {"status": "consistent", "notes": None, "fact_check_issues": [], "llm": llm}
    advisory = REVIEW_ADVISORY_PREFIX + " ".join((llm["notes"] or "").split())
    return {"status": "consistent", "notes": advisory, "fact_check_issues": [], "llm": llm}


def generate_and_review_brief(event, pc, risk_tier, delta_v_m_s, object_a_name, object_b_name,
                              object_b_type="foreign_sat", criticality="Tier2", pc_samples=None):
    """Drafter + deterministic fact check + second-pass LLM consistency review.

    Returns {brief_text, maneuver_text, generated_by, reviewer_notes,
    review_status}:
      - template fallback                     -> 'not_applicable' (no check needed)
      - fact check clean, LLM APPROVED        -> 'consistent'
      - fact check clean, LLM FLAGGED/unparseable -> 'consistent' + advisory reviewer_notes
      - fact check issues, regenerated once, issues again -> 'flagged' (notes carry the issues)
      - LLM review call failed, fact check clean -> 'skipped' (never silently passed)
      - deterministic tone guard (tone_guard_issues) hit on a draft -> regenerated once; if the
        regenerated draft still hits it -> deterministic template brief with
        generated_by 'fallback_template', review_status 'tone_guard_fallback' and a
        "Tone guard: ..." reviewer note
    """
    args = (event, pc, risk_tier, delta_v_m_s, object_a_name, object_b_name,
            object_b_type, criticality, pc_samples)

    def _finish(brief, status, notes=None):
        return {**brief, "reviewer_notes": notes, "review_status": status}

    def _check(brief):
        return check_brief(brief["brief_text"], event, pc, risk_tier, delta_v_m_s, object_a_name,
                           object_b_name, criticality, pc_samples, source_data)

    brief = generate_brief(*args)
    if brief["generated_by"] == "fallback_template":
        return _finish(brief, "not_applicable")
    source_data = _drafter_data_block(build_prompt(*args))

    # Tone guard first: a draft with sensational certainty is regenerated
    # without spending an LLM review call on it.
    tone_first = tone_guard_issues(brief["brief_text"])
    if not tone_first:
        review = _check(brief)
        if review["status"] != "flagged":
            return _finish(brief, review["status"], review["notes"])

    brief_retry = generate_brief(*args)
    if brief_retry["generated_by"] == "fallback_template":
        return _finish(brief_retry, "not_applicable")

    tone_retry = tone_guard_issues(brief_retry["brief_text"])
    if tone_retry:
        # Still sensational after one regeneration: show the deterministic
        # template (generated_by stays 'fallback_template', the only
        # non-LLM value the mission_briefs CHECK constraint allows).
        return _finish(template_brief(*args), TONE_GUARD_REVIEW_STATUS,
                       tone_guard_note(tone_retry[0], twice=bool(tone_first)))

    review_retry = _check(brief_retry)
    if review_retry["status"] != "flagged":
        return _finish(brief_retry, review_retry["status"], review_retry["notes"])
    return _finish(brief_retry, "flagged", f"{REVIEW_FLAG_PREFIX}{review_retry['notes']}")


if __name__ == "__main__":
    fake_event = {
        "event_class": "collision_risk",
        "tca_timestamp": "2026-07-21T04:12:00+00:00",
        "miss_distance_km": 0.84,
        "rel_velocity_km_s": 14.2,
    }
    result = generate_and_review_brief(
        fake_event, pc=8.1e-5, risk_tier="High", delta_v_m_s=0.05,
        object_a_name="CARTOSAT-3", object_b_name="FENGYUN 1C DEB",
        object_b_type="debris", criticality="Tier2",
    )
    print(result)
