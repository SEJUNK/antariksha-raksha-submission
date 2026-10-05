// Pure view models (no React) for operations settings shown in the console:
// - the screening-horizon admin control (GET/PUT /api/settings/screening-horizon)
// - the periodic-refresh scheduler block of the LIVE DATA panel (/api/status
//   `scheduler`)
// - refresh error text (409 refresh_in_progress)
// - which Admin panel sections a session may see.
// Every value comes from the backend; nothing here hard-codes a horizon or a
// cadence. The backend enforces every permission again.

import { PERMISSIONS, actionErrorText, canDo } from './auth.js'
import { screeningHorizon } from './mode.js'

const isObj = (v) => v !== null && typeof v === 'object' && !Array.isArray(v)
const str = (v) => (typeof v === 'string' && v.trim() ? v.trim() : null)
const posInt = (v) => (typeof v === 'number' && Number.isInteger(v) && v > 0 ? v : null)
const finite = (v) => (typeof v === 'number' && Number.isFinite(v) ? v : null)

/** "admin" / {username, role} / null -> display text or null. */
function actorText(v) {
  if (isObj(v)) {
    const u = str(v.username) || str(v.display_name)
    if (!u) return null
    return str(v.role) ? `${String(v.role).toUpperCase()} · ${u}` : u
  }
  return str(v)
}

// ---------------------------------------------------------------------------
// Admin panel sections
// ---------------------------------------------------------------------------
/**
 * adminSections(perms) -> { open, users, horizon, audit }. The panel opens for
 * any administrative permission; the user table needs manage_users, the audit
 * view_admin_audit; the horizon section is shown to anyone who can open the
 * panel (editable only with configure_system, see horizonViewModel).
 */
export function adminSections(perms) {
  const users = canDo(perms, PERMISSIONS.MANAGE_USERS)
  const audit = canDo(perms, PERMISSIONS.VIEW_ADMIN_AUDIT)
  const configure = canDo(perms, PERMISSIONS.CONFIGURE_SYSTEM)
  const open = users || audit || configure
  return { open, users, audit, horizon: open }
}

// ---------------------------------------------------------------------------
// Screening horizon
// ---------------------------------------------------------------------------
/**
 * horizonViewModel(resp, perms) -> view model, or null when the response is
 * unusable. `allowed` is the backend's list (sorted, de-duplicated); the
 * select is offered only with configure_system, everyone else reads text.
 */
export function horizonViewModel(resp, perms) {
  if (!isObj(resp)) return null
  const allowed = [...new Set((Array.isArray(resp.allowed) ? resp.allowed : []).map(posInt).filter((v) => v != null))]
    .sort((a, b) => a - b)
  const hours = posInt(resp.hours)
  if (hours == null && allowed.length === 0) return null
  const canEdit = canDo(perms, PERMISSIONS.CONFIGURE_SYSTEM) && allowed.length > 0
  return {
    hours,
    allowed,
    defaultHours: posInt(resp.default),
    stepSeconds: finite(resp.step_seconds),
    updatedAt: str(resp.updated_at),
    updatedBy: actorText(resp.updated_by),
    description: str(resp.description),
    canEdit,
    readOnly: !canEdit,
  }
}

/**
 * horizonSavePayload(selected, vm) -> { hours } for PUT, or null when the
 * session may not edit, the value is not one of the allowed values, or it
 * equals the current value (nothing to save).
 */
export function horizonSavePayload(selected, vm) {
  if (!vm?.canEdit) return null
  const hours = typeof selected === 'string' && selected.trim() ? Number(selected) : selected
  if (!posInt(hours) || !vm.allowed.includes(hours)) return null
  if (hours === vm.hours) return null
  return { hours }
}

// ---------------------------------------------------------------------------
// Scheduler (periodic public orbital-data refresh)
// ---------------------------------------------------------------------------
const ATTEMPT_TONE = {
  running: 'var(--accent)',
  success: 'var(--safe)',
  failed: 'var(--critical)',
  refused: 'var(--warn)',
  skipped: 'var(--warn)',
}
const REASON_STATUSES = new Set(['failed', 'refused', 'skipped'])
const MODES = new Set(['internal', 'external', 'off'])

/**
 * Cadence from the backend: a cron "0 *\/N * * *" (or "0 * * * *") becomes a
 * translatable {key, vars}; anything else falls back to the backend's own
 * cadence_label (upper-cased), then the raw cron string.
 */
export function cadenceView(cron, label) {
  const c = str(cron)
  if (c) {
    const every = c.match(/^0\s+\*\/(\d+)\s+\*\s+\*\s+\*$/)
    if (every) {
      const n = Number(every[1])
      if (n === 1) return { key: 'sched.everyHour', vars: {} }
      if (n > 1) return { key: 'sched.everyHours', vars: { n } }
    }
    if (/^0\s+\*\s+\*\s+\*\s+\*$/.test(c)) return { key: 'sched.everyHour', vars: {} }
  }
  const l = str(label)
  if (l) return { text: l.toUpperCase() }
  return c ? { text: c } : null
}

/**
 * schedulerViewModel(status) -> null for an older backend without
 * `scheduler`, else {enabled, mode, modeKey, cadence, nextAt, lastSuccessAt,
 * lastAttempt, running, nextRunHours}.
 */
export function schedulerViewModel(status) {
  const s = isObj(status) && isObj(status.scheduler) ? status.scheduler : null
  if (!s) return null
  const mode = MODES.has(s.mode) ? s.mode : null
  const enabled = typeof s.enabled === 'boolean' ? s.enabled : (mode != null && mode !== 'off')
  const attemptStatus = str(s.last_attempt_status)?.toLowerCase() || null
  const trigger = str(s.last_attempt_trigger)?.toLowerCase() || null
  const attemptAt = str(s.last_attempt_at)
  const lastAttempt = attemptAt || attemptStatus
    ? {
      at: attemptAt,
      status: attemptStatus,
      statusKey: attemptStatus && ATTEMPT_TONE[attemptStatus] ? `sched.status.${attemptStatus}` : null,
      statusText: attemptStatus ? attemptStatus.toUpperCase() : null,
      tone: (attemptStatus && ATTEMPT_TONE[attemptStatus]) || 'var(--text-secondary)',
      trigger: trigger === 'manual' || trigger === 'scheduled' ? trigger : null,
      triggerKey: trigger === 'manual' || trigger === 'scheduled' ? `sched.trigger.${trigger}` : null,
      reason: attemptStatus && REASON_STATUSES.has(attemptStatus) ? str(s.last_attempt_reason) : null,
    }
    : null
  const nextRunHours = screeningHorizon(status).nextRunHours
  return {
    enabled,
    mode,
    modeKey: mode ? `sched.mode.${mode}` : null,
    cadence: cadenceView(s.cadence, s.cadence_label),
    nextAt: enabled ? str(s.next_scheduled_at) : null,
    lastSuccessAt: str(s.last_successful_refresh_at) || str(status.last_refresh_at),
    lastAttempt,
    running: s.running === true || attemptStatus === 'running',
    nextRunHours,
  }
}

// ---------------------------------------------------------------------------
// Refresh errors
// ---------------------------------------------------------------------------
/** True for the backend's 409 "another refresh is running" answer. */
export function isRefreshInProgress(err) {
  return err?.status === 409 && (err?.code == null || err.code === 'refresh_in_progress')
}

/** Message for a failed manual refresh (409 in progress is explicit). */
export function refreshErrorText(err, t = (k) => k) {
  if (isRefreshInProgress(err)) return t('refresh.inProgress')
  return actionErrorText(err, t)
}
