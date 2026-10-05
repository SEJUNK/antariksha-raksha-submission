// Pure view models for the risk-driver explanation and the evidence-trace
// strip in the event drawer (no React, no DOM) so node:test can cover them.
//
// No risk algorithm lives here: values (Pc, tier, priority score, criticality
// multiplier, dwell time) are displayed exactly as the backend stored them.
// The only mapping is which existing explanation text applies.

import { BRIEF_LLM, BRIEF_PENDING, briefState } from './briefState.js'

const isNum = (v) => typeof v === 'number' && Number.isFinite(v)

/**
 * riskDriversViewModel(event, provenance) -> three clearly separated groups:
 * collision indicator, asset criticality, priority ordering.
 */
export function riskDriversViewModel(event, provenance) {
  const ev = event || {}
  const risk = provenance?.risk && typeof provenance.risk === 'object' ? provenance.risk : {}
  const eventClass = risk.event_class || ev.event_class || null
  const isCollision = eventClass === 'collision_risk'
  // priority_basis from the backend; older backends omit it, and their
  // priority formula is fixed by event class (risk_score.py).
  const basis = risk.priority_basis || (eventClass === 'proximity_watch' ? 'proximity_dwell' : isCollision ? 'pc_x_criticality' : null)
  const pc = ev.pc_score
  const tier = typeof (risk.tier ?? ev.risk_tier) === 'string' ? (risk.tier ?? ev.risk_tier) : null
  const criticality = risk.criticality_effective ?? risk.criticality ?? null
  const multRaw = risk.criticality_multiplier_effective !== undefined
    ? risk.criticality_multiplier_effective
    : (basis === 'pc_x_criticality' ? risk.criticality_multiplier : null)
  const priority = risk.priority_score ?? ev.priority_score

  return {
    isCollision,
    collision: isCollision
      ? {
        pc: isNum(pc) ? (pc <= 0 ? '≈ 0' : pc.toExponential(2)) : null,
        tier,
        inputsKey: 'drivers.pcInputs',
      }
      : { notComputedKey: 'drivers.noPc', tier },
    criticality: {
      value: criticality ? String(criticality) : null,
      multiplier: isNum(multRaw) ? String(multRaw) : null,
      noteKey: basis === 'proximity_dwell' ? 'drivers.critNoteProx' : 'drivers.critNote',
    },
    priority: {
      value: isNum(priority) ? priority.toFixed(3) : null,
      basisKey: basis === 'proximity_dwell' ? 'drivers.prioProx' : basis === 'pc_x_criticality' ? 'drivers.prioPc' : null,
      dwell: basis === 'proximity_dwell' && isNum(risk.dwell_minutes) ? risk.dwell_minutes.toFixed(1) : null,
    },
  }
}

// Evidence-trace chain. Each step links to the drawer section that already
// shows it (no data is duplicated here). role: physics | ai | human | audit.
export const CHAIN_SECTIONS = {
  summary: 'evt-sec-summary',
  risk: 'evt-sec-risk',
  drivers: 'evt-sec-drivers',
  ai: 'evt-sec-ai',
  provenance: 'evt-sec-provenance',
  human: 'evt-sec-human',
  audit: 'evt-sec-audit',
  evolution: 'evt-sec-evolution',
  basis: 'evt-sec-basis',
}

export function evidenceChain(event) {
  const ev = event || {}
  const isCollision = ev.event_class === 'collision_risk'
  const bs = briefState(ev)
  const aiStatus = bs === BRIEF_PENDING ? 'chain.aiPending' : bs === BRIEF_LLM ? 'chain.aiLlm' : 'chain.aiTemplate'
  const decided = ev.status && ev.status !== 'pending'
  return [
    { id: 'data', key: 'chain.data', role: 'physics', target: CHAIN_SECTIONS.basis },
    { id: 'prop', key: 'chain.prop', role: 'physics', target: CHAIN_SECTIONS.provenance },
    { id: 'screen', key: 'chain.screen', role: 'physics', target: CHAIN_SECTIONS.provenance },
    { id: 'tca', key: 'chain.tca', role: 'physics', target: CHAIN_SECTIONS.summary },
    { id: 'miss', key: 'chain.miss', role: 'physics', target: CHAIN_SECTIONS.summary },
    { id: 'pc', key: 'chain.pc', role: 'physics', target: CHAIN_SECTIONS.risk, statusKey: isCollision ? null : 'chain.notComputed' },
    { id: 'prio', key: 'chain.prio', role: 'physics', target: CHAIN_SECTIONS.drivers },
    { id: 'ai', key: 'chain.ai', role: 'ai', target: CHAIN_SECTIONS.ai, statusKey: aiStatus, tipKey: 'chain.aiTip' },
    // The operator decision is separate from the AI explanation.
    { id: 'human', key: 'chain.human', role: 'human', target: CHAIN_SECTIONS.human, statusKey: decided ? 'chain.decided' : 'chain.awaiting', tipKey: 'chain.humanTip' },
    { id: 'audit', key: 'chain.audit', role: 'audit', target: CHAIN_SECTIONS.audit },
  ]
}

export const CHAIN_ROLE_KEYS = {
  physics: 'chain.role.physics',
  ai: 'chain.role.ai',
  human: 'chain.role.human',
  audit: 'chain.role.audit',
}
