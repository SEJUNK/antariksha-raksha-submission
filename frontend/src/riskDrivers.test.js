import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { CHAIN_ROLE_KEYS, evidenceChain, riskDriversViewModel } from './riskDrivers.js'
import { LANGUAGE_CODES } from './i18n/core.js'

const here = dirname(fileURLToPath(import.meta.url))
const DICTS = Object.fromEntries(
  LANGUAGE_CODES.map((c) => [c, JSON.parse(readFileSync(join(here, 'i18n', `${c}.json`), 'utf8'))]),
)

const collision = { event_class: 'collision_risk', pc_score: 1.234e-4, risk_tier: 'Critical', priority_score: 246.8, generated_by: 'llm', brief_text: 'x', status: 'pending' }
const proximity = { event_class: 'proximity_watch', pc_score: null, risk_tier: 'High', priority_score: 0.5, generated_by: null, brief_text: null, status: 'acknowledged' }

test('drivers: collision separates indicator, criticality and priority (values as given)', () => {
  const vm = riskDriversViewModel(collision, {
    risk: { tier: 'Critical', criticality: 'Tier1', criticality_multiplier: 2, priority_score: 246.8, priority_basis: 'pc_x_criticality', event_class: 'collision_risk' },
  })
  assert.equal(vm.isCollision, true)
  assert.equal(vm.collision.pc, '1.23e-4')
  assert.equal(vm.collision.tier, 'Critical')
  assert.equal(vm.criticality.value, 'Tier1')
  assert.equal(vm.criticality.multiplier, '2')
  assert.equal(vm.criticality.noteKey, 'drivers.critNote')
  assert.equal(vm.priority.value, '246.800')
  assert.equal(vm.priority.basisKey, 'drivers.prioPc')
  assert.equal(vm.priority.dwell, null)
})

test('drivers: proximity -> Pc not computed, dwell-based priority, no criticality multiplier', () => {
  const vm = riskDriversViewModel(proximity, {
    risk: { tier: 'High', criticality: 'Tier1', criticality_multiplier: 2, criticality_effective: null, criticality_multiplier_effective: null, priority_basis: 'proximity_dwell', dwell_minutes: 212.5, priority_score: 0.5 },
  })
  assert.equal(vm.isCollision, false)
  assert.equal(vm.collision.notComputedKey, 'drivers.noPc')
  assert.equal(vm.criticality.multiplier, null)
  assert.equal(vm.criticality.noteKey, 'drivers.critNoteProx')
  assert.equal(vm.priority.basisKey, 'drivers.prioProx')
  assert.equal(vm.priority.dwell, '212.5')
})

test('drivers: older backend without priority_basis maps by event class; missing values hidden', () => {
  const prox = riskDriversViewModel(proximity, { risk: { criticality: 'Tier2', criticality_multiplier: 1.5 } })
  assert.equal(prox.priority.basisKey, 'drivers.prioProx')
  assert.equal(prox.criticality.multiplier, null) // multiplier is not part of proximity priority
  const col = riskDriversViewModel({ event_class: 'collision_risk' }, null)
  assert.equal(col.collision.pc, null)
  assert.equal(col.priority.value, null)
  assert.equal(col.criticality.value, null)
  assert.equal(col.priority.basisKey, 'drivers.prioPc')
  assert.doesNotThrow(() => riskDriversViewModel(undefined, undefined))
})

test('drivers: criticality text states it does not change Pc (every language keeps Pc)', () => {
  for (const c of LANGUAGE_CODES) {
    assert.match(DICTS[c]['drivers.critNote'], /Pc/)
    assert.match(DICTS[c]['drivers.prioPc'], /Pc/)
    assert.match(DICTS[c]['drivers.noPc'], /Pc/)
  }
  assert.match(DICTS.en['drivers.critNote'], /does not change Pc or the risk tier/)
})

test('evidence chain: ten ordered steps with physics / AI / human / audit roles', () => {
  const nodes = evidenceChain(collision)
  assert.deepEqual(nodes.map((n) => n.id), ['data', 'prop', 'screen', 'tca', 'miss', 'pc', 'prio', 'ai', 'human', 'audit'])
  assert.deepEqual([...new Set(nodes.map((n) => n.role))], ['physics', 'ai', 'human', 'audit'])
  assert.equal(nodes.find((n) => n.id === 'pc').statusKey, null)
  assert.equal(nodes.find((n) => n.id === 'ai').statusKey, 'chain.aiLlm')
  assert.equal(nodes.find((n) => n.id === 'ai').tipKey, 'chain.aiTip')
  assert.equal(nodes.find((n) => n.id === 'human').statusKey, 'chain.awaiting')
  for (const n of nodes) assert.ok(n.target.startsWith('evt-sec-'))
})

test('evidence chain: proximity Pc not computed; brief pending / template; decision recorded', () => {
  const p = evidenceChain(proximity)
  assert.equal(p.find((n) => n.id === 'pc').statusKey, 'chain.notComputed')
  assert.equal(p.find((n) => n.id === 'ai').statusKey, 'chain.aiPending')
  assert.equal(p.find((n) => n.id === 'human').statusKey, 'chain.decided')
  const f = evidenceChain({ ...collision, generated_by: 'fallback_template' })
  assert.equal(f.find((n) => n.id === 'ai').statusKey, 'chain.aiTemplate')
  // All chain label keys exist in every language; LLM second pass is advisory.
  for (const c of LANGUAGE_CODES) {
    for (const n of p) assert.ok(DICTS[c][n.key], `${c}:${n.key}`)
    for (const k of Object.values(CHAIN_ROLE_KEYS)) assert.ok(DICTS[c][k], `${c}:${k}`)
    assert.match(DICTS[c]['chain.aiTip'], /LLM/)
    assert.equal(DICTS[c]['chain.tca'], 'TCA')
  }
  assert.match(DICTS.en['chain.aiTip'], /deterministic fact check is authoritative; the LLM second pass is advisory/)
})

test('drivers: exact criticality sentence; proximity priority from band + dwell, no Pc', () => {
  assert.ok(DICTS.en['drivers.critNote'].startsWith('Asset criticality affects priority ordering only.'))
  assert.ok(DICTS.en['drivers.critNoteProx'].startsWith('Asset criticality affects priority ordering only.'))
  assert.match(DICTS.en['drivers.prioProx'], /dwell-time bonus/)
  assert.match(DICTS.en['drivers.noPc'], /Pc not computed/)
  for (const c of LANGUAGE_CODES) assert.match(DICTS[c]['drivers.critNote'], /Pc/)
})

test('evidence chain: AI step explains computed evidence only; human step is separate from the AI', () => {
  const nodes = evidenceChain(collision)
  assert.equal(nodes.find((n) => n.id === 'human').tipKey, 'chain.humanTip')
  const scope = DICTS.en['brief.aiScope']
  assert.match(scope, /^AI explains and summarises backend-computed evidence\./)
  for (const w of ['positions', 'TCA', 'Pc', 'does not validate the physics', 'cannot command']) assert.ok(scope.includes(w), w)
  assert.match(DICTS.en['chain.aiTip'], /does not compute Pc, TCA or positions, does not validate the physics/)
  assert.match(DICTS.en['chain.humanTip'], /separate from the AI/)
  for (const c of LANGUAGE_CODES) {
    assert.match(DICTS[c]['chain.humanTip'], /AI/)
    assert.match(DICTS[c]['brief.aiScope'], /Pc/)
    assert.match(DICTS[c]['brief.aiScope'], /TCA/)
  }
})
