// Pure authentication / authorisation helpers (no React, no DOM, no network)
// so node:test can cover them.
//
// The UI is gated by the PERMISSION strings the backend returns from
// /api/auth/login and /api/auth/me -- never by comparing role names, never by
// a frontend role picker, never by anything kept in localStorage. The backend
// enforces every permission again on its side; hiding a control here is a
// convenience, not the security boundary.

export const PERMISSIONS = Object.freeze({
  VIEW: 'view',
  TRANSLATE: 'translate',
  VIEW_AUDIT: 'view_audit',
  REFRESH: 'refresh',
  DEMO: 'demo',
  DECIDE: 'decide',
  REVIEW_OBJECTS: 'review_objects',
  MANAGE_ASSETS: 'manage_assets',
  MANAGE_USERS: 'manage_users',
  VIEW_ADMIN_AUDIT: 'view_admin_audit',
  CONFIGURE_SYSTEM: 'configure_system',
})
export const ALL_PERMISSIONS = Object.values(PERMISSIONS)

// Known role codes (display + the admin "change role" list). Role codes are
// data and are always shown in Latin script.
export const ROLES = ['VIEWER', 'OPERATOR', 'ASSET_MANAGER', 'ADMINISTRATOR']

const isObj = (v) => v !== null && typeof v === 'object' && !Array.isArray(v)
const str = (v) => (typeof v === 'string' && v.trim() ? v.trim() : null)

/** Normalise any permission container (array / Set / missing) to a Set of strings. */
export function permissionSet(perms) {
  if (perms instanceof Set) return new Set([...perms].filter((p) => typeof p === 'string'))
  if (Array.isArray(perms)) return new Set(perms.filter((p) => typeof p === 'string'))
  return new Set()
}

/** canDo(perms, p): true only if the backend granted permission `p`. */
export function canDo(perms, p) {
  if (typeof p !== 'string' || !p) return false
  return permissionSet(perms).has(p)
}

/** Role code as shown in the UI ("OPERATOR"); unknown/missing -> "—". */
export function roleCode(role) {
  const r = str(role)
  return r ? r.toUpperCase() : '—'
}

/** i18n key for the role description (role.OPERATOR …); unknown -> role.unknown. */
export function roleLabelKey(role) {
  const r = roleCode(role)
  return ROLES.includes(r) ? `role.${r}` : 'role.unknown'
}

/** i18n key for a permission's human name (perm.refresh …). */
export function permLabelKey(p) {
  return ALL_PERMISSIONS.includes(p) ? `perm.${p}` : 'perm.unknown'
}

/**
 * normalizeSession(resp) -> { user:{id, username, displayName, role}, permissions:[...], expiresAt } | null.
 * Anything without a username is not a session.
 */
export function normalizeSession(resp) {
  if (!isObj(resp) || !isObj(resp.user)) return null
  const username = str(resp.user.username)
  if (!username) return null
  return {
    user: {
      id: resp.user.id ?? null,
      username,
      displayName: str(resp.user.display_name),
      role: roleCode(resp.user.role),
    },
    permissions: [...permissionSet(resp.permissions)],
    expiresAt: str(resp.expires_at),
  }
}

/** Header indicator: "OPERATOR · Sejal" (name = display name or username). */
export function userIndicator(session) {
  const u = session?.user
  if (!u) return null
  const name = u.displayName || u.username
  return { role: roleCode(u.role), name, text: `${roleCode(u.role)} · ${name}`, username: u.username }
}

// ---------------------------------------------------------------------------
// Auth state machine. status: 'loading' | 'anonymous' | 'authenticated'.
// `expired` = we WERE signed in and an API call answered 401 (session ended).
// ---------------------------------------------------------------------------
export const INITIAL_AUTH = Object.freeze({ status: 'loading', session: null, expired: false, error: null })

export function authReducer(state, action) {
  const s = state || INITIAL_AUTH
  switch (action?.type) {
    case 'session': {
      const session = normalizeSession(action.payload)
      return session
        ? { status: 'authenticated', session, expired: false, error: null }
        : { status: 'anonymous', session: null, expired: false, error: null }
    }
    case 'unauthorized':
      // Any 401 (other than a failed login) drops back to the login screen.
      // Several in-flight polls may all answer 401: keep the "session ended"
      // notice once it is set.
      return { status: 'anonymous', session: null, expired: s.status === 'authenticated' || s.expired === true, error: null }
    case 'checkFailed':
      // /api/auth/me could not be reached (backend down): show the login
      // screen with a connection notice; never assume a session.
      return { status: 'anonymous', session: null, expired: false, error: action.error || 'unreachable' }
    case 'logout':
      return { status: 'anonymous', session: null, expired: false, error: null }
    default:
      return s
  }
}

/** The capabilities the UI derives from a session (everything false when signed out). */
export function capabilities(session) {
  const p = permissionSet(session?.permissions)
  return {
    view: p.has(PERMISSIONS.VIEW),
    translate: p.has(PERMISSIONS.TRANSLATE),
    viewAudit: p.has(PERMISSIONS.VIEW_AUDIT),
    refresh: p.has(PERMISSIONS.REFRESH),
    demo: p.has(PERMISSIONS.DEMO),
    decide: p.has(PERMISSIONS.DECIDE),
    reviewObjects: p.has(PERMISSIONS.REVIEW_OBJECTS),
    manageAssets: p.has(PERMISSIONS.MANAGE_ASSETS),
    manageUsers: p.has(PERMISSIONS.MANAGE_USERS),
    viewAdminAudit: p.has(PERMISSIONS.VIEW_ADMIN_AUDIT),
    configureSystem: p.has(PERMISSIONS.CONFIGURE_SYSTEM),
  }
}

/** Tooltip vars for a control the current role may not use. */
export function deniedVars(session, p) {
  return { role: roleCode(session?.user?.role), perm: p }
}

// ---------------------------------------------------------------------------
// Identity-aware audit display.
// ---------------------------------------------------------------------------
/**
 * actorOf(record) -> { role, username, text } | null. Reads actor_username /
 * actor_role, or a nested reviewed_by / actor {username, role}. null means a
 * legacy (pre-authentication) record.
 */
export function actorOf(rec) {
  if (!isObj(rec)) return null
  const nested = [rec.reviewed_by, rec.actor, rec.decided_by].find(isObj)
  const username = str(rec.actor_username) || str(rec.reviewed_by_username) || str(nested?.username)
  const role = str(rec.actor_role) || str(rec.reviewed_by_role) || str(nested?.role)
  if (!username) return null
  return { role: roleCode(role), username, text: `${roleCode(role)} · ${username}` }
}

/**
 * decisionLine(d, formatUtc) -> { decision, at, actor, legacy, text }.
 * "APPROVED · 2026-10-03 09:00:00 UTC · OPERATOR · sejal"; legacy rows carry
 * no actor and are labelled as pre-authentication records by the UI.
 */
export function decisionLine(d, formatUtc = (v) => v) {
  const rec = isObj(d) ? d : {}
  const decision = String(rec.decision || rec.status || 'unknown').toUpperCase()
  const at = rec.decided_at ? formatUtc(rec.decided_at) : '—'
  const actor = actorOf(rec)
  const parts = [decision, at]
  if (actor) parts.push(actor.text)
  return { decision, at, actor, legacy: !actor, text: parts.join(' · ') }
}

/**
 * loginErrorKey(err) -> i18n key for a failed sign-in, or null to show the
 * backend's own detail. Wrong credentials never reveal which field was wrong.
 */
export function loginErrorKey(err) {
  const status = err?.status
  if (status === 401) return 'login.invalid'
  if (status === 429) return 'login.tooMany'
  if (!status) return 'login.unreachable' // network failure (fetch TypeError)
  return null
}

/**
 * actionErrorText(err, t) -> message for a failed action: 403 shows the
 * backend's explanation (falling back to a generic "not permitted"), other
 * errors their detail.
 */
export function actionErrorText(err, t = (k) => k) {
  if (err?.status === 403) {
    const d = typeof err.message === 'string' && err.message && !/HTTP 403/.test(err.message) ? err.message : null
    return d || t('auth.forbidden')
  }
  return err?.message || String(err || '')
}
