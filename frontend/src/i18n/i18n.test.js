import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { dirname, join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'
import {
  ACRONYM_EXPANSIONS, BRIEF_HEADING_KEYS, BRIEF_SOURCE_KEYS, DEFAULT_LANGUAGE, LANGUAGES, LANGUAGE_CODES,
  STORAGE_KEY, acronymTooltip, acronymTooltipsFor, createTranslator, loadLanguage, normalizeLanguage,
  reviewLabelKey, saveLanguage, tierLabelKey, translate,
} from './core.js'
import { briefDisplay, shouldRequestTranslation } from './briefTranslation.js'
import { briefHeading, generatedByLabel, reviewLabel } from '../briefState.js'
import { formatUtc, modeChipText, modeInfo } from '../mode.js'

const here = dirname(fileURLToPath(import.meta.url))
const DICTS = Object.fromEntries(
  LANGUAGE_CODES.map((c) => [c, JSON.parse(readFileSync(join(here, `${c}.json`), 'utf8'))]),
)
const INDIC = LANGUAGE_CODES.filter((c) => c !== 'en')

function memoryStorage(initial = {}) {
  const data = { ...initial }
  return {
    getItem: (k) => (k in data ? data[k] : null),
    setItem: (k, v) => { data[k] = String(v) },
    data,
  }
}

test('11 languages with native names; default is English', () => {
  assert.equal(LANGUAGES.length, 11)
  assert.deepEqual(LANGUAGE_CODES, ['en', 'hi', 'ta', 'te', 'mr', 'bn', 'gu', 'kn', 'ml', 'pa', 'or'])
  assert.equal(DEFAULT_LANGUAGE, 'en')
  assert.equal(LANGUAGES.find((l) => l.code === 'hi').native, 'हिन्दी')
  assert.equal(LANGUAGES.find((l) => l.code === 'or').native, 'ଓଡ଼ିଆ')
  assert.equal(loadLanguage(memoryStorage()), 'en')
  assert.equal(translate(DICTS, undefined, 'feed.title'), 'Event Feed')
})

test('Hindi and Tamil change labels', () => {
  assert.equal(translate(DICTS, 'hi', 'feed.title'), 'घटना फ़ीड')
  assert.equal(translate(DICTS, 'ta', 'feed.title'), 'நிகழ்வு ஊட்டம்')
  assert.notEqual(translate(DICTS, 'hi', 'approval.approve'), translate(DICTS, 'en', 'approval.approve'))
  assert.notEqual(translate(DICTS, 'ta', 'detail.s2'), translate(DICTS, 'en', 'detail.s2'))
  const t = createTranslator(DICTS, 'hi')
  assert.equal(t('lang.status', { name: 'हिन्दी' }), 'भाषा: हिन्दी · लोकल')
})

test('persistence load/save via injectable storage', () => {
  const s = memoryStorage()
  assert.equal(saveLanguage(s, 'ta'), true)
  assert.equal(s.data[STORAGE_KEY], 'ta')
  assert.equal(loadLanguage(s), 'ta')
  saveLanguage(s, 'klingon')
  assert.equal(loadLanguage(s), 'en')
  // Throwing / missing storage never breaks anything.
  const broken = { getItem: () => { throw new Error('blocked') }, setItem: () => { throw new Error('blocked') } }
  assert.equal(loadLanguage(broken), 'en')
  assert.equal(saveLanguage(broken, 'hi'), false)
  assert.equal(loadLanguage(null), 'en')
  assert.equal(loadLanguage(memoryStorage({ [STORAGE_KEY]: 'garbage' })), 'en')
})

test('unknown language falls back to English', () => {
  assert.equal(normalizeLanguage('xx'), 'en')
  assert.equal(normalizeLanguage(42), 'en')
  assert.equal(normalizeLanguage(' HI '), 'hi')
  assert.equal(translate(DICTS, 'fr', 'feed.title'), 'Event Feed')
})

test('missing key falls back to English, then to the key', () => {
  const dicts = { en: { 'a.b': 'Hello {name}' }, hi: {} }
  assert.equal(translate(dicts, 'hi', 'a.b', { name: 'X' }), 'Hello X')
  assert.equal(translate(dicts, 'hi', 'no.such.key'), 'no.such.key')
})

test('all language files have exactly the same key set as en.json', () => {
  const enKeys = Object.keys(DICTS.en).sort()
  for (const c of INDIC) {
    const keys = Object.keys(DICTS[c]).sort()
    const missing = enKeys.filter((k) => !keys.includes(k))
    const extra = keys.filter((k) => !enKeys.includes(k))
    assert.deepEqual({ c, missing, extra }, { c, missing: [], extra: [] })
    for (const k of keys) assert.ok(typeof DICTS[c][k] === 'string' && DICTS[c][k].trim(), `${c}:${k} empty`)
  }
})

test('placeholders ({vars}) are identical in every language', () => {
  const vars = (s) => (s.match(/\{\w+\}/g) || []).sort().join(',')
  for (const [k, v] of Object.entries(DICTS.en)) {
    for (const c of INDIC) assert.equal(vars(DICTS[c][k]), vars(v), `${c}:${k}`)
  }
})

test('acronyms / technical terms stay in Latin script in every language', () => {
  const terms = ['TLE', 'SGP4', 'TCA', 'Pc', 'NORAD', 'RPO', 'CDM', 'Δv', 'LLM', 'Ollama', 'CelesTrak', 'UTC', 'SSA', 'km', 'm/s', 'Monte Carlo']
  for (const [k, v] of Object.entries(DICTS.en)) {
    for (const term of terms) {
      const re = new RegExp(`(^|[^A-Za-z])${term.replace(/[.*+?^${}()|[\]\\/]/g, '\\$&')}($|[^A-Za-z])`)
      if (!re.test(v)) continue
      for (const c of INDIC) {
        // Upper-case English labels sometimes write KM; translations use km.
        assert.ok(re.test(DICTS[c][k]) || (term === 'km' && /KM/.test(v)), `${c}:${k} lost "${term}"`)
      }
    }
  }
  // Selected keys explicitly.
  for (const c of LANGUAGE_CODES) {
    assert.match(translate(DICTS, c, 'detail.tca'), /TCA \(UTC\)/)
    assert.match(translate(DICTS, c, 'risk.pcTitle'), /\(Pc\)/)
    assert.match(translate(DICTS, c, 'globe.caption'), /SGP4/)
    assert.match(translate(DICTS, c, 'brief.dvTitle'), /Δv/)
    assert.match(translate(DICTS, c, 'brief.source.llm'), /LLM/)
    assert.match(translate(DICTS, c, 'refresh.note'), /CelesTrak/)
  }
})

test('risk tier codes in threshold text are never translated', () => {
  for (const c of LANGUAGE_CODES) {
    assert.match(translate(DICTS, c, 'risk.tierFromPc'), /Critical >1e-4, High >1e-5, Medium >1e-6/)
  }
  assert.equal(tierLabelKey('Critical'), 'tier.Critical')
  assert.equal(tierLabelKey('Bogus'), null)
})

test('numbers, IDs, timestamps and tier values passed as vars are unchanged in every language', () => {
  const vars = { tca: '2026-07-21 04:12 UTC', miss: '0.41', count: 3, tier: 'CRITICAL', n: '4,329', v: '0.000231', id: 44804, t: '04:12:00 UTC' }
  const checks = [
    ['feed.rowMeta', ['2026-07-21 04:12 UTC', '0.41']],
    ['header.eventsMany', ['3', 'CRITICAL']],
    ['header.eventsOne', ['CRITICAL']],
    ['risk.odds', ['4,329']],
    ['risk.priority', ['0.000231']],
    ['prov.derived', ['44804']],
    ['approval.logged', ['04:12:00 UTC']],
  ]
  for (const c of LANGUAGE_CODES) {
    for (const [key, expected] of checks) {
      const out = translate(DICTS, c, key, vars)
      for (const e of expected) assert.ok(out.includes(e), `${c}:${key} -> ${out}`)
    }
  }
  // Formatting helpers are language-independent.
  assert.equal(formatUtc('2026-07-21T04:12:00+00:00'), '2026-07-21 04:12:00 UTC')
})

test('demo / live mode label keys exist in every language and stay correct', () => {
  for (const c of LANGUAGE_CODES) {
    for (const k of ['mode.live', 'mode.cached', 'mode.stale', 'mode.demo', 'mode.not_screened', 'mode.unknown',
      'detail.demoTitle', 'detail.demoBanner', 'common.demo', 'header.demoSuffix', 'feed.demoTip', 'prov.public']) {
      assert.ok(DICTS[c][k], `${c} missing ${k}`)
    }
    // The demo chip never reads as the live label, and vice versa.
    assert.notEqual(DICTS[c]['mode.demo'], DICTS[c]['mode.live'])
  }
  // modeInfo still never reports live unless backend says so; chip text localizes only the label.
  assert.equal(modeInfo({}).key, 'mode.unknown')
  assert.equal(modeInfo({ mode: 'demo' }).key, 'mode.demo')
  assert.equal(modeChipText(modeInfo({ mode: 'demo' }), createTranslator(DICTS, 'hi')), '◆ डेमो मोड')
  assert.equal(modeChipText(modeInfo({ mode: 'live' }), createTranslator(DICTS, 'en')), '● LIVE DATA')
  assert.equal(modeChipText(modeInfo({ mode: 'live' })), '● LIVE DATA')
})

test('brief / review label keys match briefState English labels exactly', () => {
  const cases = [
    [{ generated_by: null, brief_text: null }, 'pending'],
    [{ generated_by: 'llm', brief_text: 'x' }, 'llm'],
    [{ generated_by: 'fallback_template', brief_text: 'x' }, 'fallback'],
  ]
  for (const [ev, state] of cases) {
    assert.equal(translate(DICTS, 'en', BRIEF_SOURCE_KEYS[state]), generatedByLabel(ev))
    assert.equal(translate(DICTS, 'en', BRIEF_HEADING_KEYS[state]), briefHeading(ev))
  }
  assert.equal(translate(DICTS, 'en', BRIEF_SOURCE_KEYS.pending), 'BRIEF PENDING — LLM generation in progress')
  for (const s of ['consistent', 'flagged', 'skipped']) {
    assert.equal(translate(DICTS, 'en', reviewLabelKey(s)), reviewLabel(s))
  }
  assert.equal(reviewLabelKey(null), null)
  assert.equal(reviewLabelKey('approved'), null)
  // No language ever claims "approved" for a brief merely existing.
  for (const c of LANGUAGE_CODES) {
    for (const k of Object.values(BRIEF_SOURCE_KEYS)) {
      assert.doesNotMatch(translate(DICTS, c, k), /approved/i)
      assert.notEqual(translate(DICTS, c, k), translate(DICTS, c, 'approval.approved'))
    }
  }
})

test('acronym tooltips are bilingual and keep the acronym', () => {
  const en = acronymTooltip(DICTS, 'en', 'TCA')
  assert.match(en, /^TCA — Time of Closest Approach · /)
  const hi = acronymTooltip(DICTS, 'hi', 'TCA')
  assert.match(hi, /^TCA — Time of Closest Approach · /)
  assert.ok(hi.includes(DICTS.hi['acro.TCA']))
  assert.match(acronymTooltip(DICTS, 'ta', 'Δv'), /^Δv — Delta-v/)
  for (const c of LANGUAGE_CODES) {
    for (const code of Object.keys(ACRONYM_EXPANSIONS)) {
      assert.ok(acronymTooltip(DICTS, c, code).startsWith(`${code} — `))
    }
  }
  const multi = acronymTooltipsFor(DICTS, 'te', 'MISS DISTANCE (AT TCA) · Pc')
  assert.match(multi, /TCA — /)
  assert.match(multi, /Pc — /)
  assert.equal(acronymTooltipsFor(DICTS, 'en', 'Event Feed'), undefined)
})

test('dynamic translation: only requested for non-English with a brief', () => {
  const ev = { id: 1, brief_text: 'Miss 0.41 km at TCA.', maneuver_text: 'Δv 0.25 m/s' }
  assert.equal(shouldRequestTranslation('en', ev, false), false)
  assert.equal(shouldRequestTranslation('hi', ev, true), false)
  assert.equal(shouldRequestTranslation('hi', { brief_text: null }, false), false)
  assert.equal(shouldRequestTranslation('hi', ev, false), true)
})

test('dynamic translation disabled / failed -> English fallback with notice', () => {
  const ev = { brief_text: 'Miss 0.41 km at TCA.', maneuver_text: 'Δv 0.25 m/s' }
  const english = { brief_text: ev.brief_text, maneuver_text: ev.maneuver_text }
  assert.deepEqual(briefDisplay({ lang: 'en', event: ev, translation: null }),
    { briefText: ev.brief_text, maneuverText: ev.maneuver_text, mode: 'english', noticeKey: null })
  assert.equal(briefDisplay({ lang: 'hi', event: ev, translation: { loading: true } }).briefText, ev.brief_text)
  for (const tr of [
    { status: 'unavailable', translated: false, target_language: 'hi', brief_text: ev.brief_text, english },
    { status: 'failed', translated: false, target_language: 'hi', brief_text: ev.brief_text, english },
    { error: 'network' },
    // Translated, but for a different (stale) English text -> must not be shown.
    { status: 'translated', translated: true, target_language: 'hi', brief_text: 'अनुवाद', english: { brief_text: 'old', maneuver_text: ev.maneuver_text } },
    // Translated for another language.
    { status: 'translated', translated: true, target_language: 'ta', brief_text: 'மொழி', english },
  ]) {
    const d = briefDisplay({ lang: 'hi', event: ev, translation: tr })
    assert.equal(d.mode, 'fallback')
    assert.equal(d.briefText, ev.brief_text)
    assert.equal(d.maneuverText, ev.maneuver_text)
    assert.equal(d.noticeKey, 'brief.translationUnavailable')
  }
  for (const c of LANGUAGE_CODES) assert.ok(DICTS[c]['brief.translationUnavailable'])
})

test('dynamic translation success shows labelled local translation with English toggle', () => {
  const ev = { brief_text: 'Miss 0.41 km at TCA.', maneuver_text: 'Δv 0.25 m/s' }
  const tr = {
    status: 'translated', translated: true, target_language: 'hi',
    brief_text: 'TCA पर दूरी 0.41 km।', maneuver_text: 'Δv 0.25 m/s',
    english: { brief_text: ev.brief_text, maneuver_text: ev.maneuver_text },
  }
  const d = briefDisplay({ lang: 'hi', event: ev, translation: tr })
  assert.equal(d.mode, 'translated')
  assert.equal(d.noticeKey, 'brief.mtLabel')
  assert.equal(d.briefText, 'TCA पर दूरी 0.41 km।')
  const orig = briefDisplay({ lang: 'hi', event: ev, translation: tr, showOriginal: true })
  assert.equal(orig.briefText, ev.brief_text)
  assert.equal(orig.mode, 'english')
})

test('native-script product name: nothing extra in English, local script elsewhere', () => {
  assert.equal(translate(DICTS, 'en', 'header.brandLocal'), '')
  const expected = {
    hi: 'अंतरिक्ष रक्षा', mr: 'अंतरिक्ष रक्षा', gu: 'અંતરિક્ષ રક્ષા', ta: 'அந்தரிக்ஷ ரக்ஷா', te: 'అంతరిక్ష రక్ష',
    bn: 'অন্তরীক্ষ রক্ষা', kn: 'ಅಂತರಿಕ್ಷ ರಕ್ಷಾ', ml: 'അന്തരിക്ഷ രക്ഷ', pa: 'ਅੰਤਰਿਕਸ਼ ਰਕਸ਼ਾ', or: 'ଅନ୍ତରୀକ୍ଷ ରକ୍ଷା',
  }
  for (const c of INDIC) {
    assert.equal(translate(DICTS, c, 'header.brandLocal'), expected[c])
    assert.doesNotMatch(translate(DICTS, c, 'header.brandLocal'), /[A-Za-z]/)
  }
})

test('scientific limitations are translated keys and keep technical terms', () => {
  for (let i = 1; i <= 7; i++) {
    for (const c of LANGUAGE_CODES) assert.ok(DICTS[c][`limit.${i}`], `${c}:limit.${i}`)
  }
  for (const c of LANGUAGE_CODES) {
    assert.match(DICTS[c]['limit.1'], /SGP4/)
    assert.match(DICTS[c]['limit.1'], /TLE\/GP/)
    assert.match(DICTS[c]['limit.3'], /Pc/)
    assert.match(DICTS[c]['limit.4'], /10 km/)
    assert.match(DICTS[c]['limit.5'], /Δv/)
  }
})

// ---------------------------------------------------------------------------
// No external endpoint anywhere in the frontend source.
// ---------------------------------------------------------------------------
function walk(dir) {
  const out = []
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) out.push(...walk(p))
    else if (/\.(js|jsx|json|css|ts|tsx)$/.test(name)) out.push(p)
  }
  return out
}

test('no external endpoint / translation host in frontend/src', () => {
  const srcDir = join(here, '..')
  const self = fileURLToPath(import.meta.url)
  const hosts = ['translate.googleapis', 'translate.google', 'api.cognitive.microsofttranslator', 'microsofttranslator',
    'translate.amazonaws', 'bhashini', 'fonts.googleapis', 'fonts.gstatic', 'deepl.com', 'libretranslate']
  const offenders = []
  for (const file of walk(srcDir)) {
    // Shipped source only: unit-test files carry mocked fixture URLs (e.g.
    // the deployment proxy tests) that never reach the bundle.
    if (file === self || /\.test\.jsx?$/.test(file)) continue
    const text = readFileSync(file, 'utf8')
    for (const m of text.matchAll(/https?:\/\/[^\s'"`)]+/g)) {
      if (!/^https?:\/\/(localhost|127\.0\.0\.1)(:\d+)?(\/|$)/.test(m[0])) offenders.push(`${relative(srcDir, file)}: ${m[0]}`)
    }
    for (const h of hosts) if (text.toLowerCase().includes(h)) offenders.push(`${relative(srcDir, file)}: ${h}`)
  }
  assert.deepEqual(offenders, [])
})

// ---------------------------------------------------------------------------
// Event evolution / change summary / risk drivers / evidence trace strings.
// ---------------------------------------------------------------------------
const NEW_PREFIXES = ['evo.', 'delta.', 'drivers.', 'chain.', 'ai.', 'basis.', 'analyst.', 'timeline.', 'catalog.']

test('evolution / delta / drivers / chain keys exist in every language', () => {
  const keys = Object.keys(DICTS.en).filter((k) => NEW_PREFIXES.some((p) => k.startsWith(p)))
  assert.ok(keys.length >= 70, `only ${keys.length} new keys`)
  for (const c of LANGUAGE_CODES) for (const k of keys) assert.ok(DICTS[c][k] && DICTS[c][k].trim(), `${c}:${k}`)
  assert.equal(DICTS.en['evo.single'], 'Only one screening observation is available.')
  assert.equal(DICTS.en['delta.none'], 'No previous screening run available.')
  assert.equal(DICTS.en['delta.notPresent'], 'Not present in the latest screening run')
  for (const c of LANGUAGE_CODES) {
    assert.match(DICTS[c]['evo.colTca'], /TCA \(UTC\)/)
    assert.match(DICTS[c]['evo.colPc'], /^Pc$/)
    assert.match(DICTS[c]['evo.colMiss'], /\(km\)/)
    assert.match(DICTS[c]['evo.colRelVel'], /\(km\/s\)/)
    assert.match(DICTS[c]['delta.tca'], /^TCA \{old\} → \{new\}$/)
  }
})

test('new strings use neutral wording only (no threat / hostile / intent / attack)', () => {
  const banned = /threat|hostile|intent|attack|enemy|adversar/i
  for (const c of LANGUAGE_CODES) {
    for (const [k, v] of Object.entries(DICTS[c])) {
      if (!NEW_PREFIXES.some((p) => k.startsWith(p))) continue
      assert.doesNotMatch(v, banned, `${c}:${k}`)
    }
  }
})

test('delta / evolution values passed as vars are unchanged in every language', () => {
  for (const c of LANGUAGE_CODES) {
    const miss = translate(DICTS, c, 'delta.miss', { old: '0.840', new: '0.410' })
    assert.ok(miss.includes('0.840') && miss.includes('0.410') && miss.includes('km'), `${c}: ${miss}`)
    const vs = translate(DICTS, c, 'delta.vs', { latest: 12, previous: 11 })
    assert.ok(vs.includes('#12') && vs.includes('#11'), `${c}: ${vs}`)
    const run = translate(DICTS, c, 'evo.run', { id: 10, time: '2026-10-01 06:00:00 UTC' })
    assert.ok(run.includes('#10') && run.includes('2026-10-01 06:00:00 UTC'), `${c}: ${run}`)
    const pc = translate(DICTS, c, 'drivers.pcValue', { v: '1.23e-4', tier: 'Critical' })
    assert.ok(pc.includes('1.23e-4') && pc.includes('Critical') && pc.includes('Pc'), `${c}: ${pc}`)
  }
})

test('new delta / evolution / demo-history / chain strings exist in every language with protected terms', () => {
  const keys = ['delta.more', 'delta.pcRatioTip', 'evo.pcChange', 'evo.demoHistory', 'evo.demoStep', 'chain.humanTip',
    'demo.historyHeading', 'demo.historyStep1', 'demo.historyStep2', 'demo.historyTip']
  for (const c of LANGUAGE_CODES) {
    for (const k of keys) assert.ok(DICTS[c][k] && DICTS[c][k].trim(), `${c}:${k}`)
    assert.match(DICTS[c]['evo.pcChange'], /\(Pc\): \{old\} → \{new\}$/)
    assert.match(DICTS[c]['delta.pcRatioTip'], /Pc/)
    assert.match(DICTS[c]['demo.historyStep1'], /1/)
    assert.match(DICTS[c]['demo.historyStep2'], /2/)
    if (c !== 'en') {
      for (const k of keys) assert.notEqual(DICTS[c][k], DICTS.en[k], `${c}:${k} untranslated`)
    }
  }
  // Demo-history wording is neutral and disclosed.
  assert.match(DICTS.en['demo.historyTip'], /Not an operational warning/)
})

test('backlog strings: AI boundary, assessment basis, analyst summary, timeline and catalog exist in every language', () => {
  const required = [
    'ai.statement', 'ai.status.available', 'ai.status.unavailable', 'ai.status.checkFailed', 'ai.didNot.pc', 'ai.didNot.decision',
    'header.aiAvailable', 'header.aiOffline', 'basis.title', 'basis.cov.radar', 'basis.lim.notPc', 'analyst.title', 'analyst.whyPc',
    'timeline.firstObserved', 'catalog.registryNote', 'catalog.newNote', 'catalog.reviewTip',
  ]
  for (const c of LANGUAGE_CODES) {
    for (const k of required) assert.ok(typeof DICTS[c][k] === 'string' && DICTS[c][k].length > 0, `${c}:${k}`)
    // AI never calculates the physics; Pc stays a protected term.
    assert.match(DICTS[c]['ai.didNot.pc'], /Pc/)
    assert.match(DICTS[c]['basis.lim.notPc'], /Pc/)
    assert.match(DICTS[c]['basis.cov.catalog'], /CelesTrak/)
  }
  // English wording of the required boundary statement and AI states.
  assert.equal(DICTS.en['ai.statement'], 'Physics evidence is generated by the deterministic backend. AI explains that evidence.')
  assert.equal(DICTS.en['ai.status.unavailable'], 'AI UNAVAILABLE — deterministic assessment remains available')
  assert.equal(DICTS.en['ai.status.checkFailed'], 'AI CHECK FAILED — backend evidence remains authoritative')
  // No invented confidence score anywhere in the new strings.
  for (const [k, v] of Object.entries(DICTS.en)) {
    if (['basis.', 'analyst.', 'ai.'].some((p) => k.startsWith(p))) assert.doesNotMatch(v, /\d+\s?%|confidence score/i, k)
  }
})

test('final UX strings exist in every language; LIVE/DEMO and NORAD kept; refresh label says successful', () => {
  const keys = ['asset.lastRefresh', 'asset.lastFailed', 'delta.currentRun', 'delta.previousRun', 'delta.excludedDemo',
    'delta.excludedLive', 'delta.between', 'catalog.protectedHeading', 'catalog.configured', 'catalog.search', 'catalog.filter.other']
  for (const c of LANGUAGE_CODES) {
    for (const k of keys) assert.ok(DICTS[c][k], `${c}:${k}`)
    assert.match(DICTS[c]['delta.excludedDemo'], /LIVE/)
    assert.match(DICTS[c]['delta.excludedLive'], /DEMO/)
    assert.match(DICTS[c]['catalog.search'], /NORAD/)
  }
  assert.equal(DICTS.en['asset.lastRefresh'], 'LAST SUCCESSFUL REFRESH')
  assert.doesNotMatch(DICTS.en['delta.betweenTip'], /lost(?!\.)|resolved|cleared/i)
})
