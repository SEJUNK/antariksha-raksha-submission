// Screening horizon display (never a hard-coded 72) and the AI tone-guard
// fallback labels.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { screeningHorizon } from './mode.js'
import { aiBoundary, assessmentBasis, horizonNote } from './eventTrust.js'
import { briefHeading, briefSourceKind, briefSourceText, briefState, generatedByLabel, isToneGuardFallback } from './briefState.js'

const here = dirname(fileURLToPath(import.meta.url))

test('horizon: LIVE DATA row comes from the status payload (24 h / 30 s), fallback to latest_run', () => {
  const h = screeningHorizon({ screening_window_hours: 24, screening_step_seconds: 30, configured_window_hours: 24, configured_step_seconds: 30, screening_horizon_source: 'screening_run' })
  assert.equal(h.hours, 24)
  assert.equal(h.step, 30)
  assert.equal(h.source, 'screening_run')
  assert.equal(h.configuredDiffers, false)
  const fb = screeningHorizon({ latest_run: { window_hours: 48, step_seconds: 120 } })
  assert.equal(fb.hours, 48)
  assert.equal(fb.step, 120)
  // Config changed since the last run -> flagged, the run's own values still shown.
  const diff = screeningHorizon({ screening_window_hours: 24, screening_step_seconds: 30, configured_window_hours: 72, configured_step_seconds: 60 })
  assert.equal(diff.hours, 24)
  assert.equal(diff.configuredDiffers, true)
  assert.equal(diff.configuredHours, 72)
  // Nothing reported -> null (rendered N/A), never a default.
  for (const s of [null, {}, { screening_window_hours: 'x' }]) {
    const n = screeningHorizon(s)
    assert.equal(n.hours, null)
    assert.equal(n.step, null)
  }
})

test('horizon: no hard-coded 72 h in any React component', () => {
  const dir = join(here, 'components')
  for (const f of readdirSync(dir).filter((n) => n.endsWith('.jsx'))) {
    const src = readFileSync(join(dir, f), 'utf8')
    assert.doesNotMatch(src, /\b72\s*(h\b|hours|\*\s*3600)/, f)
  }
})

test('assessment basis: separate horizon and step rows from the event\'s own run; config_fallback qualified', () => {
  const ev = { event_class: 'collision_risk', screening_run_id: 61 }
  const own = assessmentBasis(ev, { data: { screening_run: { id: 61 } }, propagation: { engine: 'SGP4', horizon_hours: 24, step_seconds: 30, horizon_source: 'screening_run' } })
  const rows = Object.fromEntries(own.groups.flatMap((g) => g.rows).map((r) => [r.labelKey, r]))
  assert.equal(rows['basis.screenHorizon'].value, '24 h')
  assert.equal(rows['basis.propStep'].value, '30 s')
  assert.equal(rows['basis.screenHorizon'].noteKey, 'basis.horizonRun')
  assert.deepEqual(rows['basis.screenHorizon'].noteVars, { n: 61 })
  assert.equal(rows['basis.horizon'], undefined)

  const cfg = assessmentBasis(ev, { propagation: { horizon_hours: 72, step_seconds: 60, horizon_source: 'config_fallback' } })
  const crow = Object.fromEntries(cfg.groups.flatMap((g) => g.rows).map((r) => [r.labelKey, r]))
  assert.equal(crow['basis.screenHorizon'].value, '72 h')
  assert.equal(crow['basis.screenHorizon'].noteKey, 'basis.horizonConfig')
  assert.equal(crow['basis.propStep'].noteKey, 'basis.horizonConfig')

  // Older backend (no horizon_source): values shown, no qualifier invented.
  assert.deepEqual(horizonNote({ horizon_hours: 72 }, null, {}), {})
  // Missing step -> only the horizon row.
  const partial = assessmentBasis(ev, { propagation: { horizon_hours: 12 } })
  const keys = partial.groups.flatMap((g) => g.rows).map((r) => r.labelKey)
  assert.ok(keys.includes('basis.screenHorizon'))
  assert.ok(!keys.includes('basis.propStep'))
})

const TONE = { generated_by: 'fallback_template', review_status: 'tone_guard_fallback', brief_text: 'template', reviewer_notes: 'Draft used alarmist wording.' }

test('tone guard: template label says the AI draft failed the tone guard, not "LLM unavailable"', () => {
  assert.equal(briefState(TONE), 'fallback')
  assert.equal(isToneGuardFallback(TONE), true)
  assert.equal(briefSourceKind(TONE), 'tone_guard')
  assert.equal(generatedByLabel(TONE), 'DETERMINISTIC TEMPLATE (AI draft failed the tone guard)')
  assert.doesNotMatch(generatedByLabel(TONE), /LLM unavailable/)
  assert.equal(briefHeading(TONE), 'Deterministic brief')
  assert.equal(briefSourceText(TONE, null), 'DETERMINISTIC TEMPLATE (AI draft failed the tone guard)')
  // Plain fallback keeps the old label.
  const plain = { generated_by: 'fallback_template', brief_text: 't' }
  assert.equal(isToneGuardFallback(plain), false)
  assert.equal(briefSourceKind(plain), 'fallback')
  assert.match(generatedByLabel(plain), /LLM unavailable/)
  // review_status alone on an LLM brief is not a tone-guard fallback.
  assert.equal(isToneGuardFallback({ generated_by: 'llm', review_status: 'tone_guard_fallback' }), false)
})

test('tone guard: AI status says the draft was rejected; AI DID lists the rejected draft', () => {
  const b = aiBoundary(TONE)
  assert.equal(b.status.key, 'ai.status.toneGuard')
  assert.equal(b.status.tone, 'warn')
  assert.deepEqual(b.did, ['ai.did.toneGuard'])
  assert.ok(b.didNot.includes('ai.didNot.pc'))
  assert.equal(aiBoundary({ generated_by: 'fallback_template', brief_text: 't' }).status.key, 'ai.status.unavailable')
})
