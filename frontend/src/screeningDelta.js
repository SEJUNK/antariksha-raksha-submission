// Pure view model for the "What changed since last refresh?" card (no React,
// no DOM) so node:test can cover it. Input: GET /api/screening/delta, or the
// smaller `delta` summary carried by the /api/refresh response.
//
// Every value shown is a stored backend value; group membership (new / risk
// increased / ...) is decided by the backend. A demo-domain comparison is
// always flagged `demo` so it can never be presented as live data.

import { formatPc, formatPcRatio, formatTcaShift, formatUtcShort } from './eventEvolution.js'

const isNum = (v) => typeof v === 'number' && Number.isFinite(v)
const DASH = '—'

// Order = display order. [backend group key, i18n label key]
export const DELTA_GROUPS = [
  ['new', 'delta.new'],
  ['increased', 'delta.increased'], // "Risk tier increased" (tier bucket, not Pc)
  ['decreased', 'delta.decreased'], // "Risk tier decreased"
  ['unchanged', 'delta.unchanged'],
  ['not_present', 'delta.notPresent'],
]

const MISS_TREND_KEYS = { increasing: 'evo.missInc', decreasing: 'evo.missDec', stable: 'evo.missStable' }

// Badge for the run's data mode. 'live' only when the backend says so, and
// never for a demo-domain comparison.
function badgeKey(demo, mode) {
  if (demo) return 'mode.demo'
  if (mode === 'live' || mode === 'cached' || mode === 'stale') return `mode.${mode}`
  return 'prov.public'
}

function itemOf(it, index) {
  const prev = it.previous && typeof it.previous === 'object' ? it.previous : null
  const cur = it.current && typeof it.current === 'object' ? it.current : null
  const miss = (s) => (s && isNum(s.miss_distance_km) ? s.miss_distance_km.toFixed(3) : DASH)
  const tca = (s) => (s && formatUtcShort(s.tca_timestamp)) || DASH
  const tier = (s) => (s && typeof s.risk_tier === 'string' && s.risk_tier) || DASH
  const nameA = it.object_a_name || (it.object_a_id != null ? String(it.object_a_id) : DASH)
  const nameB = it.object_b_name || (it.object_b_id != null ? String(it.object_b_id) : DASH)
  const shift = formatTcaShift(it.tca_shift_seconds)
  // Stored Pc old -> new (collision only; proximity has no Pc). Hidden when
  // neither snapshot carries a Pc. Ratio = backend pc_ratio, display only.
  const isProximity = it.event_class === 'proximity_watch'
  const pcOf = (s) => (s && !isProximity ? formatPc(s.pc_score) : null)
  const pcOld = pcOf(prev)
  const pcNew = pcOf(cur)
  return {
    key: it.track_id ?? it.event_id ?? `${nameA}-${nameB}-${index}`,
    names: `${nameA} ⇔ ${nameB}`,
    eventId: it.event_id ?? null,
    eventClass: it.event_class ?? null,
    isDemo: Boolean(it.is_demo),
    hasPrevious: Boolean(prev),
    hasCurrent: Boolean(cur),
    miss: { old: miss(prev), new: miss(cur) },
    tca: { old: tca(prev), new: tca(cur) },
    tier: { old: tier(prev), new: tier(cur) },
    missTrendKey: MISS_TREND_KEYS[it.miss_distance_trend] || null,
    tcaShift: shift !== null && prev && cur ? shift : null,
    pc: pcOld || pcNew ? { old: pcOld || DASH, new: pcNew || DASH } : null,
    pcRatio: prev && cur ? formatPcRatio(it.pc_ratio) : null,
  }
}

/**
 * deltaViewModel(resp) -> null | { state: 'none', demo, reasonKey, ... } |
 * { state: 'ok', demo, badgeKey, counts, groups, latestRunId, previousRunId, ... }
 */
export function deltaViewModel(resp) {
  if (!resp || typeof resp !== 'object') return null
  const latest = resp.latest_run && typeof resp.latest_run === 'object' ? resp.latest_run : null
  const previous = resp.previous_run && typeof resp.previous_run === 'object' ? resp.previous_run : null
  const demo = resp.domain === 'demo' || latest?.mode === 'demo'
  const base = {
    demo,
    badgeKey: badgeKey(demo, latest?.mode),
    latestRunId: latest?.id ?? null,
    latestStartedAt: formatUtcShort(latest?.started_at),
    previousRunId: previous?.id ?? resp.previous_run_id ?? null,
    previousStartedAt: formatUtcShort(previous?.started_at),
    // Run labels use the stored run mode (LIVE / CACHED / DEMO) as a code.
    latestMode: latest?.mode ? String(latest.mode).toUpperCase() : null,
    previousMode: previous?.mode ? String(previous.mode).toUpperCase() : null,
    // Which runs this comparison excludes, so a gap in run numbers (e.g.
    // DEMO runs between two LIVE runs) is never read as a lost run.
    domainNoteKey: demo ? 'delta.excludedLive' : 'delta.excludedDemo',
    between: Array.isArray(resp.intermediate_runs)
      ? resp.intermediate_runs.filter((r) => r && typeof r === 'object' && r.id != null)
        .map((r) => ({ id: r.id, mode: r.mode ? String(r.mode).toUpperCase() : '?', recorded: r.recorded !== false }))
      : [],
  }
  if (!resp.available) {
    return { ...base, state: 'none', reasonKey: resp.reason === 'no_runs' ? 'delta.noRuns' : 'delta.none' }
  }
  const items = resp.items && typeof resp.items === 'object' ? resp.items : {}
  const counts = {}
  const groups = []
  for (const [k, labelKey] of DELTA_GROUPS) {
    const list = Array.isArray(items[k]) ? items[k].filter((x) => x && typeof x === 'object') : []
    const c = resp.counts && isNum(resp.counts[k]) ? resp.counts[k] : list.length
    counts[k] = c
    groups.push({ key: k, labelKey, count: c, items: list.map(itemOf) })
  }
  return {
    ...base,
    state: 'ok',
    counts,
    groups,
    hasItems: groups.some((g) => g.items.length > 0),
  }
}

/** Auto-show once per latest screening run (after a manual refresh). */
export function shouldAutoShow(resp, lastShownRunId) {
  if (!resp || typeof resp !== 'object') return false
  const id = resp.latest_run?.id
  // Summary-only payload (no run id): it came from a manual refresh, show it.
  if (id == null) return resp.available !== undefined
  return id !== lastShownRunId
}
