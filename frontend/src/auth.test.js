import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  ALL_PERMISSIONS, INITIAL_AUTH, PERMISSIONS, ROLES, actionErrorText, actorOf, authReducer, canDo, capabilities,
  decisionLine, loginErrorKey, normalizeSession, permLabelKey, roleCode, roleLabelKey, userIndicator,
} from './auth.js'

// Permission lists as the backend might return them. The UI must follow the
// LIST, not the role name.
const VIEWER = { user: { id: 3, username: 'viewer1', display_name: 'View Only', role: 'VIEWER' }, permissions: ['view', 'translate', 'view_audit'], expires_at: '2026-10-03T20:00:00Z' }
const OPERATOR = { user: { id: 2, username: 'sejal', display_name: null, role: 'OPERATOR' }, permissions: ['view', 'translate', 'view_audit', 'refresh', 'demo', 'decide', 'review_objects'] }
const ASSET_MANAGER = { user: { id: 4, username: 'am', role: 'ASSET_MANAGER' }, permissions: ['view', 'view_audit', 'manage_assets'] }
const ADMIN = { user: { id: 1, username: 'admin', display_name: 'Admin', role: 'ADMINISTRATOR' }, permissions: [...ALL_PERMISSIONS] }

test('auth: permission gating follows the backend permission list per role', () => {
  const v = capabilities(normalizeSession(VIEWER))
  assert.equal(v.view, true)
  assert.equal(v.viewAudit, true)
  for (const k of ['refresh', 'demo', 'decide', 'reviewObjects', 'manageAssets', 'manageUsers', 'viewAdminAudit']) assert.equal(v[k], false, k)

  const o = capabilities(normalizeSession(OPERATOR))
  for (const k of ['refresh', 'demo', 'decide', 'reviewObjects']) assert.equal(o[k], true, k)
  for (const k of ['manageAssets', 'manageUsers', 'viewAdminAudit']) assert.equal(o[k], false, k)

  const am = capabilities(normalizeSession(ASSET_MANAGER))
  assert.equal(am.manageAssets, true)
  assert.equal(am.decide, false)
  assert.equal(am.manageUsers, false)

  const a = capabilities(normalizeSession(ADMIN))
  for (const [k, val] of Object.entries(a)) assert.equal(val, true, k)
})

test('auth: role name alone never grants anything; unknown role / missing perms -> no actions', () => {
  // ADMINISTRATOR role but an empty permission list -> nothing allowed.
  const noPerms = normalizeSession({ user: { username: 'x', role: 'ADMINISTRATOR' } })
  assert.deepEqual(noPerms.permissions, [])
  assert.ok(Object.values(capabilities(noPerms)).every((v) => v === false))
  // Unknown role with a real permission: the permission still applies.
  const odd = normalizeSession({ user: { username: 'y', role: 'auditor' }, permissions: ['view', 42, null] })
  assert.equal(odd.user.role, 'AUDITOR')
  assert.deepEqual(odd.permissions, ['view'])
  assert.equal(roleLabelKey('auditor'), 'role.unknown')
  assert.equal(roleLabelKey(undefined), 'role.unknown')
  assert.equal(roleCode(null), '—')
  // Signed out.
  assert.ok(Object.values(capabilities(null)).every((v) => v === false))
  assert.equal(canDo(undefined, 'view'), false)
  assert.equal(canDo(['view'], ''), false)
  assert.equal(canDo(new Set(['decide']), PERMISSIONS.DECIDE), true)
  assert.equal(canDo(['decide'], 'refresh'), false)
})

test('auth: role / permission label keys and the header indicator', () => {
  for (const r of ROLES) assert.equal(roleLabelKey(r), `role.${r}`)
  assert.equal(roleLabelKey('operator'), 'role.OPERATOR')
  for (const p of ALL_PERMISSIONS) assert.equal(permLabelKey(p), `perm.${p}`)
  assert.equal(permLabelKey('fly'), 'perm.unknown')
  assert.deepEqual(userIndicator(normalizeSession(OPERATOR)), { role: 'OPERATOR', name: 'sejal', text: 'OPERATOR · sejal', username: 'sejal' })
  assert.equal(userIndicator(normalizeSession(VIEWER)).text, 'VIEWER · View Only')
  assert.equal(userIndicator(null), null)
})

test('auth: malformed /me responses are not sessions', () => {
  for (const r of [null, {}, { user: null }, { user: { username: '  ' } }, 'x', []]) assert.equal(normalizeSession(r), null)
})

test('auth state: session -> authenticated; 401 -> logged out (expired only if we were signed in)', () => {
  let s = authReducer(INITIAL_AUTH, { type: 'session', payload: OPERATOR })
  assert.equal(s.status, 'authenticated')
  assert.equal(s.session.user.username, 'sejal')
  s = authReducer(s, { type: 'unauthorized' })
  assert.deepEqual(s, { status: 'anonymous', session: null, expired: true, error: null })
  // Further 401s from other in-flight polls keep the "session ended" notice.
  assert.equal(authReducer(s, { type: 'unauthorized' }).expired, true)
  // 401 on the initial /me check: plain login screen, not "session ended".
  assert.equal(authReducer(INITIAL_AUTH, { type: 'unauthorized' }).expired, false)
  // Bad payload never yields a session.
  assert.equal(authReducer(INITIAL_AUTH, { type: 'session', payload: { user: {} } }).status, 'anonymous')
  // Backend unreachable on load: login screen with a notice.
  assert.equal(authReducer(INITIAL_AUTH, { type: 'checkFailed' }).error, 'unreachable')
  // Logout clears everything.
  const out = authReducer(authReducer(INITIAL_AUTH, { type: 'session', payload: ADMIN }), { type: 'logout' })
  assert.deepEqual(out, { status: 'anonymous', session: null, expired: false, error: null })
  assert.equal(authReducer(out, { type: 'nonsense' }), out)
})

test('auth: login / action error messages (401 generic, 403 shows backend detail)', () => {
  assert.equal(loginErrorKey({ status: 401 }), 'login.invalid')
  assert.equal(loginErrorKey({ status: 429 }), 'login.tooMany')
  assert.equal(loginErrorKey(new TypeError('Failed to fetch')), 'login.unreachable')
  assert.equal(loginErrorKey({ status: 500, message: 'boom' }), null)
  const t = (k) => `[${k}]`
  assert.equal(actionErrorText({ status: 403, message: 'Role VIEWER cannot record decisions.' }, t), 'Role VIEWER cannot record decisions.')
  assert.equal(actionErrorText({ status: 403, message: '/api/refresh -> HTTP 403' }, t), '[auth.forbidden]')
  assert.equal(actionErrorText({ status: 409, message: 'conflict detail' }, t), 'conflict detail')
})

test('decision actor: "APPROVED · <UTC> · OPERATOR · sejal"; null actor -> legacy record', () => {
  const fmt = (v) => `${String(v).slice(0, 19).replace('T', ' ')} UTC`
  const d = decisionLine({ decision: 'approved', decided_at: '2026-10-03T09:00:00+00:00', actor_username: 'sejal', actor_role: 'OPERATOR' }, fmt)
  assert.equal(d.text, 'APPROVED · 2026-10-03 09:00:00 UTC · OPERATOR · sejal')
  assert.equal(d.legacy, false)
  const legacy = decisionLine({ decision: 'dismissed', decided_at: '2026-09-01T00:00:00Z', actor_username: null, actor_role: null }, fmt)
  assert.equal(legacy.legacy, true)
  assert.equal(legacy.actor, null)
  assert.equal(legacy.text, 'DISMISSED · 2026-09-01 00:00:00 UTC')
  assert.equal(decisionLine(null).decision, 'UNKNOWN')
  // Nested reviewer shapes (new-object review) are read defensively.
  assert.equal(actorOf({ reviewed_by: { username: 'am', role: 'asset_manager' } }).text, 'ASSET_MANAGER · am')
  assert.equal(actorOf({ actor_username: 'x' }).text, '— · x')
  assert.equal(actorOf({ reviewed_by: null }), null)
  assert.equal(actorOf(undefined), null)
})

test('header: user indicator fits the adaptive compaction (name hides from level 3, icons from 5, max level 7)', async () => {
  const { readFileSync } = await import('node:fs')
  const { HEADER_MAX_COMPACT, nextHeaderCompact } = await import('./uiScale.js')
  assert.equal(HEADER_MAX_COMPACT, 7)
  assert.equal(nextHeaderCompact(6, 900, 780), 7)
  assert.equal(nextHeaderCompact(7, 900, 780), 7)
  const css = readFileSync(new URL('./theme.css', import.meta.url), 'utf8')
  for (let l = 3; l <= 7; l++) assert.ok(css.includes(`.hdr-bar[data-compact="${l}"] .hdr-user-name`), `name hidden at ${l}`)
  for (let l = 0; l <= 2; l++) assert.ok(!css.includes(`.hdr-bar[data-compact="${l}"] .hdr-user-name`), `name shown at ${l}`)
  for (let l = 5; l <= 7; l++) assert.ok(css.includes(`.hdr-bar[data-compact="${l}"] .hdr-btn-icon`), `icons at ${l}`)
})
