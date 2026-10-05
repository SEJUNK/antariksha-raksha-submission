// API base URL, screening-horizon admin control, periodic-refresh scheduler
// block, AI header status and the related i18n strings.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { DEFAULT_API_BASE, resolveApiBase } from './api.js'
import { ALL_PERMISSIONS, PERMISSIONS, capabilities } from './auth.js'
import {
  adminSections, cadenceView, horizonSavePayload, horizonViewModel, isRefreshInProgress, refreshErrorText, schedulerViewModel,
} from './opsView.js'
import { headerAiStatus } from './eventTrust.js'
import { screeningHorizon } from './mode.js'
import { ApiError } from './api.js'

const here = dirname(fileURLToPath(import.meta.url))
const LANGS = ['en', 'hi', 'ta', 'te', 'mr', 'bn', 'gu', 'kn', 'ml', 'pa', 'or']
const DICTS = Object.fromEntries(LANGS.map((c) => [c, JSON.parse(readFileSync(join(here, 'i18n', `${c}.json`), 'utf8'))]))

const ADMIN_PERMS = [...ALL_PERMISSIONS]
const OPERATOR_PERMS = ['view', 'translate', 'view_audit', 'refresh', 'demo', 'decide']
const HORIZON = {
  hours: 48, allowed: [24, 48, 72, 96, 120], default: 72, step_seconds: 60,
  updated_at: '2026-10-03T09:00:00Z', updated_by: 'admin', description: 'Future screening window',
}

// ---------------------------------------------------------------------------
test('api base: undefined -> localhost:8000; empty string -> same-origin relative; trailing slash trimmed', () => {
  assert.equal(DEFAULT_API_BASE, 'http://localhost:8000')
  assert.equal(resolveApiBase(undefined), 'http://localhost:8000')
  assert.equal(resolveApiBase(null), 'http://localhost:8000')
  assert.equal(resolveApiBase(''), '')
  assert.equal(`${resolveApiBase('')}/api/health`, '/api/health')
  assert.equal(resolveApiBase('  '), '')
  assert.equal(resolveApiBase('https://backend.example.org/'), 'https://backend.example.org')
  assert.equal(resolveApiBase('http://10.0.0.5:8000'), 'http://10.0.0.5:8000')
})

test('api base: request and CSV export both use BASE_URL', () => {
  const src = readFileSync(join(here, 'api.js'), 'utf8')
  assert.match(src, /BASE_URL = resolveApiBase\(import\.meta\.env\?\.VITE_API_BASE_URL\)/)
  assert.match(src, /fetch\(`\$\{BASE_URL\}\$\{path\}`/)
  assert.match(src, /decisionsCsvUrl: `\$\{BASE_URL\}\/api\/decisions\/export`/)
})

// ---------------------------------------------------------------------------
test('permissions: configure_system is known, labelled and only mapped to configureSystem', () => {
  assert.equal(PERMISSIONS.CONFIGURE_SYSTEM, 'configure_system')
  assert.equal(capabilities({ permissions: ['configure_system'] }).configureSystem, true)
  assert.equal(capabilities({ permissions: OPERATOR_PERMS }).configureSystem, false)
  for (const c of LANGS) assert.ok(DICTS[c]['perm.configure_system'], c)
})

test('admin sections: panel opens for any admin permission; users/audit/horizon gated by permission strings', () => {
  assert.deepEqual(adminSections(ADMIN_PERMS), { open: true, users: true, audit: true, horizon: true })
  assert.deepEqual(adminSections(['view', 'configure_system']), { open: true, users: false, audit: false, horizon: true })
  assert.deepEqual(adminSections(['view', 'manage_users']), { open: true, users: true, audit: false, horizon: true })
  assert.deepEqual(adminSections(OPERATOR_PERMS), { open: false, users: false, audit: false, horizon: false })
  assert.deepEqual(adminSections(undefined), { open: false, users: false, audit: false, horizon: false })
})

test('horizon view model: allowed values from backend, current value, last change', () => {
  const vm = horizonViewModel(HORIZON, ADMIN_PERMS)
  assert.deepEqual(vm.allowed, [24, 48, 72, 96, 120])
  assert.equal(vm.hours, 48)
  assert.equal(vm.defaultHours, 72)
  assert.equal(vm.stepSeconds, 60)
  assert.equal(vm.updatedBy, 'admin')
  assert.equal(vm.updatedAt, '2026-10-03T09:00:00Z')
  assert.equal(vm.canEdit, true)
  assert.equal(vm.readOnly, false)
  // Unsorted / duplicate / junk values are cleaned; actor objects rendered.
  const messy = horizonViewModel({ hours: 24, allowed: [120, 24, 'x', 24, -1, 48.5], updated_by: { username: 'root', role: 'administrator' } }, ADMIN_PERMS)
  assert.deepEqual(messy.allowed, [24, 120])
  assert.equal(messy.updatedBy, 'ADMINISTRATOR · root')
  // Unusable responses.
  assert.equal(horizonViewModel(null, ADMIN_PERMS), null)
  assert.equal(horizonViewModel({}, ADMIN_PERMS), null)
})

test('horizon view model: read-only without configure_system (operator, manage_users-only admin)', () => {
  for (const perms of [OPERATOR_PERMS, ['view', 'manage_users', 'view_admin_audit'], undefined]) {
    const vm = horizonViewModel(HORIZON, perms)
    assert.equal(vm.canEdit, false)
    assert.equal(vm.readOnly, true)
    assert.equal(vm.hours, 48)
    assert.equal(horizonSavePayload(96, vm), null)
  }
})

test('horizon save payload: only allowed values that differ from the current one', () => {
  const vm = horizonViewModel(HORIZON, ADMIN_PERMS)
  assert.deepEqual(horizonSavePayload(96, vm), { hours: 96 })
  assert.deepEqual(horizonSavePayload('120', vm), { hours: 120 })
  assert.equal(horizonSavePayload(48, vm), null) // unchanged
  assert.equal(horizonSavePayload(36, vm), null) // not allowed
  assert.equal(horizonSavePayload(null, vm), null)
  assert.equal(horizonSavePayload(96, null), null)
})

// ---------------------------------------------------------------------------
const STATUS = {
  mode: 'live',
  last_refresh_at: '2026-10-03T08:00:05Z',
  screening_window_hours: 72,
  screening_step_seconds: 60,
  configured_window_hours: 72,
  scheduler: {
    mode: 'internal', enabled: true, cadence: '0 */2 * * *', cadence_label: 'every 2 hours',
    next_scheduled_at: '2026-10-03T10:00:00Z', last_successful_refresh_at: '2026-10-03T08:00:05Z',
    last_attempt_at: '2026-10-03T08:00:00Z', last_attempt_status: 'success', last_attempt_trigger: 'scheduled',
    last_attempt_reason: null, running: false,
  },
}

test('scheduler: enabled internal scheduler, cadence from cron, next and last attempt', () => {
  const vm = schedulerViewModel(STATUS)
  assert.equal(vm.enabled, true)
  assert.equal(vm.mode, 'internal')
  assert.equal(vm.modeKey, 'sched.mode.internal')
  assert.deepEqual(vm.cadence, { key: 'sched.everyHours', vars: { n: 2 } })
  assert.equal(vm.nextAt, '2026-10-03T10:00:00Z')
  assert.equal(vm.lastSuccessAt, '2026-10-03T08:00:05Z')
  assert.equal(vm.lastAttempt.status, 'success')
  assert.equal(vm.lastAttempt.statusKey, 'sched.status.success')
  assert.equal(vm.lastAttempt.tone, 'var(--safe)')
  assert.equal(vm.lastAttempt.trigger, 'scheduled')
  assert.equal(vm.lastAttempt.triggerKey, 'sched.trigger.scheduled')
  assert.equal(vm.lastAttempt.reason, null)
  assert.equal(vm.running, false)
  assert.equal(vm.nextRunHours, null)
})

test('scheduler: disabled / off -> no next time; external cron mode', () => {
  const off = schedulerViewModel({ scheduler: { mode: 'off', enabled: false, next_scheduled_at: '2026-10-03T10:00:00Z' } })
  assert.equal(off.enabled, false)
  assert.equal(off.modeKey, 'sched.mode.off')
  assert.equal(off.nextAt, null)
  assert.equal(off.lastAttempt, null)
  const ext = schedulerViewModel({ scheduler: { mode: 'external', cadence: '0 */3 * * *' } })
  assert.equal(ext.enabled, true) // derived from mode when `enabled` is missing
  assert.equal(ext.modeKey, 'sched.mode.external')
  assert.deepEqual(ext.cadence.vars, { n: 3 })
  // Older backend without scheduler -> no block at all.
  assert.equal(schedulerViewModel({ mode: 'live' }), null)
  assert.equal(schedulerViewModel(null), null)
})

test('scheduler: failed / refused / skipped attempts carry their reason and a non-green tone', () => {
  for (const [st, tone] of [['failed', 'var(--critical)'], ['refused', 'var(--warn)'], ['skipped', 'var(--warn)']]) {
    const vm = schedulerViewModel({ scheduler: { ...STATUS.scheduler, last_attempt_status: st, last_attempt_trigger: 'manual', last_attempt_reason: 'CelesTrak HTTP 503' } })
    assert.equal(vm.lastAttempt.statusKey, `sched.status.${st}`)
    assert.equal(vm.lastAttempt.tone, tone)
    assert.equal(vm.lastAttempt.triggerKey, 'sched.trigger.manual')
    assert.equal(vm.lastAttempt.reason, 'CelesTrak HTTP 503')
  }
  // Success never shows a stale reason; running marks the scheduler running.
  const ok = schedulerViewModel({ scheduler: { ...STATUS.scheduler, last_attempt_reason: 'old' } })
  assert.equal(ok.lastAttempt.reason, null)
  assert.equal(schedulerViewModel({ scheduler: { ...STATUS.scheduler, last_attempt_status: 'running' } }).running, true)
})

test('scheduler: "Next run uses {h} h" only when the configured horizon differs from the latest run', () => {
  const diff = { ...STATUS, configured_window_hours: 120 }
  assert.equal(schedulerViewModel(diff).nextRunHours, 120)
  assert.equal(screeningHorizon(diff).nextRunHours, 120)
  assert.equal(screeningHorizon(diff).hours, 72) // latest run's value still shown
  assert.equal(screeningHorizon(STATUS).nextRunHours, null)
  assert.equal(screeningHorizon({ configured_window_hours: 24 }).nextRunHours, null) // no run yet
  assert.equal(DICTS.en['sched.nextRunUses'], 'Next run uses {h} h')
})

test('cadence: cron -> translatable key; fallback to backend label, then raw cron', () => {
  assert.deepEqual(cadenceView('0 */6 * * *'), { key: 'sched.everyHours', vars: { n: 6 } })
  assert.deepEqual(cadenceView('0 * * * *'), { key: 'sched.everyHour', vars: {} })
  assert.deepEqual(cadenceView('15 3 * * 1', 'weekly on Monday'), { text: 'WEEKLY ON MONDAY' })
  assert.deepEqual(cadenceView('15 3 * * 1'), { text: '15 3 * * 1' })
  assert.equal(cadenceView(null, null), null)
  assert.equal(DICTS.en['sched.everyHours'].replace('{n}', 2), 'EVERY 2 HOURS')
})

test('refresh errors: 409 refresh_in_progress -> "A refresh is already running"', () => {
  const t = (k) => DICTS.en[k] || k
  const busy = new ApiError('Another refresh is running', { status: 409, code: 'refresh_in_progress' })
  assert.equal(isRefreshInProgress(busy), true)
  assert.equal(refreshErrorText(busy, t), 'A refresh is already running')
  assert.equal(isRefreshInProgress(new ApiError('x', { status: 409, code: 'other' })), false)
  assert.equal(refreshErrorText(new ApiError('Network down', { status: 502 }), t), 'Network down')
  assert.equal(refreshErrorText(new ApiError('/api/refresh -> HTTP 403', { status: 403 }), t), DICTS.en['auth.forbidden'])
})

// ---------------------------------------------------------------------------
test('AI header: health.ai.status preferred, then ai.available, then ollama_reachable', () => {
  assert.equal(headerAiStatus(null), null)
  assert.equal(headerAiStatus({ ai: { status: 'LOCAL_AI_AVAILABLE' }, ollama_reachable: false }).key, 'header.aiAvailable')
  assert.equal(headerAiStatus({ ai: { status: 'AI_FALLBACK_ACTIVE' }, ollama_reachable: true }).key, 'header.aiOffline')
  assert.equal(headerAiStatus({ ai: { available: true } }).key, 'header.aiAvailable')
  assert.equal(headerAiStatus({ ai: { mode: 'disabled', available: false } }).tone, 'warn')
  assert.equal(headerAiStatus({ ollama_reachable: true }).key, 'header.aiAvailable')
  assert.equal(headerAiStatus({ ollama_reachable: false }).key, 'header.aiOffline')
  assert.equal(headerAiStatus({}).key, 'header.aiOffline')
  assert.equal(DICTS.en['header.aiOffline'], 'AI FALLBACK ACTIVE — DETERMINISTIC ASSESSMENT AVAILABLE')
  assert.equal(DICTS.en['header.aiAvailable'], 'LOCAL AI AVAILABLE')
  for (const c of LANGS) {
    assert.match(DICTS[c]['header.aiOffline'], /^AI /, c)
    assert.match(DICTS[c]['header.aiAvailable'], /AI/, c)
  }
})

// ---------------------------------------------------------------------------
const NEW_KEYS = [
  'perm.configure_system', 'horizon.title', 'horizon.explain', 'horizon.applies', 'horizon.current', 'horizon.hoursValue',
  'horizon.step', 'horizon.lastChange', 'horizon.noChange', 'horizon.select', 'horizon.default', 'horizon.saved',
  'horizon.readOnly', 'horizon.unavailable', 'sched.descriptor', 'sched.auto', 'sched.enabled', 'sched.disabled',
  'sched.mode.internal', 'sched.mode.external', 'sched.mode.off', 'sched.cadence', 'sched.everyHour', 'sched.everyHours',
  'sched.lastAttempt', 'sched.status.running', 'sched.status.success', 'sched.status.failed', 'sched.status.refused',
  'sched.status.skipped', 'sched.trigger.manual', 'sched.trigger.scheduled', 'sched.reason', 'sched.next', 'sched.nextNone',
  'sched.nextRunUses', 'refresh.inProgress', 'globe.unavailableTitle', 'globe.unavailableBody',
]

test('ops i18n: new keys in all 11 languages with identical placeholders; h / s / cron / WebGL stay Latin', () => {
  const vars = (s) => (s.match(/\{\w+\}/g) || []).sort().join(',')
  for (const c of LANGS) {
    for (const k of NEW_KEYS) {
      assert.ok(typeof DICTS[c][k] === 'string' && DICTS[c][k].trim(), `${c}:${k}`)
      assert.equal(vars(DICTS[c][k]), vars(DICTS.en[k]), `${c}:${k}`)
    }
    assert.match(DICTS[c]['horizon.hoursValue'], /\{h\} h/)
    assert.match(DICTS[c]['sched.nextRunUses'], /\{h\} h/)
    assert.match(DICTS[c]['horizon.step'], /\{s\} s/)
    assert.match(DICTS[c]['sched.mode.external'], /cron/)
    assert.match(DICTS[c]['globe.unavailableBody'], /WebGL/)
    assert.match(DICTS[c]['globe.unavailableTitle'], /3D/)
  }
  assert.equal(DICTS.en['horizon.explain'], 'The prototype screens propagated trajectories for candidate close approaches over this future window.')
  assert.equal(DICTS.en['horizon.applies'], 'Applies to the next screening run; past runs keep the horizon they used.')
  assert.equal(DICTS.en['sched.descriptor'], 'Periodic public orbital-data refresh')
  assert.equal(DICTS.en['refresh.inProgress'], 'A refresh is already running')
})

test('ops i18n: neutral wording (no real-time, continuous tracking or guarantee claims)', () => {
  for (const k of [...NEW_KEYS, 'header.aiOffline', 'header.aiAvailable']) {
    assert.doesNotMatch(DICTS.en[k], /real[- ]?time|continuous|live tracking|guarantee|predicts? collisions?|certain/i, k)
  }
})

test('components: no hard-coded 72 h horizon or 2-hour cadence in React components', () => {
  const files = [join(here, 'App.jsx'), ...readdirSync(join(here, 'components')).filter((n) => n.endsWith('.jsx')).map((n) => join(here, 'components', n))]
  for (const f of files) {
    const src = readFileSync(f, 'utf8')
    assert.doesNotMatch(src, /\b72\s*(h\b|hours|\*\s*3600)/, f)
    assert.doesNotMatch(src, /\b(2|two)[\s-]*(h\b|hours?)|\*\/2\b|7200\b/i, f)
    assert.doesNotMatch(src, /\[\s*24\s*,\s*48\s*,\s*72/, f) // allowed list comes from the backend
  }
})
