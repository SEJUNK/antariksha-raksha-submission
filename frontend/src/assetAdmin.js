// Pure view model (no React) for protected-asset registry management
// (manage_assets permission). The backend owns the registry and its rules;
// this only decides which actions to OFFER for a row's status and builds the
// request payloads. Every change takes effect at the next successful refresh.

import { actorOf } from './auth.js'

export const CRITICALITIES = ['Tier1', 'Tier2', 'Tier3']
export const ASSET_STATUSES = ['active', 'suspended', 'retired']

// Allowed lifecycle transitions: active <-> suspended, either -> retired
// (terminal). Retiring/suspending the last active asset is refused by the
// backend (409) and that message is shown.
const ACTIONS_BY_STATUS = {
  active: ['edit', 'suspend', 'retire'],
  suspended: ['edit', 'resume', 'retire'],
  retired: [],
}
export const ACTION_TARGET = { suspend: 'suspended', resume: 'active', retire: 'retired' }

/** Actions offered for a registry row (unknown status / no asset id -> none). */
export function assetActions(row) {
  if (!row || row.assetId == null) return []
  return [...(ACTIONS_BY_STATUS[row.status] || [])]
}

export function canTransition(from, to) {
  const acts = ACTIONS_BY_STATUS[from] || []
  return acts.some((a) => ACTION_TARGET[a] === to)
}

/** Actions that need an explicit confirmation step. */
export const CONFIRM_ACTIONS = ['retire']

/**
 * validateAssetForm(form) -> { ok, errors: {field: i18nKey} } for the
 * "Add protected asset" form.
 */
export function validateAssetForm(form) {
  const f = form || {}
  const errors = {}
  if (!String(f.name_query || '').trim()) errors.name_query = 'assets.err.name'
  if (!CRITICALITIES.includes(f.criticality)) errors.criticality = 'assets.err.criticality'
  if (String(f.note || '').length > 500) errors.note = 'assets.err.note'
  return { ok: Object.keys(errors).length === 0, errors }
}

/** POST /api/protected-assets body (only defined fields). */
export function createAssetPayload(form) {
  const f = form || {}
  const out = { name_query: String(f.name_query || '').trim(), criticality: f.criticality }
  if (f.exact_match === true) out.exact_match = true
  const note = String(f.note || '').trim()
  if (note) out.note = note
  return out
}

/** PATCH body with only the fields that changed; null when nothing changed. */
export function patchAssetPayload(row, edit) {
  const r = row || {}
  const e = edit || {}
  const out = {}
  if (e.criticality && e.criticality !== r.criticality && CRITICALITIES.includes(e.criticality)) out.criticality = e.criticality
  const note = e.note === undefined ? undefined : String(e.note || '').trim()
  if (note !== undefined && note !== (r.note || '')) out.note = note
  if (typeof e.exact_match === 'boolean' && e.exact_match !== Boolean(r.exactMatch)) out.exact_match = e.exact_match
  return Object.keys(out).length ? out : null
}

const str = (v) => (v === null || v === undefined || v === '' ? null : String(v))

/**
 * Generic governance-audit row (registry audit and admin audit share it):
 * { key, at, actor: {role, username, text}|null, action, target, detail }.
 */
export function auditRows(resp) {
  const list = Array.isArray(resp)
    ? resp
    : (Array.isArray(resp?.entries) ? resp.entries
      : Array.isArray(resp?.audit) ? resp.audit
        : Array.isArray(resp?.events) ? resp.events
          : Array.isArray(resp?.items) ? resp.items : null)
  if (!list) return null
  return list.filter((e) => e && typeof e === 'object').map((e, i) => ({
    key: e.id ?? i,
    at: str(e.at ?? e.created_at ?? e.timestamp ?? e.occurred_at),
    actor: actorOf(e),
    action: str(e.action ?? e.event ?? e.type) ? String(e.action ?? e.event ?? e.type).toUpperCase() : '—',
    target: str(e.target_username ?? e.target ?? e.name_query ?? e.asset_name ?? (e.asset_id != null ? `#${e.asset_id}` : null)
      ?? (e.target_user_id != null ? `#${e.target_user_id}` : null)),
    detail: str(e.detail ?? e.reason ?? e.note ?? (e.changes && typeof e.changes === 'object' ? JSON.stringify(e.changes) : e.changes)),
  }))
}
