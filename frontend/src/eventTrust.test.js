import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  AI_DID_NOT_KEYS, aiBoundary, analystSummary, assessmentBasis, BASIS_LIMITATION_KEYS, COVERAGE,
  eventTimeline, headerAiStatus,
} from './eventTrust.js'
import { evolutionViewModel } from './eventEvolution.js'

const LLM = { generated_by: 'llm', brief_text: 'x', event_class: 'collision_risk' }
const PROV = {
  data: {
    source: 'CelesTrak public GP/TLE data (celestrak.org)',
    object_a: { name: 'CARTOSAT-3', norad_id: '44804', tle_age_days: 0.4, source_format: 'tle' },
    object_b: { name: 'FENGYUN 1C', norad_id: '25730', tle_age_days: 1.2, source_format: 'tle' },
    screening_run: { id: 52, mode: 'live' },
  },
  propagation: { engine: 'SGP4 (skyfield)', horizon_hours: 72, step_seconds: 60 },
  analysis: { tca_refinement: 'bounded minimisation on re-evaluated SGP4 states' },
  probability: { uncertainty_model: 'isotropic assumed sigma' },
  risk: { priority_basis: 'pc_x_criticality', criticality_effective: 'Tier2', criticality_multiplier_effective: 2, priority_score: 15174.93 },
}
const EVENT = {
  id: 7, event_class: 'collision_risk', tca_timestamp: '2026-10-03T13:00:00+00:00', miss_distance_km: 0.0205,
  rel_velocity_km_s: 0.1994, pc_score: 7.79e-3, risk_tier: 'Critical', priority_score: 15174.93, status: 'pending',
  generated_by: 'llm', brief_text: 'x', screening_run_id: 52,
}

test('AI boundary: LLM brief with passed check -> available; DID NOT always lists the physics and the decision', () => {
  const b = aiBoundary(LLM, { reviewStatus: 'consistent' })
  assert.equal(b.status.key, 'ai.status.available')
  assert.deepEqual(b.did, ['ai.did.summarise', 'ai.did.explain', 'ai.did.readable', 'ai.did.check'])
  for (const k of ['ai.didNot.orbit', 'ai.didNot.tca', 'ai.didNot.miss', 'ai.didNot.pc', 'ai.didNot.risk',
    'ai.didNot.command', 'ai.didNot.maneuver', 'ai.didNot.decision']) assert.ok(b.didNot.includes(k), k)
  assert.equal(b.statementKey, 'ai.statement')
})

test('AI boundary: template fallback is "AI unavailable", not a physics failure; AI did nothing', () => {
  const b = aiBoundary({ generated_by: 'fallback_template', brief_text: 't' })
  assert.equal(b.status.key, 'ai.status.unavailable')
  assert.deepEqual(b.did, ['ai.did.none'])
  assert.deepEqual(b.didNot, AI_DID_NOT_KEYS)
})

test('AI boundary: flagged / skipped checks keep backend evidence authoritative; pending; translation only when shown', () => {
  assert.equal(aiBoundary(LLM, { reviewStatus: 'flagged' }).status.key, 'ai.status.checkFailed')
  assert.equal(aiBoundary(LLM, { reviewStatus: 'skipped' }).status.key, 'ai.status.checkNotRun')
  assert.ok(!aiBoundary(LLM, { reviewStatus: 'skipped' }).did.includes('ai.did.check'))
  const pending = aiBoundary({ generated_by: null, brief_text: null })
  assert.equal(pending.status.key, 'ai.status.pending')
  assert.deepEqual(pending.did, ['ai.did.pending'])
  assert.ok(aiBoundary(LLM, { translationShown: true }).did.includes('ai.did.translate'))
  assert.ok(!aiBoundary(LLM).did.includes('ai.did.translate'))
})

test('header AI status follows /api/health only', () => {
  assert.equal(headerAiStatus(null), null)
  assert.equal(headerAiStatus({ ollama_reachable: true }).key, 'header.aiAvailable')
  assert.equal(headerAiStatus({ ollama_reachable: false }).key, 'header.aiOffline')
})

test('assessment basis: data, propagation and assessment facts from stored values; no confidence score', () => {
  const b = assessmentBasis(EVENT, PROV)
  const flat = Object.fromEntries(b.groups.flatMap((g) => g.rows.map((r) => [r.labelKey, r.value ?? r.valueKey])))
  assert.equal(flat['basis.source'], 'CelesTrak public GP/TLE data (celestrak.org)')
  assert.equal(flat['basis.format'], 'TLE')
  assert.equal(flat['basis.age'], 'CARTOSAT-3: 0.4 d · FENGYUN 1C: 1.2 d')
  assert.equal(flat['basis.run'], '#52 · LIVE')
  assert.equal(flat['basis.engine'], 'SGP4 (skyfield)')
  // Horizon and step are separate rows (from the event's own screening run).
  assert.equal(flat['basis.screenHorizon'], '72 h')
  assert.equal(flat['basis.propStep'], '60 s')
  assert.equal(flat['basis.miss'], '0.021 km')
  assert.equal(flat['basis.indicator'], '7.79e-3')
  assert.equal(flat['basis.uncertainty'], 'isotropic assumed sigma')
  assert.ok(!JSON.stringify(b).match(/confidence|%/i))
  assert.deepEqual(b.limitationKeys, BASIS_LIMITATION_KEYS)
  assert.deepEqual(b.coverage.notConnected, COVERAGE.notConnected)
})

test('assessment basis: proximity says Pc not computed; missing values are omitted, mixed formats shown', () => {
  const prox = { ...EVENT, event_class: 'proximity_watch', pc_score: 0, rel_velocity_km_s: null }
  const b = assessmentBasis(prox, { data: { object_a: { source_format: 'tle' }, object_b: { source_format: 'omm' } } })
  const rows = b.groups.flatMap((g) => g.rows)
  assert.deepEqual(rows.find((r) => r.labelKey === 'basis.indicator'), { labelKey: 'basis.indicator', valueKey: 'basis.notComputed' })
  assert.equal(rows.find((r) => r.labelKey === 'basis.format').value, 'OMM + TLE')
  assert.ok(!rows.some((r) => r.labelKey === 'basis.engine'))
  assert.ok(!rows.some((r) => r.labelKey === 'basis.uncertainty'))
})

test('analyst summary: what / when / how close / indicator / why / changed / limits / decision from stored values', () => {
  const evo = evolutionViewModel({
    observations: [
      { observation_id: 1, screening_run_id: 51, run_started_at: '2026-10-03T06:56:10Z', event_class: 'collision_risk', miss_distance_km: 0.519, pc_score: 3.96e-5, risk_tier: 'High' },
      { observation_id: 2, screening_run_id: 52, run_started_at: '2026-10-03T06:56:24Z', event_class: 'collision_risk', miss_distance_km: 0.0205, pc_score: 7.79e-3, risk_tier: 'Critical', is_current: true },
    ],
    trends: { miss_distance: 'decreasing', risk_tier: 'increased', tca_shift_seconds: 0, pc_ratio: 196.7 },
  })
  const rows = analystSummary({ event: EVENT, provenance: PROV, evolutionVm: evo, history: [], nameA: 'CARTOSAT-3', nameB: 'FENGYUN 1C' })
  const byId = Object.fromEntries(rows.map((r) => [r.id, r]))
  assert.deepEqual(rows.map((r) => r.id), ['what', 'when', 'close', 'indicator', 'why', 'changed', 'limits', 'decision'])
  assert.deepEqual(byId.close.parts[0], { key: 'analyst.closeValue', vars: { miss: '0.021', rel: '0.20' } })
  assert.deepEqual(byId.indicator.parts[0].vars, { pc: '7.79e-3', tier: 'Critical' })
  assert.deepEqual(byId.why.parts[0], { key: 'analyst.whyPc', vars: { tier: 'Critical', crit: 'Tier2', mult: '2', prio: '15174.930' } })
  assert.deepEqual(byId.changed.parts.map((p) => p.key), ['evo.missDec', 'evo.tierInc', 'evo.tcaShift', 'evo.pcChange'])
  assert.deepEqual(byId.limits.parts.map((p) => p.key), ['analyst.dataAge', 'analyst.limits'])
  assert.equal(byId.limits.parts[0].vars.d, '1.2')
  assert.deepEqual(byId.decision.parts, [{ key: 'analyst.decPending' }])
})

test('analyst summary: proximity has no Pc and dwell-based priority; recorded decision; demo note; single observation', () => {
  const prox = { ...EVENT, event_class: 'proximity_watch', pc_score: 0, rel_velocity_km_s: null, status: 'dismissed', is_demo: 1 }
  const rows = analystSummary({
    event: prox,
    provenance: { risk: { priority_basis: 'proximity_dwell', dwell_minutes: 4320, priority_score: 0.35 } },
    evolutionVm: evolutionViewModel({ observations: [{ observation_id: 3, is_current: true, event_class: 'proximity_watch' }] }),
    history: [{ decision: 'dismissed', decided_at: '2026-10-03T07:00:00Z' }],
    nameA: 'A', nameB: 'B',
  })
  const byId = Object.fromEntries(rows.map((r) => [r.id, r]))
  assert.equal(byId.what.parts[0].key, 'analyst.whatProximity')
  assert.equal(byId.indicator.parts[0].key, 'analyst.noPc')
  assert.deepEqual(byId.why.parts[0], { key: 'analyst.whyProx', vars: { dwell: '4320.0', prio: '0.350' } })
  assert.deepEqual(byId.changed.parts, [{ key: 'evo.single' }])
  assert.ok(byId.limits.parts.some((p) => p.key === 'analyst.demo'))
  assert.equal(byId.decision.parts[0].key, 'analyst.decRecorded')
  assert.equal(byId.decision.parts[0].vars.d, 'DISMISSED')
  assert.ok(!rows.some((r) => r.id === 'close' && r.parts[0].vars.rel))
})

test('timeline: first observation, AI explanation with the current run, decisions in time order', () => {
  const resp = {
    observations: [
      { screening_run_id: 51, run_started_at: '2026-10-03T06:56:10Z', miss_distance_km: 0.519, risk_tier: 'High', pc_score: 3.96e-5, event_class: 'collision_risk', is_demo: true },
      { screening_run_id: 52, run_started_at: '2026-10-03T06:56:24Z', miss_distance_km: 0.0205, risk_tier: 'Critical', pc_score: 7.79e-3, event_class: 'collision_risk', is_demo: true, is_current: true },
    ],
  }
  const history = [{ decision: 'approved', decided_at: '2026-10-03T06:56:30Z', is_demo: 1 }, { decision: 'dismissed', decided_at: '2026-10-03T06:56:20Z', rejection_reason: 'check' }]
  const tl = eventTimeline(resp, history, { generated_by: 'llm', brief_text: 'x' })
  assert.deepEqual(tl.map((e) => e.labelKey), ['timeline.firstObserved', 'timeline.decision', 'timeline.current', 'timeline.aiLlm', 'timeline.decision'])
  assert.equal(tl[0].vars.run, 51)
  assert.equal(tl[1].vars.d, 'DISMISSED')
  assert.equal(tl[2].vars.pc, '7.79e-3')
  assert.ok(tl[0].isDemo && tl[4].isDemo)
  assert.equal(eventTimeline(null, [], {}).length, 0)
})

test('timeline: proximity observations never show a Pc; template brief labelled as such', () => {
  const tl = eventTimeline({ observations: [{ screening_run_id: 9, run_started_at: 'a', event_class: 'proximity_watch', pc_score: 0, is_current: true }] }, [], { generated_by: 'fallback_template', brief_text: 't' })
  assert.equal(tl[0].vars.pc, '—')
  assert.equal(tl[1].labelKey, 'timeline.aiTemplate')
})
