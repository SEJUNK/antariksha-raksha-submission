import test from 'node:test'
import assert from 'node:assert/strict'
import {
  briefState, shouldPollBrief, generatedByLabel, briefHeading, briefSourceText, reviewLabel,
} from './briefState.js'

test('null generated_by with no brief text is pending, never "LLM unavailable"', () => {
  for (const ev of [
    { generated_by: null, brief_text: null, review_status: null },
    { brief_text: '' },
    {},
  ]) {
    assert.equal(briefState(ev), 'pending')
    const label = generatedByLabel(ev)
    assert.match(label, /BRIEF PENDING/)
    assert.doesNotMatch(label, /unavailable/i)
    assert.equal(briefHeading(ev), 'Brief pending')
    assert.equal(briefSourceText(ev, null), 'Brief pending')
    assert.equal(briefSourceText(ev, 'stale label'), 'Brief pending')
  }
})

test("generated_by 'llm' is llm", () => {
  const ev = { generated_by: 'llm', brief_text: 'x', review_status: 'consistent' }
  assert.equal(briefState(ev), 'llm')
  assert.equal(generatedByLabel(ev), 'AI-DRAFTED (local LLM)')
  assert.equal(briefHeading(ev), 'AI-generated brief')
})

test("generated_by 'fallback_template' is fallback with LLM unavailable label", () => {
  const ev = { generated_by: 'fallback_template', brief_text: 'template' }
  assert.equal(briefState(ev), 'fallback')
  assert.match(generatedByLabel(ev), /LLM unavailable/)
  assert.equal(briefHeading(ev), 'Deterministic brief')
  assert.equal(briefSourceText(ev, 'Deterministic template'), 'Deterministic template')
})

test('unknown non-empty generated_by is treated as fallback label', () => {
  assert.equal(briefState({ generated_by: 'something_else' }), 'fallback')
})

test('review labels incl. skipped', () => {
  assert.equal(reviewLabel('skipped'), 'CONSISTENCY CHECK SKIPPED')
  assert.match(reviewLabel('consistent'), /PASSED/)
  assert.match(reviewLabel('flagged'), /FLAG/)
  assert.equal(reviewLabel(null), null)
})

test('shouldPollBrief is true only for pending', () => {
  assert.equal(shouldPollBrief({ generated_by: null, brief_text: null }), true)
  assert.equal(shouldPollBrief({ generated_by: 'llm' }), false)
  assert.equal(shouldPollBrief({ generated_by: 'fallback_template' }), false)
  assert.equal(shouldPollBrief(null), false)
  assert.equal(shouldPollBrief(undefined), false)
})
