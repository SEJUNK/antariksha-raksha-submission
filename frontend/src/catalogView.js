// Pure view models (no React) for the catalog panel: the operator-configured
// PROTECTED ASSET REGISTRY kept apart from the public OBJECT CATALOG, and the
// list of objects new to the LOCAL catalog. Values are shown as the backend
// returns them; protected status is never derived here.

import { actorOf } from './auth.js'

const isNum = (v) => typeof v === 'number' && Number.isFinite(v)

// Registry lifecycle status (operator-managed): active = Configured
// (protected), suspended / retired = kept for audit, not protected.
export const ASSET_STATUS_KEYS = {
  active: 'catalog.configured',
  suspended: 'assets.status.suspended',
  retired: 'assets.status.retired',
}
// Protected only by operator configuration (config file or the operator-
// managed registry) -- never inferred from orbital data.
const OPERATOR_BASIS = /^operator(_|$)/

export const DATA_STATUS_KEYS = {
  current: 'catalog.status.current',
  stale: 'catalog.status.stale',
  inactive: 'catalog.status.inactive',
  unresolved: 'catalog.status.unresolved',
}

export function registryViewModel(resp) {
  if (!resp || typeof resp !== 'object' || !Array.isArray(resp.assets)) return null
  const order = { Tier1: 1, Tier2: 2, Tier3: 3 }
  const rows = resp.assets
    .filter((a) => a && typeof a === 'object')
    .map((a) => ({
      key: a.asset_id ?? a.configured_name ?? a.norad_id,
      assetId: a.asset_id ?? null,
      // Missing status (older backend) = configured in the file = active.
      status: ['active', 'suspended', 'retired'].includes(a.status) ? a.status : 'active',
      statusLabelKey: ASSET_STATUS_KEYS[a.status] || ASSET_STATUS_KEYS.active,
      nameQuery: a.name_query ?? a.configured_name ?? null,
      exactMatch: a.exact_match === true,
      updatedAt: a.updated_at || null,
      updatedBy: a.updated_by && typeof a.updated_by === 'object' ? (a.updated_by.username || null) : (a.updated_by || null),
      name: a.name || a.configured_name || '—',
      configuredName: a.configured_name || null,
      noradId: a.norad_id || '—',
      criticality: a.criticality || '—',
      owner: a.owner || '—',
      note: a.note || null,
      statusKey: DATA_STATUS_KEYS[a.data_status] || 'catalog.status.unknown',
      statusTone: a.data_status === 'current' ? 'safe' : 'warn',
      reason: a.unresolved_reason || null,
      format: a.source_format ? String(a.source_format).toUpperCase() : '—',
      age: isNum(a.tle_age_days) ? `${a.tle_age_days} d` : '—',
      // Only the operator configuration makes an asset protected.
      protected: a.protected === true && OPERATOR_BASIS.test(String(a.protected_basis || '')),
    }))
    .sort((x, y) => (order[x.criticality] || 9) - (order[y.criticality] || 9) || String(x.name).localeCompare(String(y.name)))
  const cat = resp.catalog && typeof resp.catalog === 'object' ? resp.catalog : {}
  return {
    rows,
    registrySource: resp.registry_source || null,
    catalog: {
      source: cat.source || null,
      active: isNum(cat.active_objects) ? cat.active_objects : null,
      inactive: isNum(cat.inactive_objects) ? cat.inactive_objects : null,
      byType: cat.by_type && typeof cat.by_type === 'object' ? cat.by_type : {},
      byFormat: cat.by_source_format && typeof cat.by_source_format === 'object' ? cat.by_source_format : {},
    },
    counts: {
      total: rows.length,
      attention: rows.filter((r) => r.statusTone === 'warn').length,
    },
    activeCount: rows.filter((r) => r.status === 'active').length,
  }
}

export function newObjectsViewModel(resp) {
  if (!resp || typeof resp !== 'object' || !Array.isArray(resp.objects)) return null
  const rows = resp.objects.filter((o) => o && typeof o === 'object').map((o) => ({
    key: o.norad_id,
    noradId: o.norad_id,
    name: o.name || '—',
    typeKey: o.object_type ? `objType.${o.object_type}` : null,
    firstSeen: o.first_seen || null,
    format: o.source_format ? String(o.source_format).toUpperCase() : '—',
    orbitalKey: o.orbital_data === 'available' ? 'catalog.orbital.available' : 'catalog.orbital.missing',
    activeKey: o.active ? 'catalog.active' : 'catalog.inactiveNow',
    reviewed: o.review_status === 'reviewed',
    reviewedAt: o.review?.reviewed_at || null,
    note: o.review?.note || null,
    // Who acknowledged it ("OPERATOR · sejal"); null = legacy record.
    reviewer: o.review_status === 'reviewed' ? (actorOf(o.review) || actorOf(o)) : null,
  }))
  return {
    rows,
    baseline: resp.baseline || null,
    notReviewed: rows.filter((r) => !r.reviewed).length,
    empty: rows.length === 0,
  }
}

// Public object catalog search/filter (client-side over the objects the app
// already holds). 'protected' is the backend's registry-derived flag
// (/api/objects); it is never inferred from object type here.
export const CATALOG_FILTERS = ['all', 'protected', 'debris', 'other']

export function filterCatalogObjects(objects, query = '', filter = 'all') {
  const q = String(query || '').trim().toLowerCase()
  const list = Array.isArray(objects) ? objects.filter((o) => o && typeof o === 'object') : []
  return list
    .filter((o) => {
      const isProtected = o.protected === true
      if (filter === 'protected' && !isProtected) return false
      if (filter === 'debris' && o.object_type !== 'debris') return false
      if (filter === 'other' && o.object_type !== 'foreign_sat') return false
      if (!q) return true
      return String(o.name || '').toLowerCase().includes(q) || String(o.norad_id || '').toLowerCase().includes(q)
    })
    .map((o) => ({
      key: o.norad_id,
      name: o.name || '—',
      noradId: o.norad_id || '—',
      typeKey: o.object_type ? `objType.${o.object_type}` : null,
      protected: o.protected === true,
      demoAdjusted: Boolean(o.demo_adjusted),
    }))
    .sort((a, b) => Number(b.protected) - Number(a.protected) || String(a.name).localeCompare(String(b.name)))
}
