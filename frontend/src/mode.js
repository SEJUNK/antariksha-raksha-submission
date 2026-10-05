// Shared data-mode helpers. The jury must never have to guess whether they
// are looking at LIVE, CACHED/STALE, or DEMO data -- so every mode label in
// the UI is derived from /api/status `mode` here, in one place. "LIVE" is
// shown ONLY when the backend explicitly reports mode === 'live'; a missing
// or unknown mode (older backend, failed poll) never reads as live.
// `key` is the i18n key for the label (frontend/src/i18n); `chip`/`label`
// stay as the English source strings.

const MODES = {
  live: { chip: '● LIVE DATA', label: 'LIVE DATA', key: 'mode.live', symbol: '●', color: 'var(--safe)' },
  cached: { chip: '● CACHED DATA', label: 'CACHED DATA', key: 'mode.cached', symbol: '●', color: 'var(--warn)' },
  stale: { chip: '⚠ STALE DATA', label: 'STALE DATA', key: 'mode.stale', symbol: '⚠', color: 'var(--warn)' },
  demo: { chip: '◆ DEMO MODE', label: 'DEMO MODE', key: 'mode.demo', symbol: '◆', color: 'var(--warn)', demo: true },
  not_screened: { chip: 'NOT SCREENED', label: 'NOT SCREENED', key: 'mode.not_screened', symbol: '', color: 'var(--text-secondary)' },
}

const UNKNOWN = { chip: 'DATA MODE UNKNOWN', label: 'DATA MODE UNKNOWN', key: 'mode.unknown', symbol: '', color: 'var(--text-secondary)' }

export function modeInfo(status) {
  const mode = status?.mode
  const info = (mode && MODES[mode]) || UNKNOWN
  return { mode: mode || null, ...info }
}

// Localized chip text: symbol + translated label. `t` is an i18n translate
// function; without one the English chip is returned unchanged.
export function modeChipText(info, t) {
  if (typeof t !== 'function' || !info?.key) return info?.chip ?? ''
  return `${info.symbol ? `${info.symbol} ` : ''}${t(info.key)}`
}

// Local time with timezone abbreviation, plus UTC, e.g.
// "2 Oct 2026, 14:05:11 IST · 08:35:11 UTC".
export function formatLocalAndUtc(iso) {
  if (!iso) return 'N/A'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return String(iso)
  let local
  try {
    local = d.toLocaleString(undefined, {
      day: 'numeric', month: 'short', year: 'numeric',
      hour: '2-digit', minute: '2-digit', second: '2-digit',
      hour12: false, timeZoneName: 'short',
    })
  } catch {
    local = d.toString()
  }
  return `${local} · ${d.toISOString().slice(11, 19)} UTC`
}

// Last successful refresh as two parts: a UTC line ("3 Oct 2026 · 10:47:01 UTC")
// and the browser-local time ("16:17:01 GMT+5:30"). null parts when absent.
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
export function formatRefreshParts(iso) {
  if (!iso) return { utc: null, local: null }
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return { utc: String(iso), local: null }
  const utc = `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()} · ${d.toISOString().slice(11, 19)} UTC`
  let local = null
  try {
    local = d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false, timeZoneName: 'short' })
  } catch { /* no Intl: UTC line only */ }
  return { utc, local }
}

export function formatUtc(iso) {
  if (!iso) return 'N/A'
  return String(iso).slice(0, 19).replace('T', ' ') + ' UTC'
}

// Screening horizon of the LATEST screening run, from /api/status
// (screening_window_hours / screening_step_seconds), falling back to
// latest_run.window_hours / step_seconds. Never a hard-coded default: when
// the backend reports nothing the row reads N/A.
const finiteNum = (v) => (typeof v === 'number' && Number.isFinite(v) ? v : null)
export function screeningHorizon(status) {
  const s = status && typeof status === 'object' ? status : {}
  const run = s.latest_run && typeof s.latest_run === 'object' ? s.latest_run : {}
  const hours = finiteNum(s.screening_window_hours) ?? finiteNum(run.window_hours)
  const step = finiteNum(s.screening_step_seconds) ?? finiteNum(run.step_seconds)
  const configuredHours = finiteNum(s.configured_window_hours)
  const configuredStep = finiteNum(s.configured_step_seconds)
  return {
    hours,
    step,
    source: typeof s.screening_horizon_source === 'string' ? s.screening_horizon_source : null,
    // The configured value differs from what the latest run actually used
    // (e.g. config changed since): shown as a secondary note.
    configuredDiffers: (configuredHours != null && hours != null && configuredHours !== hours)
      || (configuredStep != null && step != null && configuredStep !== step),
    configuredHours,
    configuredStep,
    // Active configured horizon when it differs from the latest run's (the
    // NEXT screening run uses it): "Next run uses {h} h". null otherwise.
    nextRunHours: configuredHours != null && hours != null && configuredHours !== hours ? configuredHours : null,
  }
}
