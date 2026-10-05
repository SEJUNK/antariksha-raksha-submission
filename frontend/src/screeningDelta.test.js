import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { DELTA_GROUPS, deltaViewModel, shouldAutoShow } from './screeningDelta.js'
import { LANGUAGE_CODES } from './i18n/core.js'

const DICTS = Object.fromEntries(
  LANGUAGE_CODES.map((c) => [c, JSON.parse(readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'i18n', `${c}.json`), 'utf8'))]),
)

const item = (over) => ({
  track_id: 'T1', event_id: 5, event_class: 'collision_risk', is_demo: false,
  object_a_id: 44804, object_b_id: 99001, object_a_name: 'CARTOSAT-3', object_b_name: 'FENGYUN 1C DEB',
  previous: { tca_timestamp: '2026-10-03T04:12:00Z', miss_distance_km: 0.84, rel_velocity_km_s: 12.3, risk_tier: 'High', pc_score: 3e-5, priority_score: 30 },
  current: { tca_timestamp: '2026-10-03T04:13:00Z', miss_distance_km: 0.41, rel_velocity_km_s: 12.3, risk_tier: 'Critical', pc_score: 1.2e-4, priority_score: 120 },
  miss_distance_trend: 'decreasing', tca_shift_seconds: 60, ...over,
})

const OK = {
  available: true, reason: null, domain: 'real',
  latest_run: { id: 12, started_at: '2026-10-03T06:00:00Z', mode: 'live' },
  previous_run: { id: 11, started_at: '2026-10-02T06:00:00Z', mode: 'live' },
  counts: { new: 1, increased: 1, decreased: 0, unchanged: 2, not_present: 1 },
  items: {
    new: [item({ track_id: 'N', previous: null, miss_distance_trend: null, tca_shift_seconds: null })],
    increased: [item()],
    decreased: [],
    unchanged: [item({ track_id: 'U1' }), item({ track_id: 'U2' })],
    not_present: [item({ track_id: 'G', event_id: null, current: null, miss_distance_trend: null, tca_shift_seconds: null })],
  },
}

test('delta: counts and groups in display order', () => {
  const vm = deltaViewModel(OK)
  assert.equal(vm.state, 'ok')
  assert.equal(vm.demo, false)
  assert.equal(vm.badgeKey, 'mode.live')
  assert.deepEqual(vm.groups.map((g) => g.key), DELTA_GROUPS.map(([k]) => k))
  assert.deepEqual(vm.groups.map((g) => g.labelKey), ['delta.new', 'delta.increased', 'delta.decreased', 'delta.unchanged', 'delta.notPresent'])
  assert.deepEqual(vm.counts, { new: 1, increased: 1, decreased: 0, unchanged: 2, not_present: 1 })
  assert.equal(vm.latestRunId, 12)
  assert.equal(vm.previousRunId, 11)
  assert.equal(vm.hasItems, true)
})

test('delta: items show old -> new miss distance, TCA and tier from stored values', () => {
  const inc = deltaViewModel(OK).groups[1].items[0]
  assert.equal(inc.names, 'CARTOSAT-3 ⇔ FENGYUN 1C DEB')
  assert.deepEqual(inc.miss, { old: '0.840', new: '0.410' })
  assert.deepEqual(inc.tca, { old: '2026-10-03 04:12:00 UTC', new: '2026-10-03 04:13:00 UTC' })
  assert.deepEqual(inc.tier, { old: 'High', new: 'Critical' })
  assert.equal(inc.missTrendKey, 'evo.missDec')
  assert.equal(inc.tcaShift, '+60')
  assert.equal(inc.eventId, 5)
})

test('delta: new has no previous values; not-present has no current values and no event link', () => {
  const vm = deltaViewModel(OK)
  const n = vm.groups[0].items[0]
  assert.deepEqual(n.miss, { old: '—', new: '0.410' })
  assert.equal(n.hasPrevious, false)
  assert.equal(n.tcaShift, null)
  const g = vm.groups[4].items[0]
  assert.deepEqual(g.miss, { old: '0.840', new: '—' })
  assert.deepEqual(g.tier, { old: 'High', new: '—' })
  assert.equal(g.eventId, null)
  assert.equal(g.hasCurrent, false)
})

test('delta: no previous run / no runs', () => {
  const first = deltaViewModel({ available: false, reason: 'no_previous_run', domain: 'real', latest_run: { id: 1, started_at: '2026-10-03T06:00:00Z', mode: 'live' }, previous_run: null })
  assert.equal(first.state, 'none')
  assert.equal(first.reasonKey, 'delta.none')
  assert.equal(first.latestRunId, 1)
  assert.equal(deltaViewModel({ available: false, reason: 'no_runs', latest_run: null }).reasonKey, 'delta.noRuns')
  assert.equal(deltaViewModel(null), null)
})

test('delta: a demo-domain comparison is always flagged demo, never live', () => {
  const demo = deltaViewModel({ ...OK, domain: 'demo', latest_run: { id: 13, mode: 'live' } })
  assert.equal(demo.demo, true)
  assert.equal(demo.badgeKey, 'mode.demo')
  const demoMode = deltaViewModel({ ...OK, domain: undefined, latest_run: { id: 13, mode: 'demo' } })
  assert.equal(demoMode.demo, true)
  assert.equal(demoMode.badgeKey, 'mode.demo')
  // Unknown run mode never reads as live.
  assert.equal(deltaViewModel({ ...OK, latest_run: { id: 12 } }).badgeKey, 'prov.public')
})

test('delta: refresh-response summary (counts only) is usable', () => {
  const vm = deltaViewModel({ available: true, reason: null, previous_run_id: 11, counts: { new: 2, increased: 0, decreased: 1, unchanged: 3, not_present: 0 }, domain: 'real' })
  assert.equal(vm.state, 'ok')
  assert.equal(vm.counts.new, 2)
  assert.equal(vm.previousRunId, 11)
  assert.equal(vm.hasItems, false)
  // Missing counts fall back to list lengths.
  const v2 = deltaViewModel({ ...OK, counts: undefined })
  assert.equal(v2.counts.unchanged, 2)
})

test('shouldAutoShow: once per latest run', () => {
  assert.equal(shouldAutoShow(OK, undefined), true)
  assert.equal(shouldAutoShow(OK, 12), false)
  assert.equal(shouldAutoShow(OK, 11), true)
  assert.equal(shouldAutoShow(null, undefined), false)
  assert.equal(shouldAutoShow({ available: true, counts: {} }, 12), true)
})

test('delta: rows show stored Pc old -> new and the backend pc_ratio (display only); proximity has no Pc', () => {
  const vm = deltaViewModel({ ...OK, items: { ...OK.items, increased: [item({ pc_ratio: 4 })] } })
  const inc = vm.groups.find((g) => g.key === 'increased').items[0]
  assert.deepEqual(inc.pc, { old: '3.00e-5', new: '1.20e-4' })
  assert.equal(inc.pcRatio, '4')
  // New: no previous Pc, no ratio. Not present: no current Pc.
  const n = vm.groups.find((g) => g.key === 'new').items[0]
  assert.deepEqual(n.pc, { old: '—', new: '1.20e-4' })
  assert.equal(n.pcRatio, null)
  const g = vm.groups.find((x) => x.key === 'not_present').items[0]
  assert.deepEqual(g.pc, { old: '3.00e-5', new: '—' })
  const prox = deltaViewModel({ ...OK, items: { increased: [item({ event_class: 'proximity_watch', pc_ratio: 2 })] } })
  assert.equal(prox.groups.find((x) => x.key === 'increased').items[0].pc, null)
})

test('delta: tier buckets are labelled "Risk tier ..." and never resolved / cleared / safe', () => {
  assert.equal(DICTS.en['delta.increased'], 'Risk tier increased')
  assert.equal(DICTS.en['delta.decreased'], 'Risk tier decreased')
  assert.equal(DICTS.en['delta.notPresent'], 'Not present in the latest screening run')
  for (const [k, v] of Object.entries(DICTS.en)) {
    if (k.startsWith('delta.')) assert.doesNotMatch(v, /resolv|clear|safe|no longer a risk/i, k)
  }
})

test('delta header: explicit current/previous runs with mode, domain exclusion note, runs in between never "lost"', () => {
  const vm = deltaViewModel({
    available: true, domain: 'real',
    latest_run: { id: 57, started_at: '2026-10-03T10:47:01+00:00', mode: 'live' },
    previous_run: { id: 55, started_at: '2026-10-03T10:32:18+00:00', mode: 'live' },
    intermediate_runs: [{ id: 56, mode: 'demo', started_at: 'x', recorded: true }, { id: 58, mode: 'live', recorded: false }],
    counts: { new: 0, increased: 0, decreased: 0, unchanged: 0, not_present: 0 }, items: {},
  })
  assert.equal(vm.latestRunId, 57)
  assert.equal(vm.latestMode, 'LIVE')
  assert.equal(vm.previousRunId, 55)
  assert.equal(vm.previousMode, 'LIVE')
  assert.equal(vm.latestStartedAt, '2026-10-03 10:47:01 UTC')
  assert.equal(vm.previousStartedAt, '2026-10-03 10:32:18 UTC')
  assert.equal(vm.domainNoteKey, 'delta.excludedDemo')
  assert.deepEqual(vm.between, [{ id: 56, mode: 'DEMO', recorded: true }, { id: 58, mode: 'LIVE', recorded: false }])
  const demo = deltaViewModel({ available: true, domain: 'demo', latest_run: { id: 59, mode: 'demo' }, previous_run: { id: 58, mode: 'demo' }, items: {} })
  assert.equal(demo.domainNoteKey, 'delta.excludedLive')
  assert.deepEqual(demo.between, [])
})
