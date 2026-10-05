// Pure view models (no React, no DOM) for the event drawer's trust and
// transparency blocks, so node:test can cover them:
//
// - aiBoundary / headerAiStatus: what the AI did and did not do for THIS
//   event, and whether the local AI is available. An AI problem is never
//   presented as a physics problem.
// - assessmentBasis: the facts an assessment rests on (data, propagation,
//   assessment inputs, data coverage) and its limitations. No numerical
//   "confidence score" is invented.
// - analystSummary: a deterministic analyst-facing summary assembled from
//   values the backend already computed. No LLM, no new physics.
// - eventTimeline: chronological screening observations and operator
//   decisions for the event.
//
// Every number is passed through as a display string; nothing is recomputed.

import { BRIEF_LLM, BRIEF_PENDING, briefState, isToneGuardFallback } from './briefState.js'
import { formatPc, TREND_KEYS } from './eventEvolution.js'

const isNum = (v) => typeof v === 'number' && Number.isFinite(v)
const str = (v) => (v === null || v === undefined || v === '' ? null : String(v))

// ---------------------------------------------------------------------------
// AI boundary and status
// ---------------------------------------------------------------------------
export const AI_DID_NOT_KEYS = [
  'ai.didNot.orbit',
  'ai.didNot.tca',
  'ai.didNot.miss',
  'ai.didNot.pc',
  'ai.didNot.risk',
  'ai.didNot.command',
  'ai.didNot.maneuver',
  'ai.didNot.decision',
]

/**
 * aiBoundary(event, { reviewStatus, translationShown }) ->
 * { status: {key, tone}, did: [keys], didNot: [keys], statementKey }
 */
export function aiBoundary(event, { reviewStatus = null, translationShown = false } = {}) {
  const state = briefState(event)
  let status
  let did
  if (state === BRIEF_PENDING) {
    status = { key: 'ai.status.pending', tone: 'neutral' }
    did = ['ai.did.pending']
  } else if (isToneGuardFallback(event)) {
    // The local LLM DID draft a brief, but the deterministic tone guard
    // rejected it; the template is shown instead. Not an AI outage.
    status = { key: 'ai.status.toneGuard', tone: 'warn' }
    did = ['ai.did.toneGuard']
  } else if (state !== BRIEF_LLM) {
    // Deterministic template: the local LLM did not draft this brief.
    status = { key: 'ai.status.unavailable', tone: 'warn' }
    did = ['ai.did.none']
  } else {
    if (reviewStatus === 'flagged') status = { key: 'ai.status.checkFailed', tone: 'warn' }
    else if (reviewStatus === 'skipped') status = { key: 'ai.status.checkNotRun', tone: 'warn' }
    else status = { key: 'ai.status.available', tone: 'safe' }
    did = ['ai.did.summarise', 'ai.did.explain', 'ai.did.readable']
    if (reviewStatus === 'consistent' || reviewStatus === 'flagged') did.push('ai.did.check')
  }
  if (translationShown) did.push('ai.did.translate')
  return { status, did, didNot: [...AI_DID_NOT_KEYS], statementKey: 'ai.statement' }
}

/**
 * Header AI availability from /api/health; null until health is known.
 * Prefers health.ai.status (LOCAL_AI_AVAILABLE | AI_FALLBACK_ACTIVE), then
 * health.ai.available, then the older ollama_reachable flag. Anything not
 * positively available reads as the deterministic fallback.
 */
export function headerAiStatus(health) {
  if (!health || typeof health !== 'object') return null
  const ai = health.ai && typeof health.ai === 'object' ? health.ai : null
  let available
  if (ai?.status === 'LOCAL_AI_AVAILABLE') available = true
  else if (ai?.status === 'AI_FALLBACK_ACTIVE') available = false
  else if (typeof ai?.available === 'boolean') available = ai.available
  else available = health.ollama_reachable === true
  return available
    ? { key: 'header.aiAvailable', tone: 'safe' }
    : { key: 'header.aiOffline', tone: 'warn' }
}

// ---------------------------------------------------------------------------
// Assessment basis, data coverage and limitations
// ---------------------------------------------------------------------------
export const COVERAGE = {
  connected: ['basis.cov.catalog'],
  notConnected: ['basis.cov.radar', 'basis.cov.optical', 'basis.cov.ephemeris', 'basis.cov.covariance'],
}

export const BASIS_LIMITATION_KEYS = [
  'basis.lim.public',
  'basis.lim.uncertainty',
  'basis.lim.notCov',
  'basis.lim.notPc',
  'basis.lim.prototype',
]

function formatsOf(...objs) {
  const set = new Set(objs.map((o) => str(o?.source_format)).filter(Boolean).map((f) => f.toUpperCase()))
  return set.size ? [...set].sort().join(' + ') : null
}

/**
 * Small qualifier for the horizon / step rows: which run they come from, or
 * that the configured value is shown because the run did not record one.
 * -> { noteKey, noteVars } | {}
 */
export function horizonNote(prop, run, ev) {
  const source = str(prop?.horizon_source)
  if (source === 'config_fallback') return { noteKey: 'basis.horizonConfig', noteVars: {} }
  const id = prop?.screening_run_id ?? run?.id ?? ev?.screening_run_id
  if (source === 'screening_run' && id != null) return { noteKey: 'basis.horizonRun', noteVars: { n: id } }
  return {}
}

/**
 * assessmentBasis(event, provenance, formatUtc) -> { groups: [{titleKey, rows:[{labelKey, value|valueKey}]}],
 * coverage, limitationKeys }. Rows with no value are omitted (never invented).
 */
export function assessmentBasis(event, provenance, formatUtc = (v) => v) {
  const ev = event || {}
  const p = provenance && typeof provenance === 'object' ? provenance : {}
  const data = p.data || {}
  const prop = p.propagation || {}
  const prob = p.probability || {}
  const analysis = p.analysis || {}
  const run = data.screening_run && typeof data.screening_run === 'object' ? data.screening_run : null
  const objA = data.object_a || {}
  const objB = data.object_b || {}
  const isCollision = ev.event_class === 'collision_risk'
  const mode = str(run?.mode) || (ev.is_demo ? 'demo' : null)

  const row = (labelKey, value, extra = {}) => (value === null || value === undefined ? null : { labelKey, value: String(value), ...extra })
  const ages = [objA, objB].map((o) => (isNum(o.tle_age_days) ? `${o.name || o.norad_id || '?'}: ${o.tle_age_days} d` : null)).filter(Boolean)

  const groups = [
    {
      titleKey: 'basis.data',
      rows: [
        row('basis.source', str(data.source)),
        row('basis.format', formatsOf(objA, objB)),
        row('basis.age', ages.length ? ages.join(' · ') : null),
        row('basis.run', run?.id != null || ev.screening_run_id != null
          ? `#${run?.id ?? ev.screening_run_id}${mode ? ` · ${mode.toUpperCase()}` : ''}`
          : null),
      ].filter(Boolean),
    },
    {
      titleKey: 'basis.propagation',
      rows: [
        row('basis.engine', str(prop.engine)),
        // Horizon and step of the event's OWN screening run (provenance
        // propagation); config_fallback = run did not record them.
        row('basis.screenHorizon', prop.horizon_hours != null ? `${prop.horizon_hours} h` : null, horizonNote(prop, run, ev)),
        row('basis.propStep', prop.step_seconds != null ? `${prop.step_seconds} s` : null, horizonNote(prop, run, ev)),
      ].filter(Boolean),
    },
    {
      titleKey: 'basis.assessment',
      rows: [
        row('basis.tca', ev.tca_timestamp ? formatUtc(ev.tca_timestamp) : null),
        row('basis.tcaMethod', str(analysis.tca_refinement ?? ev.tca_refinement)),
        row('basis.miss', isNum(ev.miss_distance_km) ? `${ev.miss_distance_km.toFixed(3)} km` : null),
        isCollision
          ? row('basis.indicator', isNum(ev.pc_score) ? formatPc(ev.pc_score) : null)
          : { labelKey: 'basis.indicator', valueKey: 'basis.notComputed' },
        isCollision
          ? row('basis.uncertainty', str(prob.uncertainty_model)) || { labelKey: 'basis.uncertainty', valueKey: 'risk.uncDefault' }
          : null,
      ].filter(Boolean),
    },
  ]
  return { groups, coverage: COVERAGE, limitationKeys: [...BASIS_LIMITATION_KEYS] }
}

// ---------------------------------------------------------------------------
// Analyst summary (deterministic)
// ---------------------------------------------------------------------------
/**
 * analystSummary({event, provenance, evolutionVm, history, nameA, nameB, formatUtc}) ->
 * [{id, labelKey, parts: [{key, vars} | {text}]}]
 */
export function analystSummary({ event, provenance, evolutionVm, history, nameA, nameB, formatUtc = (v) => v }) {
  const ev = event || {}
  const risk = provenance?.risk && typeof provenance.risk === 'object' ? provenance.risk : {}
  const isCollision = ev.event_class === 'collision_risk'
  const rows = []
  const add = (id, labelKey, parts) => { if (parts.length) rows.push({ id, labelKey, parts }) }

  add('what', 'analyst.what', [{ key: isCollision ? 'analyst.whatCollision' : 'analyst.whatProximity', vars: { a: nameA ?? '?', b: nameB ?? '?' } }])
  add('when', 'analyst.when', ev.tca_timestamp ? [{ text: formatUtc(ev.tca_timestamp) }] : [])
  const miss = isNum(ev.miss_distance_km) ? ev.miss_distance_km.toFixed(3) : null
  const rel = isNum(ev.rel_velocity_km_s) ? ev.rel_velocity_km_s.toFixed(2) : null
  add('close', 'analyst.close', miss
    ? [{ key: rel ? 'analyst.closeValue' : 'analyst.closeMissOnly', vars: { miss, rel } }]
    : [])
  add('indicator', 'analyst.indicator', isCollision
    ? (isNum(ev.pc_score) ? [{ key: 'analyst.pcValue', vars: { pc: formatPc(ev.pc_score), tier: ev.risk_tier ?? '?' } }] : [])
    : [{ key: 'analyst.noPc', vars: { tier: ev.risk_tier ?? '?' } }])

  const prio = isNum(risk.priority_score ?? ev.priority_score) ? (risk.priority_score ?? ev.priority_score).toFixed(3) : null
  const basis = risk.priority_basis || (isCollision ? 'pc_x_criticality' : 'proximity_dwell')
  if (basis === 'proximity_dwell') {
    add('why', 'analyst.why', [{ key: 'analyst.whyProx', vars: { dwell: isNum(risk.dwell_minutes) ? risk.dwell_minutes.toFixed(1) : '—', prio: prio ?? '—' } }])
  } else {
    const crit = risk.criticality_effective ?? risk.criticality
    const mult = risk.criticality_multiplier_effective ?? risk.criticality_multiplier
    add('why', 'analyst.why', [{ key: 'analyst.whyPc', vars: { tier: ev.risk_tier ?? '?', crit: crit ?? '—', mult: isNum(mult) ? String(mult) : '—', prio: prio ?? '—' } }])
  }

  if (evolutionVm) {
    if (evolutionVm.single) add('changed', 'analyst.changed', [{ key: 'evo.single' }])
    else {
      const parts = evolutionVm.trends.map((tr) => ({ key: tr.key, vars: tr.vars }))
      if (evolutionVm.pcChange) parts.push({ key: evolutionVm.pcChange.key, vars: evolutionVm.pcChange.vars })
      add('changed', 'analyst.changed', parts.length ? parts : [{ key: 'analyst.noTrend' }])
    }
  }

  const ageA = provenance?.data?.object_a?.tle_age_days
  const ageB = provenance?.data?.object_b?.tle_age_days
  const worst = Math.max(isNum(ageA) ? ageA : -1, isNum(ageB) ? ageB : -1)
  const lim = []
  if (worst >= 0) lim.push({ key: 'analyst.dataAge', vars: { d: worst.toFixed(1) } })
  lim.push({ key: 'analyst.limits' })
  if (ev.is_demo) lim.push({ key: 'analyst.demo' })
  add('limits', 'analyst.limitsLabel', lim)

  const latest = Array.isArray(history) && history.length ? history[0] : null
  const status = str(ev.status) || 'pending'
  add('decision', 'analyst.decision', status === 'pending' && !latest
    ? [{ key: 'analyst.decPending' }]
    : [{ key: 'analyst.decRecorded', vars: { d: String(latest?.decision || status).toUpperCase(), at: latest?.decided_at ? formatUtc(latest.decided_at) : '—' } }])
  return rows
}

// ---------------------------------------------------------------------------
// Event timeline
// ---------------------------------------------------------------------------
const KIND_ORDER = { observation: 0, ai: 1, decision: 2 }

/**
 * eventTimeline(evolutionResp, history, event) -> chronological entries
 * [{kind, at, labelKey, vars, isDemo, isCurrent}]. Observations come from the
 * stored screening observations, decisions from the audit trail; the AI
 * explanation is attached to the current observation (briefs carry no
 * timestamp of their own).
 */
export function eventTimeline(evolutionResp, history, event) {
  const ev = event || {}
  const obs = Array.isArray(evolutionResp?.observations) ? evolutionResp.observations.filter((o) => o && typeof o === 'object') : []
  const entries = []
  obs.forEach((o, i) => {
    const isProx = o.event_class === 'proximity_watch'
    entries.push({
      kind: 'observation',
      at: str(o.run_started_at),
      labelKey: i === 0 ? 'timeline.firstObserved' : (o.is_current ? 'timeline.current' : 'timeline.observed'),
      vars: {
        run: o.screening_run_id ?? '?',
        miss: isNum(o.miss_distance_km) ? o.miss_distance_km.toFixed(3) : '—',
        tier: o.risk_tier ?? '—',
        pc: isProx ? '—' : (formatPc(o.pc_score) ?? '—'),
      },
      isDemo: Boolean(o.is_demo),
      isCurrent: Boolean(o.is_current),
    })
    if (o.is_current) {
      const state = briefState(ev)
      entries.push({
        kind: 'ai',
        at: str(o.run_started_at),
        labelKey: state === BRIEF_PENDING ? 'timeline.aiPending' : state === BRIEF_LLM ? 'timeline.aiLlm' : 'timeline.aiTemplate',
        vars: {},
        isDemo: Boolean(o.is_demo),
        isCurrent: true,
      })
    }
  })
  for (const d of Array.isArray(history) ? history : []) {
    if (!d || typeof d !== 'object') continue
    entries.push({
      kind: 'decision',
      at: str(d.decided_at),
      labelKey: 'timeline.decision',
      vars: { d: String(d.decision || d.status || '?').toUpperCase(), reason: d.rejection_reason || '' },
      isDemo: Boolean(d.is_demo),
      isCurrent: false,
    })
  }
  return entries
    .map((e, i) => ({ e, i }))
    .sort((x, y) => {
      const a = x.e.at || ''
      const b = y.e.at || ''
      if (a !== b) return a < b ? -1 : 1
      if (KIND_ORDER[x.e.kind] !== KIND_ORDER[y.e.kind]) return KIND_ORDER[x.e.kind] - KIND_ORDER[y.e.kind]
      return x.i - y.i
    })
    .map(({ e }) => e)
}

export { TREND_KEYS }
