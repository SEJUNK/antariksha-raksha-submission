// Pure i18n logic (no React, no DOM, no network) so node:test can exercise it.
// Static UI strings live in ./<lang>.json and are bundled with the app;
// switching language never makes a network request.
//
// Key naming: dot-namespaced by area, e.g. header.*, mode.*, feed.*, demo.*,
// refresh.*, asset.*, detail.*, chart.*, risk.*, prov.*, brief.*, hist.*,
// approval.*, analytics.*, globe.*, tier.*, criticality.*, objType.*,
// acro.* (acronym explanations), lang.*.
// Values may contain {name} placeholders filled from `vars`. Data values
// (numbers, IDs, timestamps, risk-tier codes) are always passed in as vars and
// are never translated.

export const DEFAULT_LANGUAGE = 'en'
export const STORAGE_KEY = 'antariksha.uiLanguage'

// Order = order in the selector. Native names are shown regardless of the
// current UI language.
export const LANGUAGES = [
  { code: 'en', native: 'English' },
  { code: 'hi', native: 'हिन्दी' },
  { code: 'ta', native: 'தமிழ்' },
  { code: 'te', native: 'తెలుగు' },
  { code: 'mr', native: 'मराठी' },
  { code: 'bn', native: 'বাংলা' },
  { code: 'gu', native: 'ગુજરાતી' },
  { code: 'kn', native: 'ಕನ್ನಡ' },
  { code: 'ml', native: 'മലയാളം' },
  { code: 'pa', native: 'ਪੰਜਾਬੀ' },
  { code: 'or', native: 'ଓଡ଼ିଆ' },
]
export const LANGUAGE_CODES = LANGUAGES.map((l) => l.code)

// Technical terms that must stay in Latin script in every language.
export const PROTECTED_TERMS = ['TLE', 'SGP4', 'TCA', 'Pc', 'NORAD', 'RPO', 'CDM', 'Δv', 'LLM', 'Ollama', 'CelesTrak', 'km', 'm/s', 'UTC']

// English expansions for acronym tooltips (shown in every language, next to
// the localized explanation from the acro.* keys).
export const ACRONYM_EXPANSIONS = {
  TCA: 'Time of Closest Approach',
  Pc: 'Probability of collision',
  TLE: 'Two-Line Element set',
  SGP4: 'Simplified General Perturbations 4 (orbit propagator)',
  NORAD: 'NORAD catalog number (satellite ID)',
  RPO: 'Rendezvous and Proximity Operations',
  CDM: 'Conjunction Data Message',
  'Δv': 'Delta-v (change in velocity)',
  LLM: 'Large Language Model',
  UTC: 'Coordinated Universal Time',
  SSA: 'Space Situational Awareness',
}
const ACRONYM_KEY = { 'Δv': 'dv' }

export function normalizeLanguage(lang) {
  if (typeof lang !== 'string') return DEFAULT_LANGUAGE
  const code = lang.trim().toLowerCase()
  return LANGUAGE_CODES.includes(code) ? code : DEFAULT_LANGUAGE
}

export function languageInfo(lang) {
  const code = normalizeLanguage(lang)
  return LANGUAGES.find((l) => l.code === code)
}

export function interpolate(str, vars) {
  if (!vars) return str
  return str.replace(/\{(\w+)\}/g, (m, name) => (
    Object.prototype.hasOwnProperty.call(vars, name) && vars[name] !== undefined && vars[name] !== null
      ? String(vars[name])
      : m
  ))
}

/**
 * translate(dicts, lang, key, vars): look up `key` in dicts[lang], falling back
 * to English for unknown languages or missing keys, and to the key itself if
 * even English lacks it.
 */
export function translate(dicts, lang, key, vars) {
  const code = normalizeLanguage(lang)
  const local = dicts?.[code]?.[key]
  const english = dicts?.[DEFAULT_LANGUAGE]?.[key]
  const str = typeof local === 'string' && local.length > 0
    ? local
    : (typeof english === 'string' ? english : key)
  return interpolate(str, vars)
}

export function createTranslator(dicts, lang) {
  return (key, vars) => translate(dicts, lang, key, vars)
}

// ---------------------------------------------------------------------------
// Persistence (injectable storage; every access wrapped in try/catch so a
// blocked/private-mode localStorage never breaks the UI).
// ---------------------------------------------------------------------------
export function loadLanguage(storage) {
  try {
    const v = storage?.getItem?.(STORAGE_KEY)
    return normalizeLanguage(v)
  } catch {
    return DEFAULT_LANGUAGE
  }
}

export function saveLanguage(storage, lang) {
  const code = normalizeLanguage(lang)
  try {
    storage?.setItem?.(STORAGE_KEY, code)
    return true
  } catch {
    return false
  }
}

// ---------------------------------------------------------------------------
// Acronym tooltips: "TCA — Time of Closest Approach · <local explanation>".
// The acronym itself is never translated.
// ---------------------------------------------------------------------------
export function acronymTooltip(dicts, lang, code) {
  const expansion = ACRONYM_EXPANSIONS[code]
  if (!expansion) return undefined
  const key = `acro.${ACRONYM_KEY[code] || code}`
  const local = translate(dicts, lang, key)
  return local && local !== key ? `${code} — ${expansion} · ${local}` : `${code} — ${expansion}`
}

const ACRO_RE = new RegExp(
  `(^|[^A-Za-z0-9])(${Object.keys(ACRONYM_EXPANSIONS).map((a) => a.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')})(?![A-Za-z0-9])`,
  'g',
)

/** Tooltip for every known acronym found in `text` (joined by newlines), or undefined. */
export function acronymTooltipsFor(dicts, lang, text) {
  if (typeof text !== 'string') return undefined
  const found = []
  for (const m of text.matchAll(ACRO_RE)) {
    if (!found.includes(m[2])) found.push(m[2])
  }
  if (found.length === 0) return undefined
  return found.map((c) => acronymTooltip(dicts, lang, c)).join('\n')
}

// ---------------------------------------------------------------------------
// Brief-state / review-state label keys. The underlying logic stays in
// ../briefState.js (unchanged); these only map its states to keys whose
// English values are identical to briefState's English labels.
// ---------------------------------------------------------------------------
export const BRIEF_SOURCE_KEYS = {
  llm: 'brief.source.llm',
  fallback: 'brief.source.fallback',
  pending: 'brief.source.pending',
  // briefState.briefSourceKind(): AI draft rejected by the tone guard.
  tone_guard: 'brief.source.toneGuard',
}
export const BRIEF_HEADING_KEYS = {
  llm: 'brief.heading.llm',
  fallback: 'brief.heading.fallback',
  pending: 'brief.heading.pending',
}
export function reviewLabelKey(status) {
  switch (status) {
    case 'consistent': return 'brief.review.consistent'
    case 'flagged': return 'brief.review.flagged'
    case 'skipped': return 'brief.review.skipped'
    default: return null
  }
}

// Risk tier: the code (Critical/High/Medium/Low) is data and shown as-is; the
// translated word is only an auxiliary label.
export function tierLabelKey(tier) {
  return ['Critical', 'High', 'Medium', 'Low'].includes(tier) ? `tier.${tier}` : null
}
