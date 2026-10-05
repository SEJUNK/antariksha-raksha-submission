// Pure view model for the "Event Evolution" drawer section (no React, no DOM)
// so node:test can cover it. Input: GET /api/events/{id}/evolution.
//
// Everything shown is a stored backend value. Trend labels come ONLY from the
// backend `trends` object -- the frontend never derives a trend, a confidence
// or a risk value of its own. Missing fields are hidden, never invented.

const isNum = (v) => typeof v === 'number' && Number.isFinite(v)

// Backend trend code -> i18n key (+ a neutral direction glyph).
export const TREND_KEYS = {
  miss_distance: {
    increasing: { key: 'evo.missInc', glyph: '↑' },
    decreasing: { key: 'evo.missDec', glyph: '↓' },
    stable: { key: 'evo.missStable', glyph: '→' },
  },
  risk_tier: {
    increased: { key: 'evo.tierInc', glyph: '↑' },
    decreased: { key: 'evo.tierDec', glyph: '↓' },
    unchanged: { key: 'evo.tierSame', glyph: '→' },
  },
}

// Correlation rule enum code -> i18n key.
export const CORRELATION_RULE_KEYS = {
  pair_class_nearest_tca_v1: 'evo.rule.pairClassTca',
}

/** Signed, rounded seconds ("+42", "−1,234", "0"); null when not a number. */
export function formatTcaShift(seconds) {
  if (!isNum(seconds)) return null
  const r = Math.round(seconds)
  const abs = Math.abs(r).toLocaleString('en-US')
  if (r > 0) return `+${abs}`
  if (r < 0) return `−${abs}`
  return '0'
}

/** "YYYY-MM-DD HH:MM:SS UTC" (language-independent), or null. */
export function formatUtcShort(iso) {
  if (!iso || typeof iso !== 'string') return null
  return `${iso.slice(0, 19).replace('T', ' ')} UTC`
}

/** Pc display for a stored value; null when absent (never computed here). */
export function formatPc(pc) {
  if (!isNum(pc)) return null
  if (pc <= 0) return '≈ 0'
  return pc.toExponential(2)
}

/**
 * Display of the backend's stored Pc ratio (current / previous) as "×N".
 * Pure formatting -- no direction word, no interpretation; null when absent.
 */
export function formatPcRatio(ratio) {
  if (!isNum(ratio) || ratio <= 0) return null
  if (ratio >= 1000 || ratio < 0.001) return ratio.toExponential(1)
  return String(+ratio.toPrecision(3))
}

function demoHistoryOf(v) {
  if (!v || typeof v !== 'object') return null
  return {
    id: v.id ?? null,
    step: isNum(v.step) ? v.step : null,
    of: isNum(v.of) ? v.of : null,
  }
}

function rowOf(o, index) {
  const isProximity = o.event_class === 'proximity_watch'
  return {
    key: o.observation_id ?? `${o.screening_run_id ?? 'run'}-${index}`,
    labelKey: o.is_current ? 'evo.current' : 'evo.obs',
    labelVars: { n: index + 1 },
    index: index + 1,
    runId: o.screening_run_id ?? null,
    runTime: formatUtcShort(o.run_started_at),
    runMode: typeof o.run_mode === 'string' ? o.run_mode : null,
    isDemo: Boolean(o.is_demo),
    eventClass: o.event_class ?? null,
    tca: formatUtcShort(o.tca_timestamp),
    miss: isNum(o.miss_distance_km) ? o.miss_distance_km.toFixed(3) : null,
    relVel: isNum(o.rel_velocity_km_s) ? o.rel_velocity_km_s.toFixed(2) : null,
    // Proximity events have no Pc: shown as "not computed", never as a number.
    pc: isProximity ? null : formatPc(o.pc_score),
    pcNotComputed: isProximity,
    tier: typeof o.risk_tier === 'string' && o.risk_tier ? o.risk_tier : null,
    isCurrent: Boolean(o.is_current),
    ambiguous: Boolean(o.ambiguous_match),
    pcRaw: !isProximity && isNum(o.pc_score) ? o.pc_score : null,
    demoHistory: demoHistoryOf(o.demo_history),
  }
}

/**
 * evolutionViewModel(resp) -> null (nothing usable) or
 * { rows, single, historyAvailable, noteKeys, trends, correlation, stableTip,
 *   ambiguous, isDemo }
 */
export function evolutionViewModel(resp) {
  if (!resp || typeof resp !== 'object') return null
  const obs = Array.isArray(resp.observations)
    ? resp.observations.filter((o) => o && typeof o === 'object')
    : []
  // Backend sends ascending order; keep a stable defensive sort by start time.
  const sorted = obs
    .map((o, i) => ({ o, i }))
    .sort((a, b) => {
      const ta = a.o.run_started_at ? String(a.o.run_started_at) : ''
      const tb = b.o.run_started_at ? String(b.o.run_started_at) : ''
      if (ta && tb && ta !== tb) return ta < tb ? -1 : 1
      return a.i - b.i
    })
    .map(({ o }) => o)
  const rows = sorted.map(rowOf)
  const historyAvailable = resp.history_available !== false
  const single = rows.length <= 1

  const noteKeys = []
  if (single) noteKeys.push('evo.single')
  if (!historyAvailable) noteKeys.push('evo.notRecorded')

  // Trend labels only when there is something to compare.
  const trends = []
  const tr = resp.trends && typeof resp.trends === 'object' ? resp.trends : {}
  if (!single) {
    const miss = TREND_KEYS.miss_distance[tr.miss_distance]
    if (miss) trends.push({ id: 'miss', key: miss.key, glyph: miss.glyph })
    const tier = TREND_KEYS.risk_tier[tr.risk_tier]
    if (tier) trends.push({ id: 'tier', key: tier.key, glyph: tier.glyph })
    const shift = formatTcaShift(tr.tca_shift_seconds)
    if (shift !== null) trends.push({ id: 'tca', key: 'evo.tcaShift', vars: { v: shift }, glyph: '⇄' })
  }

  let stableTip = null
  if (!single && isNum(tr.miss_stable_threshold_km) && isNum(tr.miss_stable_threshold_rel)) {
    stableTip = {
      key: 'evo.stableTip',
      vars: { km: String(tr.miss_stable_threshold_km), rel: String(+(tr.miss_stable_threshold_rel * 100).toFixed(1)) },
    }
  }

  const ruleKey = CORRELATION_RULE_KEYS[resp.correlation_rule] || null
  const correlation = ruleKey && !single
    ? { key: ruleKey, vars: { s: isNum(resp.correlation_window_seconds) ? String(Math.round(resp.correlation_window_seconds)) : '—' } }
    : null

  // Collision indicator change, kept separate from the risk-tier trend: the
  // two most recent stored Pc values (old -> new) plus the backend's stored
  // ratio when given. No direction label is derived here.
  let pcChange = null
  if (!single) {
    const prev = rows[rows.length - 2]
    const cur = rows[rows.length - 1]
    if (prev.pc && cur.pc && !prev.pcNotComputed && !cur.pcNotComputed) {
      pcChange = { key: 'evo.pcChange', vars: { old: prev.pc, new: cur.pc }, ratio: formatPcRatio(tr.pc_ratio) }
    }
  }

  // DEMO HISTORY metadata: top level and/or per observation (latest wins).
  const demoHistory = demoHistoryOf(resp.demo_history)
    || [...rows].reverse().map((r) => r.demoHistory).find(Boolean)
    || null
  const isDemo = Boolean(resp.is_demo) || rows.some((r) => r.isDemo) || demoHistory !== null

  return {
    rows,
    single,
    historyAvailable,
    noteKeys,
    trends,
    pcChange,
    correlation,
    stableTip,
    ambiguous: rows.some((r) => r.ambiguous),
    isDemo,
    demoHistory,
    // "◆ DEMO HISTORY — controlled geometry" whenever the track is demo.
    demoBanner: isDemo
      ? { key: 'evo.demoHistory', stepKey: demoHistory && demoHistory.step !== null && demoHistory.of !== null ? 'evo.demoStep' : null, stepVars: demoHistory ? { step: demoHistory.step, of: demoHistory.of } : null }
      : null,
  }
}
