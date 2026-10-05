import { useCallback, useEffect, useRef, useState } from 'react'
import ApprovalPanel from './ApprovalPanel'
import { api } from '../api'
import { formatUtc } from '../mode'
import { BRIEF_LLM, BRIEF_PENDING, briefSourceKind, briefSourceText, briefState, isToneGuardFallback, reviewLabel } from '../briefState'
import { useI18n } from '../i18n'
import { BRIEF_HEADING_KEYS, BRIEF_SOURCE_KEYS, reviewLabelKey, tierLabelKey } from '../i18n/core'
import { briefDisplay, shouldRequestTranslation } from '../i18n/briefTranslation'
import { useUiScale } from '../uiScaleContext'
import { aiBoundary, analystSummary, assessmentBasis, eventTimeline, horizonNote } from '../eventTrust'
import { decisionLine } from '../auth'
import { downloadReportDocx, parseReport, printReportPdf, reportFilename } from '../reportExport'
import EventEvolution from './EventEvolution'
import { evolutionViewModel } from '../eventEvolution'
import { CHAIN_ROLE_KEYS, CHAIN_SECTIONS, evidenceChain, riskDriversViewModel } from '../riskDrivers'

// ---------------------------------------------------------------------------
// Small defensive helpers -- every backend field below may be missing on an
// older backend; nothing here may throw on undefined.
// ---------------------------------------------------------------------------
const isNum = (v) => typeof v === 'number' && Number.isFinite(v)
// Odds ("1 in N") are only shown down to this Pc; below it N is astronomically large.
const MIN_ODDS_PC = 1e-12

// English labels for the exported (English source-of-record) report; the UI
// uses the objType.* i18n keys.
const OBJECT_TYPE_LABEL = {
  satellite: 'Indian satellite',
  debris: 'Debris',
  foreign_sat: 'Other active satellite',
}
const objTypeText = (t, type) => (OBJECT_TYPE_LABEL[type] ? t(`objType.${type}`) : null)

function renderValue(v) {
  if (v === null || v === undefined || v === '') return 'N/A'
  if (typeof v === 'boolean') return v ? 'yes' : 'no'
  if (Array.isArray(v)) return v.map(renderValue).join(', ')
  if (typeof v === 'object') {
    return Object.entries(v).map(([k, val]) => `${k.replace(/_/g, ' ')}: ${renderValue(val)}`).join(' · ')
  }
  return String(v)
}

// samples is only set for legacy Monte Carlo events, where 0 meant "below
// 1/N resolution". The analytic indicator is continuous: 0 is numerical zero.
function formatPcValue(pc, samples) {
  if (!isNum(pc)) return 'N/A'
  if (pc <= 0) return samples ? `< ${(1 / samples).toExponential(1)}` : '≈ 0'
  return pc.toExponential(2)
}

// Inline SVG separation-vs-time chart -- the classic conjunction-analysis
// view: distance collapsing to the miss distance at TCA, then reopening.
function SeparationChart({ profile }) {
  const { t, tip } = useI18n()
  if (!profile || !Array.isArray(profile.sep_km) || profile.sep_km.length < 2) return null
  const W = 260, H = 90, PAD = 6
  const sep = profile.sep_km
  const maxSep = Math.max(...sep)
  const minSep = Math.min(...sep)
  const minIdx = sep.indexOf(minSep)
  const x = (i) => PAD + (i / (sep.length - 1)) * (W - 2 * PAD)
  const y = (v) => H - PAD - ((v - 0) / (maxSep || 1)) * (H - 2 * PAD)
  const points = sep.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ')
  // Near-constant separation = co-orbital geometry (e.g. station-keeping or
  // a seeded along-track offset) rather than a crossing conjunction.
  const isCoOrbital = maxSep > 0 && (maxSep - minSep) / maxSep < 0.05
  return (
    <div style={{ marginBottom: 12 }}>
      <div className="eyebrow" style={{ fontSize: 11, marginBottom: 4 }} title={tip('TCA')}>
        {t('chart.title')}
      </div>
      <svg width="100%" height={H} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none"
        style={{ display: 'block', maxWidth: W, background: 'rgba(11,16,32,0.5)', borderRadius: 6 }}>
        <line x1={x(minIdx)} y1={PAD} x2={x(minIdx)} y2={H - PAD}
          stroke="var(--warn)" strokeWidth="1" strokeDasharray="3,3" opacity="0.7" />
        <polyline points={points} fill="none" stroke="var(--accent)" strokeWidth="1.5" />
        <circle cx={x(minIdx)} cy={y(minSep)} r="3" fill="var(--warn)" />
      </svg>
      <div className="mono" style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, color: 'var(--text-secondary)', marginTop: 3, maxWidth: W }}>
        <span>{t('chart.max', { v: maxSep >= 100 ? Math.round(maxSep) : maxSep.toFixed(1) })}</span>
        <span style={{ color: 'var(--warn)' }}>{t('chart.min', { v: minSep.toFixed(2) })}</span>
      </div>
      {isCoOrbital && (
        <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 3, fontStyle: 'italic' }}>
          {t('chart.coOrbital')}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Static scientific limitations (Section 6). Backend-provided limitations
// (provenance.probability.limitations) are appended without duplicates.
// ---------------------------------------------------------------------------
const STATIC_LIMITATIONS = [
  'Orbits propagated with SGP4 from public TLE/GP element sets — position errors of the order of kilometres are normal and grow with element-set age.',
  'Uncertainty is a simplified isotropic Gaussian, not a covariance from orbit determination.',
  'Pc is a simplified analytic encounter-plane indicator (short-encounter assumption, assumed isotropic σ) — not an operational covariance-based Pc.',
  'The 10 km distance is a prototype candidate-screening threshold, not a collision criterion.',
  'The Δv figure is an illustrative estimate only — not a maneuver plan or recommendation.',
  'AI-drafted text may contain errors — the authoritative numbers are the computed values shown above.',
  'Decision-support only: no spacecraft is commanded by this system.',
]

// Static items are shown via i18n keys limit.1..limit.7 (English values are
// identical to STATIC_LIMITATIONS); backend-provided extras stay as sent.
// Returns [{ key } | { text }]; de-duplication compares against English.
function mergedLimitations(provenance) {
  const extra = provenance?.probability?.limitations
  const out = STATIC_LIMITATIONS.map((_, i) => ({ key: `limit.${i + 1}` }))
  const english = [...STATIC_LIMITATIONS]
  if (Array.isArray(extra)) {
    const seen = new Set(english.map((l) => l.trim().toLowerCase()))
    for (const l of extra) {
      if (typeof l !== 'string' || !l.trim()) continue
      const key = l.trim().toLowerCase()
      if (!seen.has(key)) { seen.add(key); out.push({ text: l.trim() }) }
    }
  }
  return out
}

// ---------------------------------------------------------------------------
// Brief provenance labels (AI-drafted vs deterministic, consistency check)
// ---------------------------------------------------------------------------
function reviewStatusOf(event, provenance) {
  const s = event.review_status ?? provenance?.ai?.review_status
  if (s) return s
  // Older backend: infer only what we can -- a reviewer note means flagged.
  if (event.generated_by === 'llm' && event.reviewer_notes) return 'flagged'
  return null
}

// generatedByLabel / reviewLabel / brief state live in ../briefState.js
// (pure, unit-tested). A null generated_by means the brief is still being
// generated -- it is never labelled "LLM unavailable".

// Local translation service status, cached briefly so a disabled service
// (the default) costs one GET instead of a POST per opened event.
let translationStatusCache = { at: 0, promise: null }
function translationAvailable() {
  const now = Date.now()
  if (!translationStatusCache.promise || now - translationStatusCache.at > 30000) {
    translationStatusCache = {
      at: now,
      promise: api.getTranslationStatus().then((st) => Boolean(st?.available)).catch(() => false),
    }
  }
  return translationStatusCache.promise
}

const EXPORT_BTN = {
  background: 'transparent', color: 'var(--accent)', border: '1px solid var(--accent-dim)',
  borderRadius: 6, padding: '7px 12px', fontSize: 11, fontWeight: 600,
  letterSpacing: '0.08em', cursor: 'pointer',
}

function buildIncidentReport(event, nameA, nameB, provenance) {
  const samples = event.pc_samples || provenance?.probability?.samples || null
  const isCollision = event.event_class === 'collision_risk'
  const methodNote = samples
    ? `legacy Monte Carlo, ${samples} samples`
    : 'simplified analytic encounter-plane indicator, isotropic σ — not an operational covariance-based Pc'
  let pcLine = null
  if (isCollision) {
    if (isNum(event.pc_score) && event.pc_score >= MIN_ODDS_PC) {
      pcLine = `- Collision probability: ${event.pc_score.toExponential(2)} (1 in ${Math.round(1 / event.pc_score).toLocaleString()}) — ${methodNote}`
    } else {
      pcLine = `- Collision probability: ${formatPcValue(event.pc_score, samples)} — ${methodNote}`
    }
  } else {
    pcLine = '- Collision probability: not computed (proximity watch)'
  }
  const review = reviewLabel(reviewStatusOf(event, provenance))
  const lines = [
    '# ANTARIKSHA-RAKSHA — Incident Report',
    '',
    `Generated: ${new Date().toISOString()} (exported from SSA decision-support console)`,
    event.is_demo ? '' : null,
    event.is_demo ? '**DEMO SCENARIO — controlled geometry created from orbital data for demonstration purposes. Not an operational collision warning.**' : null,
    '',
    '## Event',
    `- Class: ${isCollision ? 'Conjunction (collision risk)' : 'Proximity watch'}`,
    `- Objects: ${nameA} <-> ${nameB}`,
    `- Risk tier: ${event.risk_tier}`,
    `- Time of closest approach: ${event.tca_timestamp}`,
    isNum(event.miss_distance_km) ? `- Miss distance at TCA: ${event.miss_distance_km.toFixed(3)} km` : null,
    isNum(event.rel_velocity_km_s) ? `- Relative velocity at TCA: ${event.rel_velocity_km_s.toFixed(2)} km/s` : null,
    pcLine,
    '',
    '## Brief',
    `Source: ${briefSourceText(event, isToneGuardFallback(event) ? null : provenance?.ai?.generated_by_label)}`,
    review ? `Consistency check (deterministic fact check + same-model LLM review, not independent validation): ${review}` : null,
    event.reviewer_notes
      ? `${isToneGuardFallback(event) ? 'Tone-guard note' : 'Consistency-check note'}: ${event.reviewer_notes}`
      : null,
    '',
    event.brief_text || '(no brief)',
    '',
    '## Illustrative Δv estimate (not a maneuver plan)',
    event.maneuver_text || '(none)',
    isNum(event.delta_v_ms) ? `Delta-v (illustrative): ${event.delta_v_ms.toFixed(2)} m/s` : null,
    '',
    '## Operator Decision',
    `Status: ${event.status || 'pending'}`,
    'Decisions are recorded in the audit trail only; no command is sent to any spacecraft.',
    '',
    '---',
    'Prototype decision-support output. Not for operational use.',
  ]
  return lines.filter((l) => l !== null).join('\n')
}

function useCountUp(target, durationMs = 600) {
  const [value, setValue] = useState(0)
  const rafRef = useRef(null)

  useEffect(() => {
    const prefersReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    if (prefersReduced || !target) {
      setValue(target || 0)
      return
    }
    const start = performance.now()
    const step = (now) => {
      const t = Math.min((now - start) / durationMs, 1)
      setValue(Math.round(target * t))
      if (t < 1) rafRef.current = requestAnimationFrame(step)
    }
    rafRef.current = requestAnimationFrame(step)
    return () => cancelAnimationFrame(rafRef.current)
  }, [target, durationMs])

  return value
}

// ---------------------------------------------------------------------------
// Layout primitives
// ---------------------------------------------------------------------------
function SectionHeading({ n, title, right }) {
  const { tip } = useI18n()
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, marginBottom: 8 }}>
      <span className="eyebrow" style={{ color: 'var(--accent)' }} title={tip(title)}>{n} · {title}</span>
      {right}
    </div>
  )
}

function Field({ label, children, sub }) {
  const { tip } = useI18n()
  return (
    <div style={{ marginBottom: 8 }}>
      <div className="mono" style={{ fontSize: 11, color: 'var(--text-secondary)' }} title={tip(label)}>{label}</div>
      <div className="mono" style={{ fontSize: 13, fontWeight: 600 }}>{children}</div>
      {sub && <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{sub}</div>}
    </div>
  )
}

function ProvenanceField({ label, children }) {
  const { tip } = useI18n()
  return (
    <div style={{ marginBottom: 6 }}>
      <span className="mono" style={{ fontSize: 11, color: 'var(--text-secondary)' }} title={tip(label)}>{label} </span>
      <span className="mono" style={{ fontSize: 11 }}>{children}</span>
    </div>
  )
}

// Optional controlled mode (open + onOpenChange) so the evidence-trace strip
// can open a section before scrolling to it.
function Collapsible({ n, title, right, defaultOpen = false, open: openProp, onOpenChange, id, children }) {
  const [openState, setOpenState] = useState(defaultOpen)
  const controlled = typeof openProp === 'boolean'
  const open = controlled ? openProp : openState
  const setOpen = (fn) => {
    const next = typeof fn === 'function' ? fn(open) : fn
    if (controlled) onOpenChange && onOpenChange(next)
    else setOpenState(next)
  }
  return (
    <div id={id} style={{ border: '1px solid var(--border-line)', borderRadius: 8, marginTop: 12, scrollMarginTop: 8 }}>
      <div
        onClick={() => setOpen((o) => !o)}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setOpen((o) => !o) } }}
        style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '8px 12px', cursor: 'pointer' }}
      >
        <span className="eyebrow" style={{ color: 'var(--accent)' }}>{n} · {title} {open ? '▾' : '▸'}</span>
        {right}
      </div>
      {open && <div style={{ padding: '0 12px 12px' }}>{children}</div>}
    </div>
  )
}

const chipStyle = (color) => ({
  fontSize: 11, padding: '3px 8px', borderRadius: 10, border: `1px solid ${color}`, color,
  fontFamily: "'IBM Plex Mono', var(--font-indic), monospace", letterSpacing: '0.04em',
})

// ---------------------------------------------------------------------------
// Section 2 -- Risk assessment
// ---------------------------------------------------------------------------
function RiskAssessment({ event, provenance, objectsById }) {
  const { t, tip } = useI18n()
  const isCollision = event.event_class === 'collision_risk'
  const prob = provenance?.probability || {}
  const samples = event.pc_samples || prob.samples || null
  const pc = event.pc_score
  const oddsN = isNum(pc) && pc >= MIN_ODDS_PC ? Math.round(1 / pc) : 0
  const countedOdds = useCountUp(oddsN)
  const sigma = event.pc_sigma_km ?? prob.sigma_km
  const hbrKm = event.pc_hard_body_radius_km ?? prob.hard_body_radius_km
  const stateAt = prob.state_evaluated_at || provenance?.analysis?.state_evaluated_at || 'TCA (refined)'

  // Data confidence: predictions degrade as the TLEs behind them age. Show
  // the older of the two objects' TLE ages.
  const ageA = objectsById[event.object_a_id]?.tle_age_days ?? provenance?.data?.object_a?.tle_age_days
  const ageB = objectsById[event.object_b_id]?.tle_age_days ?? provenance?.data?.object_b?.tle_age_days
  const worstAge = Math.max(isNum(ageA) ? ageA : -1, isNum(ageB) ? ageB : -1)

  return (
    <div id={CHAIN_SECTIONS.risk} style={{ scrollMarginTop: 8 }}>
      <SectionHeading n={2} title={t('detail.s2')} />
      {isCollision ? (
        <div style={{ border: '1px solid var(--border-line)', borderRadius: 8, padding: 10, marginBottom: 10 }}>
          <div className="eyebrow" style={{ fontSize: 11, marginBottom: 4 }} title={tip('Pc')}>{t('risk.pcTitle')}</div>
          <div className="mono" style={{ fontSize: 22, fontWeight: 700, color: 'var(--accent)' }}>
            {formatPcValue(pc, samples)}
          </div>
          {oddsN > 0 ? (
            <div className="mono" style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{t('risk.odds', { n: countedOdds.toLocaleString() })}</div>
          ) : isNum(pc) ? (
            <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
              {samples ? t('risk.belowMc') : t('risk.negligible')}
            </div>
          ) : null}
          <div className="mono" style={{ fontSize: 11, marginTop: 8, lineHeight: 1.6 }}>
            <div>{t('risk.method', { v: prob.method || t('risk.methodDefault') })}</div>
            <div>{samples ? t('risk.samples', { n: Number(samples).toLocaleString() }) : t('risk.deterministic')}</div>
            <div>{t('risk.stateAt', { v: renderValue(stateAt) })}</div>
            <div>{t('risk.sigma', { v: isNum(sigma) ? t('risk.sigmaValue', { v: sigma }) : 'N/A' })}</div>
            <div>{t('risk.hbr', { v: isNum(hbrKm) ? `${+(hbrKm * 1000).toFixed(1)} m` : 'N/A' })}</div>
            <div>{t('risk.uncModel', { v: prob.uncertainty_model || t('risk.uncDefault') })}</div>
          </div>
          <div style={{ fontSize: 11, color: 'var(--warn)', marginTop: 6 }}>
            ⚠ {prob.label || t('risk.labelDefault')} — {t('risk.prototypeWarn')}
          </div>
        </div>
      ) : (
        <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 10 }}>
          {t('risk.noPc')}
        </div>
      )}
      <div style={{ fontSize: 12, marginBottom: 6 }}>
        <b title={tierLabelKey(event.risk_tier) ? `${event.risk_tier} — ${t(tierLabelKey(event.risk_tier))}` : undefined}>{event.risk_tier || 'N/A'}</b>{' '}
        <span style={{ color: 'var(--text-secondary)' }}>
          {isCollision ? t('risk.tierFromPc') : t('risk.proxTier')}
        </span>
      </div>
      <RiskDrivers event={event} provenance={provenance} />
      {worstAge >= 0 && (
        <div className="mono" style={{ fontSize: 11, color: worstAge <= 3 ? 'var(--safe)' : 'var(--warn)' }} title={tip('TLE')}>
          {t('risk.confidence')}: ● {worstAge <= 3 ? t('risk.confHigh') : t('risk.confReduced')} — {t('risk.tleAge', { v: worstAge.toFixed(1) })}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Risk drivers (inside Section 2): what the computed values say, separating
// the collision indicator, asset criticality and priority ordering. Values
// are shown as stored by the backend (../riskDrivers.js) -- nothing is
// recomputed here.
// ---------------------------------------------------------------------------
function DriverGroup({ title, children }) {
  return (
    <div style={{ marginBottom: 6 }}>
      <div className="mono" style={{ fontSize: 11, color: 'var(--text-primary)', fontWeight: 600 }}>{title}</div>
      <div style={{ fontSize: 11, color: 'var(--text-secondary)', lineHeight: 1.5 }}>{children}</div>
    </div>
  )
}

function RiskDrivers({ event, provenance }) {
  const { t, tip } = useI18n()
  const vm = riskDriversViewModel(event, provenance)
  return (
    <div id={CHAIN_SECTIONS.drivers} style={{ border: '1px solid var(--border-line)', borderRadius: 8, padding: '8px 10px', marginBottom: 8, scrollMarginTop: 8 }}>
      <div className="eyebrow" style={{ fontSize: 11, marginBottom: 6 }}>{t('drivers.title')}</div>
      <DriverGroup title={`1 · ${t('drivers.pc')}`}>
        {vm.isCollision ? (
          <>
            {vm.collision.pc != null && (
              <div className="mono" title={tip('Pc')}>{t('drivers.pcValue', { v: vm.collision.pc, tier: vm.collision.tier || 'N/A' })}</div>
            )}
            <div title={tip(t(vm.collision.inputsKey))}>{t(vm.collision.inputsKey)}</div>
          </>
        ) : (
          <div title={tip('Pc')}>{t(vm.collision.notComputedKey)}</div>
        )}
      </DriverGroup>
      <DriverGroup title={`2 · ${t('drivers.crit')}`}>
        {vm.criticality.value && (
          <div className="mono">{vm.criticality.value}{vm.criticality.multiplier ? ` (×${vm.criticality.multiplier})` : ''}</div>
        )}
        <div>{t(vm.criticality.noteKey)}</div>
      </DriverGroup>
      <DriverGroup title={`3 · ${t('drivers.prio')}`}>
        {vm.priority.value && <div className="mono">{t('drivers.prioValue', { v: vm.priority.value })}</div>}
        {vm.priority.dwell && <div className="mono">{t('drivers.dwell', { v: vm.priority.dwell })}</div>}
        {vm.priority.basisKey && <div>{t(vm.priority.basisKey)}</div>}
      </DriverGroup>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Evidence trace: compact chain DATA -> ... -> AUDIT. Every step jumps to the
// existing section that shows it; nothing is duplicated. Colour = role
// (computed physics / AI explanation / operator decision / audit record).
// ---------------------------------------------------------------------------
const ROLE_COLOR = {
  physics: 'var(--accent)',
  ai: 'var(--medium)',
  human: 'var(--safe)',
  audit: 'var(--text-secondary)',
}

function EvidenceChain({ event, onJump }) {
  const { t, tip } = useI18n()
  const nodes = evidenceChain(event)
  return (
    <div style={{ marginBottom: 12 }}>
      <div className="mono" style={{ fontSize: 11, color: 'var(--text-secondary)', marginBottom: 4 }} title={t('chain.tip')}>
        {t('chain.title')}
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 4, rowGap: 4 }}>
        {nodes.map((n, i) => {
          const color = ROLE_COLOR[n.role]
          const label = t(n.key)
          const title = [t(CHAIN_ROLE_KEYS[n.role]), n.tipKey ? t(n.tipKey) : null, tip(label)].filter(Boolean).join(' — ')
          return (
            <span key={n.id} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
              {i > 0 && <span aria-hidden="true" style={{ color: 'var(--text-secondary)', fontSize: 11 }}>›</span>}
              <button
                type="button"
                onClick={() => onJump(n.target)}
                title={title}
                style={{
                  background: 'transparent', border: `1px solid ${color}`, color, borderRadius: 10,
                  padding: '1px 7px', fontSize: 10.5, letterSpacing: '0.04em', cursor: 'pointer',
                  fontFamily: "'IBM Plex Mono', var(--font-indic), monospace", whiteSpace: 'nowrap',
                }}
              >
                {label}
                {n.statusKey && <span style={{ color: 'var(--text-secondary)' }}> · {t(n.statusKey)}</span>}
              </button>
            </span>
          )
        })}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Section 3 -- Data provenance
// ---------------------------------------------------------------------------
function DataProvenance({ event, provenance, error, open, onOpenChange }) {
  const { t } = useI18n()
  const isDemo = provenance?.provenance?.mode
    ? provenance.provenance.mode === 'DEMO'
    : Boolean(provenance?.provenance?.is_demo ?? event.is_demo)
  const data = provenance?.data || {}
  const prop = provenance?.propagation || {}
  const analysis = provenance?.analysis || {}
  const run = data.screening_run && typeof data.screening_run === 'object' ? data.screening_run : null
  const objB = data.object_b || {}
  const objA = data.object_a || {}
  const tcaRef = analysis.tca_refinement ?? event.tca_refinement

  return (
    <Collapsible
      n={3}
      id={CHAIN_SECTIONS.provenance}
      open={open}
      onOpenChange={onOpenChange}
      title={t('detail.s3')}
      right={(provenance || event.is_demo != null) && (
        <span className="mono" style={{ fontSize: 11, color: isDemo ? 'var(--warn)' : 'var(--text-secondary)' }}>
          {isDemo ? `◆ ${t('detail.demoTitle')}` : `● ${t('prov.public')}`}
        </span>
      )}
    >
      {error && <div className="mono" style={{ fontSize: 11, color: 'var(--warn)' }}>{t('prov.unavailable')}</div>}
      {!error && !provenance && <div className="mono" style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{t('common.loading')}</div>}
      {provenance && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 12 }}>
          <div>
            <ProvenanceField label={t('prov.source')}>{renderValue(data.source)}</ProvenanceField>
            <ProvenanceField label={t('prov.objA')}>
              {renderValue(objA.name)}{objA.norad_id != null ? ` (${objA.norad_id})` : ''} · {t('prov.tleAge', { v: isNum(objA.tle_age_days) ? `${objA.tle_age_days} d` : 'N/A' })}
            </ProvenanceField>
            <ProvenanceField label={t('prov.objB')}>
              {renderValue(objB.name)}{objB.norad_id != null ? ` (${objB.norad_id})` : ''} · {objTypeText(t, objB.object_type) || renderValue(objB.object_type)} · {t('prov.tleAge', { v: isNum(objB.tle_age_days) ? `${objB.tle_age_days} d` : 'N/A' })}
            </ProvenanceField>
            {objB.demo_adjusted && (
              <div className="mono" style={{ fontSize: 11, color: 'var(--warn)', marginBottom: 6 }}>
                ◆ {t('prov.demoAdjusted')}
                {(objB.derived_from_norad_id || objB.derived_from) ? t('prov.derived', { id: renderValue(objB.derived_from_norad_id || objB.derived_from) }) : ''}
              </div>
            )}
          </div>
          <div>
            {run ? (
              <>
                <div className="mono" style={{ fontSize: 11, color: 'var(--text-secondary)', marginBottom: 4 }}>{t('prov.run')}</div>
                {Object.entries(run).map(([k, v]) => (
                  <ProvenanceField key={k} label={k.replace(/_/g, ' ').toUpperCase()}>{renderValue(v)}</ProvenanceField>
                ))}
              </>
            ) : (
              <ProvenanceField label={t('prov.run')}>
                {event.screening_run_id != null ? `#${event.screening_run_id}` : 'N/A'}
              </ProvenanceField>
            )}
          </div>
          <div>
            <ProvenanceField label={t('prov.propagation')}>
              {t('prov.propValue', { engine: renderValue(prop.engine), h: renderValue(prop.horizon_hours), s: renderValue(prop.step_seconds) })}
              {(() => {
                const n = horizonNote(prop, provenance?.data?.screening_run, event)
                return n.noteKey ? <span style={{ fontSize: 10, color: 'var(--text-secondary)', marginLeft: 4 }}>{t(n.noteKey, n.noteVars)}</span> : null
              })()}
            </ProvenanceField>
            <ProvenanceField label={t('prov.threshold')}>
              {t('prov.thresholdNote', { v: renderValue(analysis.screening_threshold_km) })}
            </ProvenanceField>
            <ProvenanceField label={t('prov.tcaRef')}>{renderValue(tcaRef)}</ProvenanceField>
            {analysis.state_evaluated_at && (
              <ProvenanceField label={t('prov.stateAt')}>{renderValue(analysis.state_evaluated_at)}</ProvenanceField>
            )}
            {provenance.probability?.pc_resolution != null && (
              <ProvenanceField label={t('prov.pcRes')}>{renderValue(provenance.probability.pc_resolution)}</ProvenanceField>
            )}
            <ProvenanceField label={t('prov.risk')}>
              {renderValue(provenance.risk?.tier)} · {t('prov.criticality', { v: renderValue(provenance.risk?.criticality) })}
              {provenance.risk?.criticality_multiplier != null ? ` (×${provenance.risk.criticality_multiplier})` : ''}
            </ProvenanceField>
          </div>
        </div>
      )}
    </Collapsible>
  )
}

// ---------------------------------------------------------------------------
// Section 4 -- AI analysis
// ---------------------------------------------------------------------------
function AiAnalysis({ event, provenance, briefPollExhausted }) {
  const { t, tip, lang } = useI18n()
  const [notesOpen, setNotesOpen] = useState(false)
  const state = briefState(event)
  const isLlm = state === BRIEF_LLM
  const isPending = state === BRIEF_PENDING
  const toneGuard = isToneGuardFallback(event)
  const review = isLlm ? reviewStatusOf(event, provenance) : null
  // reviewLabel (briefState.js) decides WHETHER a review chip exists; the
  // text is its translated equivalent.
  const reviewText = reviewLabel(review) ? t(reviewLabelKey(review)) : null

  // Dynamic brief translation: requested from the LOCAL backend only when the
  // UI language is not English and a brief exists. English stays available.
  const [translation, setTranslation] = useState(null)
  const [showOriginal, setShowOriginal] = useState(false)
  const wantTranslation = shouldRequestTranslation(lang, event, isPending)
  useEffect(() => {
    setShowOriginal(false)
    if (!wantTranslation) {
      setTranslation(null)
      return
    }
    let cancelled = false
    setTranslation({ loading: true })
    translationAvailable()
      .then((available) => (available
        ? api.translateBrief(event.id, lang)
        : { translated: false, status: 'unavailable', reason: 'local translation unavailable' }))
      .then((r) => { if (!cancelled) setTranslation(r) })
      .catch((err) => { if (!cancelled) setTranslation({ error: String(err?.message || err) }) })
    return () => { cancelled = true }
  }, [wantTranslation, event.id, lang, event.brief_text, event.maneuver_text])
  const display = briefDisplay({ lang, event, translation: wantTranslation ? translation : null, showOriginal })
  const ai = provenance?.ai || {}
  const reviewColor = review === 'flagged' ? 'var(--warn)' : review === 'consistent' ? 'var(--safe)' : 'var(--text-secondary)'
  const checkTip = (ai.review_description ? `${ai.review_description} ` : '') + t('brief.checkTip')
  const boundary = aiBoundary(event, { reviewStatus: review, translationShown: display.mode === 'translated' })
  const statusColor = boundary.status.tone === 'safe' ? 'var(--safe)' : boundary.status.tone === 'warn' ? 'var(--warn)' : 'var(--text-secondary)'

  return (
    <div id={CHAIN_SECTIONS.ai} style={{ scrollMarginTop: 8 }}>
      <SectionHeading n={4} title={t(BRIEF_HEADING_KEYS[state])} />
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 6 }}>
        <span style={chipStyle(isLlm || isPending ? 'var(--accent)' : 'var(--text-secondary)')} title={ai.model ? t('brief.model', { v: ai.model }) : tip('LLM')}>
          ⬡ {t(BRIEF_SOURCE_KEYS[briefSourceKind(event)])}
        </span>
        {reviewText && (
          <span
            style={{ ...chipStyle(reviewColor), cursor: event.reviewer_notes ? 'pointer' : 'help' }}
            title={checkTip}
            onClick={() => event.reviewer_notes && setNotesOpen((o) => !o)}
          >
            {reviewText}{event.reviewer_notes ? (notesOpen ? ' ▾' : ' ▸') : ''}
          </span>
        )}
      </div>
      <div className="mono" style={{ fontSize: 11, color: statusColor, marginBottom: 6 }} title={t('ai.statement')}>
        ● {t(boundary.status.key)}
      </div>
      {toneGuard && event.reviewer_notes && (
        <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginBottom: 6 }}>{t('brief.toneGuardNote', { v: event.reviewer_notes })}</div>
      )}
      {notesOpen && event.reviewer_notes && (
        <div style={{ fontSize: 12, color: review === 'flagged' ? 'var(--warn)' : 'var(--text-secondary)', marginBottom: 6 }}>{event.reviewer_notes}</div>
      )}
      <div style={{ fontSize: 11, color: 'var(--text-secondary)', lineHeight: 1.45, marginBottom: 4 }} title={tip(t('brief.aiScope'))}>
        {t('brief.aiScope')}
      </div>
      {reviewText && (
        <div style={{ fontSize: 11, color: 'var(--text-secondary)', fontStyle: 'italic', marginBottom: 4 }}>
          {t('brief.checkNote')}
        </div>
      )}
      {isPending ? (
        <p style={{ fontSize: 12, lineHeight: 1.55, marginTop: 8, marginBottom: 8, color: 'var(--text-secondary)', fontStyle: 'italic' }}>
          {briefPollExhausted ? t('brief.notYet') : t('brief.generating')}
        </p>
      ) : (
        <>
          {display.noticeKey && (
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center', marginTop: 8 }}>
              <span
                style={chipStyle(display.mode === 'fallback' ? 'var(--warn)' : 'var(--text-secondary)')}
                title={display.mode === 'fallback' ? (translation?.reason || translation?.error || undefined) : t('brief.mtTip')}
              >
                {t(display.noticeKey)}
              </span>
              {display.noticeKey === 'brief.mtLabel' && (
                <button
                  onClick={() => setShowOriginal((v) => !v)}
                  style={{
                    background: 'transparent', color: 'var(--accent)', border: '1px solid var(--accent-dim)',
                    borderRadius: 6, padding: '2px 8px', fontSize: 11, cursor: 'pointer',
                  }}
                >
                  {showOriginal ? t('brief.showTranslation') : t('brief.showEnglish')}
                </button>
              )}
            </div>
          )}
          <p
            lang={display.mode === 'translated' ? lang : 'en'}
            style={{ fontSize: 13, lineHeight: 1.55, marginTop: 8, marginBottom: 8 }}
          >
            {display.briefText || t('brief.none')}
          </p>
        </>
      )}
      <div style={{ border: '1px solid var(--border-line)', borderRadius: 8, padding: 10 }}>
        <div className="eyebrow" style={{ fontSize: 11, marginBottom: 4 }} title={tip('Δv')}>{t('brief.dvTitle')}</div>
        {display.maneuverText && (
          <div className="mono" lang={display.mode === 'translated' ? lang : 'en'} style={{ fontSize: 12 }}>{display.maneuverText}</div>
        )}
        {isNum(event.delta_v_ms) && (
          <div className="mono" style={{ fontSize: 12, color: 'var(--accent)', marginTop: 4 }}>
            Δv ≈ {event.delta_v_ms.toFixed(2)} m/s
          </div>
        )}
        <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 4 }}>
          {t('brief.dvNote')}
        </div>
      </div>
      <AiBoundary boundary={boundary} />
    </div>
  )
}

// AI boundary for THIS event: what the AI did and did not do. Physics
// evidence comes from the deterministic backend; the AI only explains it.
function AiBoundary({ boundary }) {
  const { t } = useI18n()
  return (
    <div style={{ border: '1px solid var(--border-line)', borderRadius: 8, padding: '8px 10px', marginTop: 8 }}>
      <div style={{ fontSize: 11, color: 'var(--text-primary)', marginBottom: 6, lineHeight: 1.45 }}>{t(boundary.statementKey)}</div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 8, fontSize: 11, color: 'var(--text-secondary)' }}>
        <div>
          <div className="mono" style={{ color: 'var(--text-primary)' }}>{t('ai.didTitle')}</div>
          <ul style={{ margin: 0, paddingLeft: 14 }}>{boundary.did.map((k) => <li key={k}>{t(k)}</li>)}</ul>
        </div>
        <div>
          <div className="mono" style={{ color: 'var(--text-primary)' }}>{t('ai.didNotTitle')}</div>
          <ul style={{ margin: 0, paddingLeft: 14 }}>{boundary.didNot.map((k) => <li key={k}>{t(k)}</li>)}</ul>
        </div>
      </div>
    </div>
  )
}

// Analyst summary: deterministic, assembled from values the backend already
// computed (no LLM, no new physics).
function AnalystSummary({ rows }) {
  const { t, tip } = useI18n()
  if (!rows.length) return null
  const partText = (p) => (p.text != null ? p.text : t(p.key, p.vars))
  return (
    <div style={{ border: '1px solid var(--border-line)', borderRadius: 8, padding: '8px 10px', marginBottom: 12 }}>
      <div className="eyebrow" style={{ fontSize: 11, marginBottom: 6 }} title={t('analyst.tip')}>{t('analyst.title')}</div>
      <div style={{ display: 'grid', gridTemplateColumns: 'max-content 1fr', columnGap: 12, rowGap: 3, fontSize: 11 }}>
        {rows.map((r) => (
          <div key={r.id} style={{ display: 'contents' }}>
            <span className="mono" style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{t(r.labelKey)}</span>
            <span style={{ minWidth: 0 }} title={tip(r.parts.map(partText).join(' · '))}>{r.parts.map(partText).join(' · ')}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

// Chronological screening observations, AI explanation and operator
// decisions for the event (stored values and audit records only).
function EventTimeline({ entries }) {
  const { t } = useI18n()
  return (
    <div style={{ marginTop: 10 }}>
      <div className="mono" style={{ fontSize: 11, color: 'var(--text-secondary)', marginBottom: 4 }}>{t('timeline.title')}</div>
      {entries.length === 0 ? (
        <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{t('timeline.empty')}</div>
      ) : (
        <ol style={{ margin: 0, paddingLeft: 18, fontSize: 11, lineHeight: 1.6 }}>
          {entries.map((e, i) => (
            <li key={i} style={{ color: e.kind === 'decision' ? 'var(--safe)' : e.kind === 'ai' ? 'var(--medium)' : 'var(--text-primary)' }}>
              <span className="mono" style={{ color: 'var(--text-secondary)', fontSize: 11 }}>{e.at ? formatUtc(e.at) : '—'}</span>{' '}
              {t(e.labelKey, e.vars)}
              {e.kind === 'decision' && e.vars.reason ? <span style={{ color: 'var(--text-secondary)' }}> · {t('hist.reason', { v: e.vars.reason })}</span> : null}
              {e.isDemo ? <span style={{ color: 'var(--warn)' }}> · ◆ {t('common.demo')}</span> : null}
            </li>
          ))}
        </ol>
      )}
    </div>
  )
}

// Section 6 -- the facts the assessment rests on, what data is and is not
// connected, and its limitations. No numerical confidence score.
function AssessmentBasis({ event, provenance }) {
  const { t, tip } = useI18n()
  const b = assessmentBasis(event, provenance, formatUtc)
  return (
    <div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 12 }}>
        {b.groups.map((g) => (
          <div key={g.titleKey}>
            <div className="mono" style={{ fontSize: 11, color: 'var(--text-primary)', marginBottom: 4 }}>{t(g.titleKey)}</div>
            {g.rows.length === 0 && <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>N/A</div>}
            {g.rows.map((r) => (
              <ProvenanceField key={r.labelKey} label={t(r.labelKey)}>
                <span title={tip(r.value ?? t(r.valueKey))}>{r.value ?? t(r.valueKey)}</span>
                {r.noteKey && (
                  <span style={{ fontSize: 10, color: 'var(--text-secondary)', marginLeft: 4 }}>{t(r.noteKey, r.noteVars)}</span>
                )}
              </ProvenanceField>
            ))}
          </div>
        ))}
        <div>
          <div className="mono" style={{ fontSize: 11, color: 'var(--text-primary)', marginBottom: 4 }}>{t('basis.coverage')}</div>
          <div style={{ fontSize: 11, color: 'var(--safe)' }}>{b.coverage.connected.map((k) => <div key={k}>✓ {t(k)}</div>)}</div>
          <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 2 }}>{b.coverage.notConnected.map((k) => <div key={k}>✗ {t(k)}</div>)}</div>
          <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 4, fontStyle: 'italic' }}>{t('basis.covNote')}</div>
        </div>
      </div>
      <div className="mono" style={{ fontSize: 11, color: 'var(--warn)', marginTop: 10, marginBottom: 2 }}>{t('basis.limits')}</div>
      <ul style={{ margin: 0, paddingLeft: 16, fontSize: 11, color: 'var(--text-secondary)', lineHeight: 1.55 }}>
        {b.limitationKeys.map((k) => <li key={k}>{t(k)}</li>)}
      </ul>
      <div className="mono" style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 8, marginBottom: 2 }}>{t('basis.details')}</div>
      <ul style={{ margin: 0, paddingLeft: 16, fontSize: 11, color: 'var(--text-secondary)', lineHeight: 1.55 }}>
        {mergedLimitations(provenance).map((l, i) => <li key={i}>{l.key ? t(l.key) : l.text}</li>)}
      </ul>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Section 5 -- Human decision + audit history
// ---------------------------------------------------------------------------
function DecisionHistory({ history, error }) {
  const { t } = useI18n()
  if (error && !history) {
    return <div className="mono" style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{t('hist.unavailable')}</div>
  }
  if (!history) return <div className="mono" style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{t('hist.loading')}</div>
  if (history.length === 0) {
    return <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{t('hist.empty')}</div>
  }
  return (
    <div>
      {history.map((d, i) => {
        // "APPROVED · <UTC> · OPERATOR · sejal"; records without an actor
        // predate authentication and are labelled as such.
        const line = decisionLine(d, formatUtc)
        return (
        <div key={d.id ?? i} style={{
          padding: '6px 8px', marginBottom: 4, borderRadius: 6, background: 'rgba(11, 16, 32, 0.5)',
          borderLeft: `3px solid ${d.decision === 'approved' ? 'var(--safe)' : 'var(--warn)'}`,
        }}>
          <div className="mono" style={{ fontSize: 11 }}>
            {line.decision}
            <span style={{ color: 'var(--text-secondary)' }}> · {line.at}</span>
            {line.actor
              ? <span title={t('hist.actorTip')}> · <span style={{ color: 'var(--accent)' }}>{line.actor.role}</span> · {line.actor.username}</span>
              : <span style={{ color: 'var(--text-secondary)', fontStyle: 'italic' }}> · {t('hist.legacy')}</span>}
            {d.is_demo ? <span style={{ color: 'var(--warn)' }}> · ◆ {t('common.demo')}</span> : null}
          </div>
          {d.rejection_reason && (
            <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 2 }}>{t('hist.reason', { v: d.rejection_reason })}</div>
          )}
        </div>
        )
      })}
    </div>
  )
}

export default function EventDetail({ event, objectsById, onClose, onDecided, briefPollExhausted = false }) {
  const { t } = useI18n()
  const { scale, layout } = useUiScale()
  // Large text / narrow screens: drawer spans the full width above the side
  // panels and its column grids wrap instead of squeezing.
  const gridCols = (normal) => (layout.compactGrid ? 'repeat(auto-fit, minmax(220px, 1fr))' : normal)
  const isCollision = event.event_class === 'collision_risk'
  const objA = objectsById[event.object_a_id]
  const objB = objectsById[event.object_b_id]
  const nameA = objA?.name || String(event.object_a_id)
  const nameB = objB?.name || String(event.object_b_id)

  const [profile, setProfile] = useState(null)
  const [provenance, setProvenance] = useState(null)
  const [provError, setProvError] = useState(false)
  const [history, setHistory] = useState(null)
  const [historyError, setHistoryError] = useState(false)

  useEffect(() => {
    let cancelled = false
    setProfile(null)
    api.getEventProfile(event.id)
      .then((p) => { if (!cancelled) setProfile(p) })
      .catch((err) => console.error('Failed to fetch separation profile', err))
    return () => { cancelled = true }
  }, [event.id])

  // Provenance carries the brief's model/label, so re-fetch it once the brief
  // leaves the 'pending' state (App re-polls the event detail while pending).
  const currentBriefState = briefState(event)
  useEffect(() => {
    let cancelled = false
    setProvenance(null)
    setProvError(false)
    api.getEventProvenance(event.id)
      .then((p) => { if (!cancelled) setProvenance(p) })
      .catch((err) => { console.error('Failed to fetch event provenance', err); if (!cancelled) setProvError(true) })
    return () => { cancelled = true }
  }, [event.id, currentBriefState])

  const loadHistory = useCallback(() => {
    setHistoryError(false)
    return api.getEventDecisions(event.id)
      .then((h) => setHistory(Array.isArray(h) ? h : []))
      .catch((err) => {
        console.error('Failed to fetch decision history', err)
        setHistoryError(true)
      })
  }, [event.id])

  useEffect(() => {
    setHistory(null)
    loadHistory()
  }, [loadHistory])

  // Event evolution across screening runs (stored observations only).
  const [evolution, setEvolution] = useState(null)
  const [evolutionError, setEvolutionError] = useState(false)
  useEffect(() => {
    let cancelled = false
    setEvolution(null)
    setEvolutionError(false)
    api.getEventEvolution(event.id)
      .then((r) => { if (!cancelled) setEvolution(r) })
      .catch((err) => { console.error('Failed to fetch event evolution', err); if (!cancelled) setEvolutionError(true) })
    return () => { cancelled = true }
    // Re-fetch when the same event id is re-screened (e.g. DEMO HISTORY step 2).
  }, [event.id, event.screening_run_id])
  const evolutionVm = evolutionViewModel(evolution)
  // Opens by default once there is more than one observation to compare;
  // after the operator toggles it, their choice wins.
  const [evoOpen, setEvoOpen] = useState(null)
  const [provOpen, setProvOpen] = useState(false)
  const [basisOpen, setBasisOpen] = useState(false)
  useEffect(() => { setEvoOpen(null); setProvOpen(false); setBasisOpen(false) }, [event.id])
  const evoOpenEffective = evoOpen ?? Boolean(evolutionVm && !evolutionVm.single)

  // Evidence-trace jump: open the target section if collapsed, then scroll.
  const jumpTo = (target) => {
    if (target === CHAIN_SECTIONS.provenance) setProvOpen(true)
    if (target === CHAIN_SECTIONS.basis) setBasisOpen(true)
    const go = () => {
      try { document.getElementById(target)?.scrollIntoView({ behavior: 'smooth', block: 'start' }) } catch { /* no DOM */ }
    }
    if (typeof requestAnimationFrame === 'function') requestAnimationFrame(go)
    else go()
  }

  // Older backend without /decisions: fall back to provenance.human_decision.
  const provHistory = Array.isArray(provenance?.human_decision?.history) ? provenance.human_decision.history : null
  const effectiveHistory = history ?? (historyError ? provHistory : null)

  const handleDecided = (id, action) => {
    onDecided && onDecided(id, action)
    loadHistory()
  }

  // English source-of-record report as Word (.docx) or PDF (browser print).
  const reportBlocks = () => parseReport(buildIncidentReport(event, nameA, nameB, provenance))
  const exportDocx = () => {
    downloadReportDocx(reportBlocks(), reportFilename(nameA, nameB, event.tca_timestamp, 'docx'))
      .catch((err) => console.error('Report export failed', err))
  }
  const exportPdf = () => printReportPdf(reportBlocks(), reportFilename(nameA, nameB, event.tca_timestamp, 'pdf'))


  const objBType = objB?.object_type || provenance?.data?.object_b?.object_type
  const objBDemoAdjusted = Boolean(objB?.demo_adjusted || provenance?.data?.object_b?.demo_adjusted)
  const criticality = objA?.criticality || provenance?.data?.object_a?.criticality || provenance?.risk?.criticality
  const feedbackMechanism = provenance?.ai?.feedback_mechanism

  return (
    <div style={{
      ...drawerStyle,
      ...layout.drawer,
      maxHeight: layout.drawerMaxHeight,
      zIndex: layout.drawerFull ? 35 : drawerStyle.zIndex,
      zoom: scale,
    }}>
      <button onClick={onClose} style={closeButtonStyle} aria-label={t('common.close')}>✕</button>

      <div className="eyebrow" style={{ marginBottom: 8, paddingRight: 24, color: 'var(--text-primary)' }}>
        {nameA} &#8660; {nameB}
        <span style={{ color: 'var(--text-secondary)' }}> · {isCollision ? t('detail.conjunction') : t('detail.proximity')}</span>
      </div>
      {event.is_demo ? (
        <div style={{
          border: '1px solid var(--warn)', borderRadius: 8, padding: '8px 12px', marginBottom: 14,
          background: 'color-mix(in srgb, var(--warn) 14%, transparent)', color: 'var(--warn)', fontSize: 12, lineHeight: 1.45,
        }}>
          <b>◆ {t('detail.demoTitle')}</b> — {t('detail.demoBanner')}
        </div>
      ) : (
        <div className="mono" style={{ fontSize: 11, color: 'var(--text-secondary)', marginBottom: 14 }}>
          ● {t('detail.liveSource')}
        </div>
      )}

      <EvidenceChain event={event} onJump={jumpTo} />
      <AnalystSummary rows={analystSummary({ event, provenance, evolutionVm, history: effectiveHistory, nameA, nameB, formatUtc })} />

      {/* Sections render in DOM order 1..7 (no CSS order / grid placement), so
          the visual reading order is the numerical order at every width. */}
      <div style={{ display: 'grid', gridTemplateColumns: gridCols('1fr 1fr'), gap: 20 }}>
        {/* 1 -- Event summary */}
        <div id={CHAIN_SECTIONS.summary} style={{ minWidth: 0, scrollMarginTop: 8 }}>
          <SectionHeading n={1} title={t('detail.s1')} />
          <Field label={t('detail.asset')}>{nameA}{criticality ? <span style={{ color: 'var(--text-secondary)', fontWeight: 400 }}> · {criticality}</span> : null}</Field>
          <Field label={t('detail.object')}>
            {nameB}
            <span style={{ color: 'var(--text-secondary)', fontWeight: 400 }}> · {objTypeText(t, objBType) || objBType || 'N/A'}</span>
            {objBDemoAdjusted && <span style={{ color: 'var(--warn)', fontWeight: 400 }}> {t('detail.demoAdjusted')}</span>}
          </Field>
          <Field label={t('detail.riskTier')}>
            {event.risk_tier || 'N/A'}
            {tierLabelKey(event.risk_tier) && t(tierLabelKey(event.risk_tier)) !== event.risk_tier && (
              <span style={{ color: 'var(--text-secondary)', fontWeight: 400 }}> · {t(tierLabelKey(event.risk_tier))}</span>
            )}
          </Field>
          <Field label={t('detail.tca')}>{formatUtc(event.tca_timestamp)}</Field>
          <Field label={t('detail.miss')}>{isNum(event.miss_distance_km) ? `${event.miss_distance_km.toFixed(3)} km` : 'N/A'}</Field>
          <Field label={t('detail.relVel')}>{isNum(event.rel_velocity_km_s) ? `${event.rel_velocity_km_s.toFixed(2)} km/s` : 'N/A'}</Field>
          <SeparationChart profile={profile} />
          <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8 }} title={t('detail.exportTip')}>
            <span className="eyebrow" style={{ color: 'var(--text-secondary)' }}>{t('detail.export')}</span>
            <button onClick={exportDocx} title={t('detail.exportTip')} style={EXPORT_BTN}>⤓ Word (.docx)</button>
            <button onClick={exportPdf} title={t('detail.exportPdfTip')} style={EXPORT_BTN}>⎙ PDF</button>
          </div>
        </div>

        {/* 2 -- Risk assessment */}
        <div style={{ minWidth: 0 }}>
          <RiskAssessment event={event} provenance={provenance} objectsById={objectsById} />
        </div>
      </div>

      {/* 3 -- Data provenance */}
      <DataProvenance event={event} provenance={provenance} error={provError} open={provOpen} onOpenChange={setProvOpen} />

      {/* 4 -- AI analysis */}
      <div style={{ minWidth: 0, marginTop: 12 }}>
        <AiAnalysis key={event.id} event={event} provenance={provenance} briefPollExhausted={briefPollExhausted} />
      </div>

      {/* 5 -- Human decision */}
      <div id={CHAIN_SECTIONS.human} style={{ border: '1px solid var(--border-line)', borderRadius: 8, marginTop: 12, padding: 12, scrollMarginTop: 8 }}>
        <SectionHeading n={5} title={t('detail.s5')} />
        <div style={{ display: 'grid', gridTemplateColumns: gridCols('1fr 1fr'), gap: 20 }}>
          <div>
            <ApprovalPanel key={event.id} event={event} onDecided={handleDecided} />
            {feedbackMechanism && (
              <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 6 }}>{t('detail.feedback', { v: renderValue(feedbackMechanism) })}</div>
            )}
          </div>
          <div id={CHAIN_SECTIONS.audit} style={{ scrollMarginTop: 8 }}>
            <div className="mono" style={{ fontSize: 11, color: 'var(--text-secondary)', marginBottom: 6 }}>{t('hist.title')}</div>
            <DecisionHistory history={effectiveHistory} error={historyError} />
          </div>
        </div>
      </div>

      {/* 6 -- Scientific limitations */}
      <Collapsible n={6} id={CHAIN_SECTIONS.basis} title={t('basis.title')} open={basisOpen} onOpenChange={setBasisOpen}>
        <AssessmentBasis event={event} provenance={provenance} />
      </Collapsible>

      {/* 7 -- Event evolution across screening runs */}
      <Collapsible
        n={7}
        id={CHAIN_SECTIONS.evolution}
        title={t('evo.title')}
        open={evoOpenEffective}
        onOpenChange={setEvoOpen}
        right={evolutionVm && evolutionVm.rows.length > 1 ? (
          <span className="mono" style={{ fontSize: 11, color: 'var(--text-secondary)' }}>×{evolutionVm.rows.length}</span>
        ) : null}
      >
        <EventEvolution vm={evolutionVm} error={evolutionError} compact={layout.compactGrid} />
        <EventTimeline entries={eventTimeline(evolution, effectiveHistory, event)} />
      </Collapsible>
    </div>
  )
}

const drawerStyle = {
  position: 'absolute',
  left: 372, right: 312, bottom: 16, // overridden by uiScale overlayLayout
  maxHeight: 480,
  overflowY: 'auto',
  background: 'var(--bg-panel-solid)',
  border: '1px solid var(--border-line)',
  borderRadius: 10,
  padding: '16px 20px',
  zIndex: 20,
  animation: 'slideUp 200ms ease-out',
}

const closeButtonStyle = {
  position: 'absolute',
  top: 10, right: 12,
  background: 'transparent',
  border: 'none',
  color: 'var(--text-secondary)',
  fontSize: 16,
  cursor: 'pointer',
}
