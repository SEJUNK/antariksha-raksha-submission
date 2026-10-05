// i18n coverage for the authentication / governance / horizon / tone-guard strings.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { BRIEF_SOURCE_KEYS, LANGUAGE_CODES, translate } from './core.js'
import { generatedByLabel } from '../briefState.js'
import { ALL_PERMISSIONS, ROLES, permLabelKey, roleLabelKey } from '../auth.js'

const here = dirname(fileURLToPath(import.meta.url))
const DICTS = Object.fromEntries(LANGUAGE_CODES.map((c) => [c, JSON.parse(readFileSync(join(here, `${c}.json`), 'utf8'))]))
const PREFIXES = ['auth.', 'login.', 'role.', 'perm.', 'admin.', 'assets.']
const EXTRA = ['approval.viewOnly', 'hist.legacy', 'hist.actorTip', 'catalog.reviewedBy', 'catalog.notReviewed', 'catalog.reviewScope',
  'asset.windowValue', 'asset.windowConfigured', 'basis.screenHorizon', 'basis.propStep', 'basis.horizonRun', 'basis.horizonConfig',
  'brief.source.toneGuard', 'brief.toneGuardNote', 'ai.status.toneGuard', 'ai.did.toneGuard']
const NEW_KEYS = Object.keys(DICTS.en).filter((k) => PREFIXES.some((p) => k.startsWith(p)) || EXTRA.includes(k))

test('auth/governance keys exist and are translated in all 11 languages with identical placeholders', () => {
  assert.ok(NEW_KEYS.length >= 100, `only ${NEW_KEYS.length}`)
  const vars = (s) => (s.match(/\{\w+\}/g) || []).sort().join(',')
  for (const c of LANGUAGE_CODES) {
    for (const k of NEW_KEYS) {
      assert.ok(typeof DICTS[c][k] === 'string' && DICTS[c][k].trim(), `${c}:${k}`)
      assert.equal(vars(DICTS[c][k]), vars(DICTS.en[k]), `${c}:${k} placeholders`)
    }
  }
  // Every role / permission the UI can show has a label.
  for (const c of LANGUAGE_CODES) {
    for (const r of [...ROLES, 'x']) assert.ok(DICTS[c][roleLabelKey(r)], `${c}:${roleLabelKey(r)}`)
    for (const p of [...ALL_PERMISSIONS, 'x']) assert.ok(DICTS[c][permLabelKey(p)], `${c}:${permLabelKey(p)}`)
  }
})

test('protected terms (AI, Pc, UTC …) stay Latin; role codes and run numbers are data', () => {
  for (const c of LANGUAGE_CODES) {
    for (const k of ['brief.source.toneGuard', 'ai.status.toneGuard']) assert.match(DICTS[c][k], /\bAI\b/, `${c}:${k}`)
    assert.match(translate(DICTS, c, 'basis.horizonRun', { n: 61 }), /#61/)
    assert.ok(translate(DICTS, c, 'approval.viewOnly', { role: 'VIEWER' }).includes('VIEWER'))
    assert.ok(translate(DICTS, c, 'auth.denied', { role: 'VIEWER', perm: 'x' }).includes('VIEWER'))
    const w = translate(DICTS, c, 'asset.windowValue', { h: 24, s: 30 })
    assert.ok(w.includes('24 h') && w.includes('30 s'), `${c}: ${w}`)
    if (c !== 'en') {
      for (const k of ['login.title', 'login.note', 'assets.effective', 'admin.title', 'hist.legacy', 'catalog.reviewScope']) {
        assert.notEqual(DICTS[c][k], DICTS.en[k], `${c}:${k} untranslated`)
      }
    }
  }
})

test('tone-guard source label key matches briefState English label; old mappings unchanged', () => {
  const ev = { generated_by: 'fallback_template', review_status: 'tone_guard_fallback', brief_text: 't' }
  assert.equal(translate(DICTS, 'en', BRIEF_SOURCE_KEYS.tone_guard), generatedByLabel(ev))
  assert.equal(DICTS.en['brief.source.fallback'], 'DETERMINISTIC TEMPLATE (LLM unavailable)')
  assert.equal(DICTS.en['hist.legacy'], 'Legacy / pre-authentication record')
  assert.equal(DICTS.en['approval.viewOnly'], 'Your role ({role}) can view but not decide.')
  assert.equal(DICTS.en['assets.effective'], 'Changes take effect at the next successful refresh.')
  assert.equal(DICTS.en['catalog.reviewScope'], 'Review acknowledgement does not alter classification or protected status.')
  assert.equal(DICTS.en['login.note'], 'Prototype role-based access — local accounts.')
})

test('auth/governance strings: neutral wording, no credentials or username hints on the login screen', () => {
  const banned = /threat|hostile|intent|attack|enemy|adversar/i
  for (const c of LANGUAGE_CODES) for (const k of NEW_KEYS) assert.doesNotMatch(DICTS[c][k], banned, `${c}:${k}`)
  for (const k of Object.keys(DICTS.en).filter((x) => x.startsWith('login.'))) {
    assert.doesNotMatch(DICTS.en[k], /admin|default|demo123|changeme|e\.g\./i, k)
  }
})
