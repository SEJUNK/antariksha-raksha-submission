import { test } from 'node:test'
import assert from 'node:assert/strict'
import { filterCatalogObjects, newObjectsViewModel, registryViewModel } from './catalogView.js'
import { formatRefreshParts } from './mode.js'

test('registry: configured assets only, protected from configuration, sorted by criticality, catalog kept separate', () => {
  const vm = registryViewModel({
    registry_source: 'Operator configuration: data/working_set.json, Group A',
    assets: [
      { configured_name: 'B-SAT', name: 'B-SAT', norad_id: '2', criticality: 'Tier3', protected: true, protected_basis: 'operator_configuration', data_status: 'current', source_format: 'tle', tle_age_days: 0.8 },
      { configured_name: 'A-SAT', name: null, norad_id: null, criticality: 'Tier1', protected: true, protected_basis: 'operator_configuration', data_status: 'unresolved', unresolved_reason: 'ambiguous: 3 matches' },
    ],
    catalog: { source: 'CelesTrak public GP data (working set)', active_objects: 103, inactive_objects: 2, by_type: { satellite: 11, debris: 80 }, by_source_format: { tle: 103 } },
  })
  assert.deepEqual(vm.rows.map((r) => r.configuredName), ['A-SAT', 'B-SAT'])
  assert.equal(vm.rows[0].statusKey, 'catalog.status.unresolved')
  assert.equal(vm.rows[0].reason, 'ambiguous: 3 matches')
  assert.equal(vm.rows[0].noradId, '—')
  assert.equal(vm.rows[1].statusTone, 'safe')
  assert.equal(vm.rows[1].format, 'TLE')
  assert.equal(vm.rows[1].age, '0.8 d')
  assert.ok(vm.rows.every((r) => r.protected))
  assert.equal(vm.catalog.active, 103)
  assert.deepEqual(vm.counts, { total: 2, attention: 1 })
})

test('registry: protected only with operator-configuration basis; bad input -> null', () => {
  const vm = registryViewModel({ assets: [{ configured_name: 'X', protected: true, protected_basis: 'guess', data_status: 'current' }] })
  assert.equal(vm.rows[0].protected, false)
  assert.equal(registryViewModel(null), null)
  assert.equal(registryViewModel({}), null)
})

test('new objects: first seen, orbital data, review status; empty list; never implies launch/retirement fields', () => {
  const vm = newObjectsViewModel({
    baseline: '2026-10-03T07:00:00+00:00',
    objects: [
      { norad_id: '270000', name: 'NEW-OBJ', object_type: 'debris', first_seen: '2026-10-03T08:00:00+00:00', source_format: 'omm', orbital_data: 'available', active: false, review_status: 'not_reviewed', review: null },
      { norad_id: '3', name: 'DEB-3', object_type: 'debris', first_seen: '2026-10-03T07:30:00+00:00', source_format: 'tle', orbital_data: 'available', active: true, review_status: 'reviewed', review: { reviewed_at: '2026-10-03T09:00:00+00:00', note: 'ok' } },
    ],
  })
  assert.equal(vm.notReviewed, 1)
  assert.equal(vm.rows[0].format, 'OMM')
  assert.equal(vm.rows[0].noradId, '270000')
  assert.equal(vm.rows[0].activeKey, 'catalog.inactiveNow')
  assert.equal(vm.rows[1].reviewed, true)
  assert.equal(vm.rows[1].typeKey, 'objType.debris')
  assert.ok(!JSON.stringify(vm).match(/launch|retire/i))
  assert.equal(newObjectsViewModel({ objects: [] }).empty, true)
})

test('catalog search/filter: protected = configured assets only; debris / other; search by name or NORAD ID', () => {
  const objs = [
    { norad_id: '44804', name: 'CARTOSAT-3', object_type: 'satellite', criticality: 'Tier2', protected: true },
    { norad_id: '25730', name: 'FENGYUN 1C DEB', object_type: 'debris', criticality: null },
    { norad_id: '40697', name: 'SENTINEL-2A', object_type: 'foreign_sat', criticality: null },
    { norad_id: '270000', name: 'NEW OBJ', object_type: 'debris', criticality: null },
  ]
  assert.equal(filterCatalogObjects(objs).length, 4)
  assert.equal(filterCatalogObjects(objs)[0].noradId, '44804') // protected first
  assert.deepEqual(filterCatalogObjects(objs, '', 'protected').map((o) => o.noradId), ['44804'])
  assert.deepEqual(filterCatalogObjects(objs, '', 'debris').map((o) => o.noradId).sort(), ['25730', '270000'])
  assert.deepEqual(filterCatalogObjects(objs, '', 'other').map((o) => o.noradId), ['40697'])
  assert.deepEqual(filterCatalogObjects(objs, 'fengyun').map((o) => o.noradId), ['25730'])
  assert.deepEqual(filterCatalogObjects(objs, '2700').map((o) => o.noradId), ['270000'])
  assert.equal(filterCatalogObjects(objs, 'zzz').length, 0)
  assert.ok(filterCatalogObjects(objs).every((o) => o.protected === (o.noradId === '44804')))
  assert.deepEqual(filterCatalogObjects(null), [])
})

test('catalog: protected comes only from the backend registry flag, never from object type', () => {
  const objs = [
    { norad_id: '50000', name: 'ORDINARY SAT', object_type: 'satellite', criticality: 'Tier1' },
    { norad_id: '44804', name: 'CARTOSAT-3', object_type: 'satellite', criticality: 'Tier2', protected: true },
    { norad_id: '60000', name: 'SUSPENDED ASSET', object_type: 'satellite', criticality: 'Tier1', protected: false },
  ]
  assert.deepEqual(filterCatalogObjects(objs, '', 'protected').map((o) => o.noradId), ['44804'])
  assert.ok(filterCatalogObjects(objs).every((o) => o.protected === (o.noradId === '44804')))
})

test('last successful refresh: UTC line plus local time; absent -> nulls', () => {
  const p = formatRefreshParts('2026-10-03T10:47:01+00:00')
  assert.equal(p.utc, '3 Oct 2026 · 10:47:01 UTC')
  assert.ok(p.local === null || /\d\d:\d\d:\d\d/.test(p.local))
  assert.deepEqual(formatRefreshParts(null), { utc: null, local: null })
})
