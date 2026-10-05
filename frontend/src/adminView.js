// Pure view model (no React) for the local-account Admin panel
// (manage_users / view_admin_audit). Passwords are only ever sent, never
// displayed or kept after the request; the backend enforces every rule
// (including "at least one active administrator", answered with 409).

import { ROLES, roleCode } from './auth.js'

export const MIN_PASSWORD_LENGTH = 10

const str = (v) => (typeof v === 'string' && v.trim() ? v.trim() : null)

/** GET /api/admin/users (array or {users:[...]}) -> sorted rows, or null. */
export function usersViewModel(resp) {
  const list = Array.isArray(resp) ? resp : (Array.isArray(resp?.users) ? resp.users : null)
  if (!list) return null
  return list
    .filter((u) => u && typeof u === 'object' && str(u.username))
    .map((u) => ({
      id: u.id,
      username: str(u.username),
      displayName: str(u.display_name),
      role: roleCode(u.role),
      active: u.active !== false,
      createdAt: u.created_at || null,
      lastLoginAt: u.last_login_at || null,
    }))
    .sort((a, b) => Number(b.active) - Number(a.active) || a.username.localeCompare(b.username))
}

/** validateNewUser(form) -> { ok, errors: {field: i18nKey} }. */
export function validateNewUser(form) {
  const f = form || {}
  const errors = {}
  if (!str(f.username) || /\s/.test(String(f.username).trim())) errors.username = 'admin.err.username'
  if (!ROLES.includes(f.role)) errors.role = 'admin.err.role'
  if (typeof f.password !== 'string' || f.password.length < MIN_PASSWORD_LENGTH) errors.password = 'admin.err.password'
  return { ok: Object.keys(errors).length === 0, errors }
}

export function validatePassword(pw) {
  return typeof pw === 'string' && pw.length >= MIN_PASSWORD_LENGTH
}

/** POST /api/admin/users body. */
export function createUserPayload(form) {
  const f = form || {}
  const out = { username: String(f.username || '').trim(), role: f.role, password: f.password }
  const dn = str(f.display_name)
  if (dn) out.display_name = dn
  return out
}

/**
 * Actions offered for a user row. You cannot deactivate or demote your own
 * account from here (prevents locking yourself out); the backend still
 * guards the last active administrator with 409.
 */
export function userActions(row, selfId) {
  if (!row) return []
  const self = selfId != null && row.id === selfId
  const out = ['role', 'password']
  out.push(row.active ? 'deactivate' : 'activate')
  return self ? out.filter((a) => a !== 'deactivate' && a !== 'role') : out
}

/** Message for a failed admin action: 409 conflicts (last admin) are explicit. */
export function adminErrorText(err, t = (k) => k) {
  if (err?.status === 409) {
    const d = typeof err.message === 'string' && err.message && !/HTTP 409/.test(err.message) ? err.message : null
    const lastAdmin = /last_admin/.test(String(err.code || '')) || /last.{0,20}admin|administrator/i.test(d || '')
    const head = lastAdmin ? t('admin.lastAdmin') : t('admin.conflict')
    return d ? `${head} — ${d}` : head
  }
  if (err?.status === 403) {
    const d = typeof err.message === 'string' && err.message && !/HTTP 403/.test(err.message) ? err.message : null
    return d || t('auth.forbidden')
  }
  return err?.message || String(err || '')
}
