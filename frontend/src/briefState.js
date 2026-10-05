// Pure brief-state helpers (no React / JSX) so they can be unit-tested with
// Node's built-in test runner (see briefState.test.js).
//
// The backend inserts a conjunction event before its LLM brief has been
// generated, so for a short window GET /api/events/{id} returns
// generated_by = null and brief_text = null. That is "pending", not
// "LLM unavailable" -- only generated_by === 'fallback_template' means the
// deterministic fallback was actually used.

export const BRIEF_PENDING = 'pending'
export const BRIEF_LLM = 'llm'
export const BRIEF_FALLBACK = 'fallback'

// Poll cadence / cap for re-fetching a selected event while its brief is pending.
export const BRIEF_POLL_INTERVAL_MS = 3000
export const BRIEF_POLL_MAX_TRIES = 60 // ~3 minutes at 3 s

function hasText(v) {
  return typeof v === 'string' && v.trim().length > 0
}

/** 'pending' | 'llm' | 'fallback' for an event detail object. */
export function briefState(event) {
  const g = event?.generated_by
  if (g === 'llm') return BRIEF_LLM
  if (hasText(g)) return BRIEF_FALLBACK // 'fallback_template' or any other non-empty, non-llm value
  // generated_by null/undefined/empty: pending unless a brief text somehow exists.
  return hasText(event?.brief_text) ? BRIEF_FALLBACK : BRIEF_PENDING
}

// The backend's deterministic tone guard rejected the AI draft and stored the
// template instead (generated_by='fallback_template',
// review_status='tone_guard_fallback'). That is NOT "LLM unavailable".
export const TONE_GUARD_FALLBACK = 'tone_guard_fallback'

export function isToneGuardFallback(event) {
  return briefState(event) === BRIEF_FALLBACK && event?.review_status === TONE_GUARD_FALLBACK
}

/** 'pending' | 'llm' | 'fallback' | 'tone_guard' -- which source label applies. */
export function briefSourceKind(event) {
  return isToneGuardFallback(event) ? 'tone_guard' : briefState(event)
}

export function isBriefPending(event) {
  return briefState(event) === BRIEF_PENDING
}

/** True only while the brief is still being generated (poll should run). */
export function shouldPollBrief(event) {
  return event != null && isBriefPending(event)
}

/** Chip / export label for where the brief came from. */
export function generatedByLabel(event) {
  if (isToneGuardFallback(event)) return 'DETERMINISTIC TEMPLATE (AI draft failed the tone guard)'
  switch (briefState(event)) {
    case BRIEF_LLM: return 'AI-DRAFTED (local LLM)'
    case BRIEF_FALLBACK: return 'DETERMINISTIC TEMPLATE (LLM unavailable)'
    default: return 'BRIEF PENDING — LLM generation in progress'
  }
}

/** Section heading for the brief panel. */
export function briefHeading(event) {
  switch (briefState(event)) {
    case BRIEF_LLM: return 'AI-generated brief'
    case BRIEF_FALLBACK: return 'Deterministic brief'
    default: return 'Brief pending'
  }
}

/** Plain-text source line used in the exported incident report. */
export function briefSourceText(event, provenanceLabel) {
  if (isBriefPending(event)) return 'Brief pending'
  return provenanceLabel || generatedByLabel(event)
}

export function reviewLabel(status) {
  switch (status) {
    case 'consistent': return 'CONSISTENCY CHECK: PASSED'
    case 'flagged': return '⚠ CONSISTENCY CHECK FLAG'
    case 'skipped': return 'CONSISTENCY CHECK SKIPPED'
    default: return null
  }
}
