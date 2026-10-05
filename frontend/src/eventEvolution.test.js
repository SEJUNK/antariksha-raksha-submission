import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { evolutionViewModel, formatTcaShift, TREND_KEYS } from './eventEvolution.js'
import { LANGUAGE_CODES, translate } from './i18n/core.js'

const here = dirname(fileURLToPath(import.meta.url))
const DICTS = Object.fromEntries(
  LANGUAGE_CODES.map((c) => [c, JSON.parse(readFileSync(join(here, 'i18n', `${c}.json`), 'utf8'))]),
)

const obs = (over) => ({
  observation_id: 1, screening_run_id: 10, run_started_at: '2026-10-01T06:00:00Z', run_mode: 'live',
  event_class: 'collision_risk', is_demo: false, tca_timestamp: '2026-10-03T04:12:00Z',
  miss_distance_km: 0.84, rel_velocity_km_s: 12.3, pc_score: 2.1e-6, risk_tier: 'Medium',
  priority_score: 4.2, dwell_minutes: null, is_current: false, ambiguous_match: false, ...over,
})

const THREE = {
  event_id: 7, track_id: 'T1', is_demo: false, history_available: true,
  correlation_rule: 'pair_class_nearest_tca_v1', correlation_window_seconds: 1800,
  observations: [
    obs({ observation_id: 1, screening_run_id: 10, run_started_at: '2026-10-01T06:00:00Z' }),
    obs({ observation_id: 2, screening_run_id: 11, run_started_at: '2026-10-02T06:00:00Z', miss_distance_km: 0.62, risk_tier: 'High', pc_score: 3.4e-5 }),
    obs({ observation_id: 3, screening_run_id: 12, run_started_at: '2026-10-03T06:00:00Z', miss_distance_km: 0.41, risk_tier: 'Critical', pc_score: 1.2e-4, is_current: true, tca_timestamp: '2026-10-03T04:12:42Z' }),
  ],
  trends: { miss_distance: 'decreasing', risk_tier: 'increased', tca_shift_seconds: 42, miss_stable_threshold_km: 0.05, miss_stable_threshold_rel: 0.1 },
}

test('evolution: three observations render chronologically, current last', () => {
  const vm = evolutionViewModel(THREE)
  assert.equal(vm.rows.length, 3)
  assert.equal(vm.single, false)
  assert.deepEqual(vm.rows.map((r) => r.runId), [10, 11, 12])
  assert.deepEqual(vm.rows.map((r) => r.labelKey), ['evo.obs', 'evo.obs', 'evo.current'])
  assert.deepEqual(vm.rows[1].labelVars, { n: 2 })
  assert.equal(vm.rows[2].isCurrent, true)
  assert.equal(vm.rows[2].miss, '0.410')
  assert.equal(vm.rows[0].relVel, '12.30')
  assert.equal(vm.rows[2].pc, '1.20e-4')
  assert.equal(vm.rows[2].tca, '2026-10-03 04:12:42 UTC')
  assert.equal(vm.rows[0].runTime, '2026-10-01 06:00:00 UTC')
  assert.deepEqual(vm.noteKeys, [])
})

test('evolution: out-of-order input is sorted by run start time', () => {
  const vm = evolutionViewModel({ ...THREE, observations: [THREE.observations[2], THREE.observations[0], THREE.observations[1]] })
  assert.deepEqual(vm.rows.map((r) => r.runId), [10, 11, 12])
})

test('evolution: trend labels come only from backend trend codes', () => {
  const vm = evolutionViewModel(THREE)
  assert.deepEqual(vm.trends.map((t) => t.key), ['evo.missDec', 'evo.tierInc', 'evo.tcaShift'])
  assert.deepEqual(vm.trends[2].vars, { v: '+42' })
  assert.deepEqual(vm.stableTip, { key: 'evo.stableTip', vars: { km: '0.05', rel: '10' } })
  assert.deepEqual(vm.correlation, { key: 'evo.rule.pairClassTca', vars: { s: '1800' } })
  for (const [code, { key }] of Object.entries(TREND_KEYS.miss_distance)) {
    const v = evolutionViewModel({ ...THREE, trends: { miss_distance: code } })
    assert.deepEqual(v.trends.map((t) => t.key), [key])
  }
  for (const [code, { key }] of Object.entries(TREND_KEYS.risk_tier)) {
    const v = evolutionViewModel({ ...THREE, trends: { risk_tier: code } })
    assert.deepEqual(v.trends.map((t) => t.key), [key])
  }
  // Null / unknown trend codes are hidden -- never derived in the frontend.
  const none = evolutionViewModel({ ...THREE, trends: { miss_distance: null, risk_tier: 'bogus', tca_shift_seconds: null } })
  assert.deepEqual(none.trends, [])
  assert.deepEqual(evolutionViewModel({ ...THREE, trends: undefined }).trends, [])
})

test('evolution: single observation shows the single-observation note and no trends', () => {
  const vm = evolutionViewModel({ ...THREE, observations: [obs({ is_current: true })], trends: { miss_distance: 'stable', risk_tier: 'unchanged', tca_shift_seconds: 0 } })
  assert.equal(vm.single, true)
  assert.equal(vm.rows.length, 1)
  assert.equal(vm.rows[0].labelKey, 'evo.current')
  assert.deepEqual(vm.noteKeys, ['evo.single'])
  assert.deepEqual(vm.trends, [])
  assert.equal(vm.correlation, null)
  assert.equal(translate(DICTS, 'en', 'evo.single'), 'Only one screening observation is available.')
})

test('evolution: history not yet recorded -> single note plus neutral not-recorded note', () => {
  const vm = evolutionViewModel({ event_id: 7, history_available: false, observations: [obs({ is_current: true })] })
  assert.deepEqual(vm.noteKeys, ['evo.single', 'evo.notRecorded'])
  assert.equal(vm.historyAvailable, false)
  const empty = evolutionViewModel({ event_id: 7, history_available: false, observations: [] })
  assert.equal(empty.rows.length, 0)
  assert.equal(empty.single, true)
})

test('evolution: proximity rows never show a Pc number; missing fields hidden', () => {
  const vm = evolutionViewModel({ observations: [
    obs({ event_class: 'proximity_watch', pc_score: 0.5 }),
    obs({ run_started_at: '2026-10-02T06:00:00Z', rel_velocity_km_s: null, miss_distance_km: undefined, risk_tier: null, is_current: true, ambiguous_match: true, is_demo: true }),
  ] })
  assert.equal(vm.rows[0].pc, null)
  assert.equal(vm.rows[0].pcNotComputed, true)
  assert.equal(vm.rows[1].relVel, null)
  assert.equal(vm.rows[1].miss, null)
  assert.equal(vm.rows[1].tier, null)
  assert.equal(vm.rows[1].ambiguous, true)
  assert.equal(vm.rows[1].isDemo, true)
  assert.equal(vm.ambiguous, true)
  assert.equal(evolutionViewModel(null), null)
  assert.equal(evolutionViewModel('x'), null)
  assert.deepEqual(evolutionViewModel({}).rows, [])
})

test('formatTcaShift is signed, rounded and language-independent', () => {
  assert.equal(formatTcaShift(42.4), '+42')
  assert.equal(formatTcaShift(-1234), '−1,234')
  assert.equal(formatTcaShift(0), '0')
  assert.equal(formatTcaShift(null), null)
  assert.equal(formatTcaShift('5'), null)
  for (const c of LANGUAGE_CODES) {
    const out = translate(DICTS, c, 'evo.tcaShift', { v: '+42' })
    assert.ok(out.includes('+42') && out.includes('TCA'), `${c}: ${out}`)
  }
})

test('evolution: collision-indicator change shows stored Pc old -> new, separate from the tier trend', () => {
  const vm = evolutionViewModel({ ...THREE, trends: { ...THREE.trends, pc_ratio: 3.5294 } })
  assert.deepEqual(vm.pcChange, { key: 'evo.pcChange', vars: { old: '3.40e-5', new: '1.20e-4' }, ratio: '3.53' })
  // The risk-tier trend label is still its own chip.
  assert.ok(vm.trends.some((tr) => tr.id === 'tier' && tr.key === 'evo.tierInc'))
  // No direction wording derived from Pc anywhere in the view model.
  assert.doesNotMatch(JSON.stringify(vm.pcChange), /rising|falling|increas|decreas/i)
  // Ratio missing -> values only.
  assert.equal(evolutionViewModel(THREE).pcChange.ratio, null)
  // Single observation / proximity / missing Pc -> no Pc change chip.
  assert.equal(evolutionViewModel({ ...THREE, observations: [THREE.observations[0]] }).pcChange, null)
  const prox = THREE.observations.map((o) => ({ ...o, event_class: 'proximity_watch' }))
  assert.equal(evolutionViewModel({ ...THREE, observations: prox }).pcChange, null)
  const noPc = THREE.observations.map((o, i) => (i === 2 ? { ...o, pc_score: null } : o))
  assert.equal(evolutionViewModel({ ...THREE, observations: noPc }).pcChange, null)
  for (const c of LANGUAGE_CODES) {
    const s = translate(DICTS, c, 'evo.pcChange', { old: '3.40e-5', new: '1.20e-4' })
    assert.ok(s.includes('(Pc)') && s.includes('3.40e-5') && s.includes('1.20e-4'), `${c}: ${s}`)
  }
})

test('evolution: DEMO HISTORY banner for demo tracks, with step metadata from top level or observations', () => {
  assert.equal(evolutionViewModel(THREE).demoBanner, null)
  const demoObs = THREE.observations.map((o) => ({ ...o, is_demo: true }))
  const plain = evolutionViewModel({ ...THREE, observations: demoObs })
  assert.deepEqual(plain.demoBanner, { key: 'evo.demoHistory', stepKey: null, stepVars: null })
  const top = evolutionViewModel({ ...THREE, is_demo: true, demo_history: { id: 'h1', step: 2, of: 2 } })
  assert.deepEqual(top.demoBanner, { key: 'evo.demoHistory', stepKey: 'evo.demoStep', stepVars: { step: 2, of: 2 } })
  const perObs = THREE.observations.map((o, i) => ({ ...o, is_demo: true, demo_history: { id: 'h1', step: i, of: 2 } }))
  const vm = evolutionViewModel({ ...THREE, observations: perObs })
  assert.equal(vm.demoHistory.step, 2)
  assert.equal(vm.isDemo, true)
  assert.equal(DICTS.en['evo.demoHistory'], 'DEMO HISTORY — controlled geometry')
  for (const c of LANGUAGE_CODES) {
    const s = translate(DICTS, c, 'evo.demoStep', { step: 2, of: 2 })
    assert.ok(s.includes('2'), `${c}: ${s}`)
  }
})
