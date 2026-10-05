// Protected-asset management and Admin panel view models.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  ACTION_TARGET, assetActions, auditRows, canTransition, createAssetPayload, patchAssetPayload, validateAssetForm,
} from './assetAdmin.js'
import { MIN_PASSWORD_LENGTH, adminErrorText, createUserPayload, userActions, usersViewModel, validateNewUser } from './adminView.js'
import { newObjectsViewModel, registryViewModel } from './catalogView.js'

const REG = {
  assets: [
    { asset_id: 1, configured_name: 'CARTOSAT-3', name: 'CARTOSAT-3', norad_id: '44804', criticality: 'Tier1', protected: true, protected_basis: 'operator_configuration', status: 'active', name_query: 'CARTOSAT-3', exact_match: true, data_status: 'current', updated_at: '2026-10-03T08:00:00Z', updated_by: 'am' },
    { asset_id: 2, configured_name: 'RISAT-2B', name: 'RISAT-2B', norad_id: '44233', criticality: 'Tier2', protected: false, protected_basis: 'operator_configuration', status: 'suspended', name_query: 'RISAT-2B', data_status: 'current' },
    { asset_id: 3, configured_name: 'OLD-SAT', criticality: 'Tier3', protected: false, protected_basis: 'operator_registry', status: 'retired', data_status: 'unresolved' },
  ],
}

test('registry rows: status Configured / Suspended / Retired; protected only when active and operator-configured', () => {
  const vm = registryViewModel(REG)
  const byId = Object.fromEntries(vm.rows.map((r) => [r.assetId, r]))
  assert.equal(byId[1].statusLabelKey, 'catalog.configured')
  assert.equal(byId[1].protected, true)
  assert.equal(byId[1].exactMatch, true)
  assert.equal(byId[1].updatedBy, 'am')
  assert.equal(byId[2].statusLabelKey, 'assets.status.suspended')
  assert.equal(byId[2].protected, false)
  assert.equal(byId[3].statusLabelKey, 'assets.status.retired')
  assert.equal(vm.activeCount, 1)
  // Older backend: no status field -> active (configured in the file).
  const old = registryViewModel({ assets: [{ configured_name: 'X', protected: true, protected_basis: 'operator_configuration' }] })
  assert.equal(old.rows[0].status, 'active')
  assert.equal(old.rows[0].assetId, null)
  assert.deepEqual(assetActions(old.rows[0]), []) // no id -> not manageable
})

test('asset management: allowed transitions per status (retired is terminal)', () => {
  const vm = registryViewModel(REG)
  const byId = Object.fromEntries(vm.rows.map((r) => [r.assetId, r]))
  assert.deepEqual(assetActions(byId[1]), ['edit', 'suspend', 'retire'])
  assert.deepEqual(assetActions(byId[2]), ['edit', 'resume', 'retire'])
  assert.deepEqual(assetActions(byId[3]), [])
  assert.deepEqual(assetActions({ assetId: 9, status: 'weird' }), [])
  assert.deepEqual(ACTION_TARGET, { suspend: 'suspended', resume: 'active', retire: 'retired' })
  assert.equal(canTransition('active', 'suspended'), true)
  assert.equal(canTransition('suspended', 'active'), true)
  assert.equal(canTransition('active', 'retired'), true)
  assert.equal(canTransition('retired', 'active'), false)
  assert.equal(canTransition('retired', 'suspended'), false)
  assert.equal(canTransition('active', 'active'), false)
})

test('asset management: add form validation and payloads (only changed fields are patched)', () => {
  assert.deepEqual(validateAssetForm({ name_query: ' ', criticality: 'Tier9' }).errors, { name_query: 'assets.err.name', criticality: 'assets.err.criticality' })
  assert.equal(validateAssetForm({ name_query: 'GSAT-30', criticality: 'Tier2' }).ok, true)
  assert.equal(validateAssetForm({ name_query: 'x', criticality: 'Tier1', note: 'n'.repeat(501) }).errors.note, 'assets.err.note')
  assert.deepEqual(createAssetPayload({ name_query: ' GSAT-30 ', criticality: 'Tier2', exact_match: false, note: ' ' }), { name_query: 'GSAT-30', criticality: 'Tier2' })
  assert.deepEqual(createAssetPayload({ name_query: 'A', criticality: 'Tier1', exact_match: true, note: 'why' }), { name_query: 'A', criticality: 'Tier1', exact_match: true, note: 'why' })
  const row = { criticality: 'Tier1', note: 'x', exactMatch: true }
  assert.equal(patchAssetPayload(row, { criticality: 'Tier1', note: 'x', exact_match: true }), null)
  assert.deepEqual(patchAssetPayload(row, { criticality: 'Tier3', note: 'x', exact_match: false }), { criticality: 'Tier3', exact_match: false })
  assert.deepEqual(patchAssetPayload(row, { note: 'new note' }), { note: 'new note' })
})

test('governance audit rows: actor, action, target; tolerant of shapes', () => {
  const rows = auditRows({ entries: [
    { id: 1, at: '2026-10-03T08:00:00Z', actor_username: 'am', actor_role: 'ASSET_MANAGER', action: 'suspend', asset_id: 2, reason: 'maintenance' },
    { id: 2, created_at: '2026-10-03T09:00:00Z', action: 'create_user', target_username: 'newbie' },
  ] })
  assert.equal(rows[0].actor.text, 'ASSET_MANAGER · am')
  assert.equal(rows[0].action, 'SUSPEND')
  assert.equal(rows[0].target, '#2')
  assert.equal(rows[0].detail, 'maintenance')
  assert.equal(rows[1].actor, null)
  assert.equal(rows[1].target, 'newbie')
  assert.equal(auditRows([]).length, 0)
  assert.equal(auditRows({}), null)
})

test('new-object review shows the reviewer (or legacy when not recorded)', () => {
  const vm = newObjectsViewModel({ objects: [
    { norad_id: '1', review_status: 'reviewed', review: { reviewed_at: '2026-10-03T09:00:00Z', reviewed_by: { username: 'sejal', role: 'OPERATOR' } } },
    { norad_id: '2', review_status: 'reviewed', review: { reviewed_at: '2026-10-03T09:00:00Z', actor_username: 'am', actor_role: 'ASSET_MANAGER' } },
    { norad_id: '3', review_status: 'reviewed', review: { reviewed_at: '2026-09-01T00:00:00Z' } },
    { norad_id: '4', review_status: 'not_reviewed', review: null },
  ] })
  assert.equal(vm.rows[0].reviewer.text, 'OPERATOR · sejal')
  assert.equal(vm.rows[1].reviewer.text, 'ASSET_MANAGER · am')
  assert.equal(vm.rows[2].reviewer, null)
  assert.equal(vm.rows[3].reviewer, null)
})

test('admin: users table rows, create validation (password >= 10, never echoed), actions, 409 last admin', () => {
  const vm = usersViewModel([
    { id: 2, username: 'zed', role: 'viewer', active: false, created_at: 'c', last_login_at: null },
    { id: 1, username: 'admin', display_name: 'Admin', role: 'ADMINISTRATOR', active: true, last_login_at: '2026-10-03T08:00:00Z' },
    { id: 9 },
  ])
  assert.deepEqual(vm.map((u) => u.username), ['admin', 'zed'])
  assert.equal(vm[1].role, 'VIEWER')
  assert.equal(vm[1].active, false)
  assert.equal(usersViewModel({ users: [] }).length, 0)
  assert.equal(usersViewModel(null), null)
  // No password field ever appears in a row.
  assert.ok(vm.every((u) => !('password' in u)))

  assert.equal(MIN_PASSWORD_LENGTH, 10)
  assert.deepEqual(validateNewUser({ username: 'a b', role: 'KING', password: 'short' }).errors,
    { username: 'admin.err.username', role: 'admin.err.role', password: 'admin.err.password' })
  assert.equal(validateNewUser({ username: 'op2', role: 'OPERATOR', password: '0123456789' }).ok, true)
  assert.equal(validateNewUser({ username: 'op2', role: 'OPERATOR', password: '012345678' }).ok, false)
  assert.deepEqual(createUserPayload({ username: ' op2 ', display_name: ' ', role: 'OPERATOR', password: '0123456789' }),
    { username: 'op2', role: 'OPERATOR', password: '0123456789' })

  assert.deepEqual(userActions(vm[0], 5), ['role', 'password', 'deactivate'])
  assert.deepEqual(userActions(vm[1], 5), ['role', 'password', 'activate'])
  // Own account: no self-deactivation / self-demotion from the panel.
  assert.deepEqual(userActions(vm[0], 1), ['password'])

  const t = (k) => `[${k}]`
  assert.equal(adminErrorText({ status: 409, code: 'last_admin', message: 'Cannot remove the last active administrator.' }, t),
    '[admin.lastAdmin] — Cannot remove the last active administrator.')
  assert.equal(adminErrorText({ status: 409, message: 'Username already exists.' }, t), '[admin.conflict] — Username already exists.')
  assert.equal(adminErrorText({ status: 409, message: '/api/admin/users/1 -> HTTP 409' }, t), '[admin.conflict]')
  assert.equal(adminErrorText({ status: 403, message: 'forbidden detail' }, t), 'forbidden detail')
})
