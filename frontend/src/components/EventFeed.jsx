import { useEffect, useRef, useState } from 'react'
import { useI18n } from '../i18n'
import { useAuth } from '../authContext'
import { PERMISSIONS, actionErrorText } from '../auth'
import { refreshErrorText } from '../opsView'
import { tierLabelKey } from '../i18n/core'
import { useUiScale } from '../uiScaleContext'

const TIER_COLOR = {
  Critical: 'var(--critical)',
  High: 'var(--warn)',
  Medium: 'var(--medium)',
  Low: 'var(--safe)',
}

// samples is only set for legacy Monte Carlo events (0 = below 1/N
// resolution); the analytic indicator is continuous, so 0 is numerical zero.
function formatPc(pc, samples) {
  if (pc == null || Number.isNaN(Number(pc))) return 'N/A'
  if (pc <= 0) return samples ? `< 1/${samples.toLocaleString()}` : '≈ 0'
  return pc.toExponential(1)
}

function EventRow({ event, selected, isNew, onSelect, objectsById }) {
  const nameA = objectsById[event.object_a_id]?.name || event.object_a_id
  const nameB = objectsById[event.object_b_id]?.name || event.object_b_id
  const tcaShort = event.tca_timestamp ? event.tca_timestamp.slice(0, 16).replace('T', ' ') + ' UTC' : ''
  const { t, tip } = useI18n()
  const tierKey = tierLabelKey(event.risk_tier)
  const meta = t('feed.rowMeta', { tca: tcaShort, miss: event.miss_distance_km?.toFixed(2) })

  return (
    <div
      onClick={() => onSelect(event)}
      tabIndex={0}
      role="button"
      style={{
        display: 'flex',
        gap: 8,
        padding: '10px 12px',
        cursor: 'pointer',
        background: selected ? 'var(--accent-dim)' : 'transparent',
        borderRadius: 6,
        marginBottom: 4,
        animation: isNew ? 'fadeIn 300ms ease-out' : undefined,
      }}
    >
      <div style={{ width: 3, borderRadius: 2, background: selected ? 'var(--accent)' : TIER_COLOR[event.risk_tier], flexShrink: 0 }} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginBottom: 3 }}>
          <span title={tierKey ? `${event.risk_tier} — ${t(tierKey)}` : undefined} style={{
            fontSize: 11, textTransform: 'uppercase', padding: '2px 6px', borderRadius: 10,
            background: `color-mix(in srgb, ${TIER_COLOR[event.risk_tier]} 15%, transparent)`,
            color: TIER_COLOR[event.risk_tier],
          }}>{event.risk_tier}</span>
          <span style={{ fontSize: 10, textTransform: 'uppercase', padding: '2px 6px', borderRadius: 10, border: '1px solid var(--border-line)', color: 'var(--text-secondary)' }}>
            {event.event_class === 'collision_risk' ? t('feed.conjunction') : t('feed.proximity')}
          </span>
          {Boolean(event.is_demo) && (
            <span
              title={t('feed.demoTip')}
              style={{ fontSize: 10, fontWeight: 700, padding: '2px 6px', borderRadius: 10, border: '1px solid var(--warn)', color: 'var(--warn)', background: 'color-mix(in srgb, var(--warn) 15%, transparent)' }}
            >◆ {t('common.demo')}</span>
          )}
        </div>
        <div style={{ fontSize: 13, marginBottom: 3, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {nameA} &#8660; {nameB}
        </div>
        <div className="mono" style={{ fontSize: 11, color: 'var(--text-secondary)' }} title={tip(`${meta} Pc`)}>
          {meta}
          {event.event_class === 'collision_risk' ? ` · Pc ${formatPc(event.pc_score, event.pc_samples)}` : ''}
        </div>
      </div>
    </div>
  )
}

// Perceived progress for the duration of the single blocking /api/demo/seed
// call -- the backend doesn't stream intermediate steps, so this just
// advances a label on a timer while the request is in flight, capping at
// the last stage rather than looping (no false precision, no busywork
// animation beyond what the real wait already requires).
const DEMO_STAGES = ['demo.stage1', 'demo.stage2', 'demo.stage3', 'demo.stage4'] // i18n keys

function useStagedLabel(active, stages, intervalMs = 900) {
  const [idx, setIdx] = useState(0)
  useEffect(() => {
    if (!active) {
      setIdx(0)
      return
    }
    const id = setInterval(() => setIdx((i) => Math.min(i + 1, stages.length - 1)), intervalMs)
    return () => clearInterval(id)
  }, [active, stages, intervalMs])
  return stages[idx]
}

const demoButtonStyle = (active) => ({
  flex: 1, padding: '7px 10px', borderRadius: 6, fontSize: 11,
  fontWeight: 600, letterSpacing: '0.06em', textTransform: 'uppercase',
  background: 'transparent', border: '1px solid var(--warn)', color: 'var(--warn)',
  cursor: active ? 'default' : 'pointer', opacity: active ? 0.5 : 1,
})

// Compact demo control: presenter picks COLLISION or PROXIMITY, sees staged
// progress while the real screening/risk/brief pipeline runs on the seeded
// geometry, and gets a clear failure message (never a silent empty feed) if
// the scenario didn't produce its intended event.
function DemoControl({ onSimulate, demoActive }) {
  const { t } = useI18n()
  const [simulating, setSimulating] = useState(false)
  const [lastRun, setLastRun] = useState({ mode: 'collision', opts: {} })
  const [error, setError] = useState(null)
  // DEMO HISTORY: step 2 is offered after a step-1 run (or whenever demo mode
  // is active -- the backend answers 409 with a readable detail if step 1 is
  // missing, and that detail is shown).
  const [historyStarted, setHistoryStarted] = useState(false)
  const stageLabel = useStagedLabel(simulating, DEMO_STAGES)

  const run = async (mode, opts = {}) => {
    setLastRun({ mode, opts })
    setError(null)
    setSimulating(true)
    try {
      await onSimulate(mode, opts)
      if (opts.historyStep === 1) setHistoryStarted(true)
      else if (opts.historyStep !== 2) setHistoryStarted(false)
    } catch (err) {
      setError(actionErrorText(err, t) || t('demo.unknownError'))
    } finally {
      setSimulating(false)
    }
  }
  const showStep2 = historyStarted || demoActive

  return (
    <div style={{ marginBottom: 12 }}>
      <div className="eyebrow" style={{ fontSize: 10, marginBottom: 6, color: 'var(--warn)' }}>◆ {t('demo.heading')}</div>
      <div style={{ display: 'flex', gap: 8 }}>
        <button
          onClick={() => run('collision')}
          disabled={simulating}
          title={t('demo.collisionTip')}
          style={demoButtonStyle(simulating)}
        >
          {t('demo.collision')}
        </button>
        <button
          onClick={() => run('proximity')}
          disabled={simulating}
          title={t('demo.proximityTip')}
          style={demoButtonStyle(simulating)}
        >
          {t('demo.proximity')}
        </button>
      </div>
      <div style={{ fontSize: 10, color: 'var(--text-secondary)', marginTop: 4 }}>
        {t('demo.note')}
      </div>
      <div className="eyebrow" style={{ fontSize: 10, marginTop: 8, marginBottom: 4, color: 'var(--warn)' }} title={t('demo.historyTip')}>
        ◆ {t('demo.historyHeading')}
      </div>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <button
          onClick={() => run('collision', { historyStep: 1 })}
          disabled={simulating}
          title={t('demo.historyTip')}
          style={{ ...demoButtonStyle(simulating), textTransform: 'none', letterSpacing: 0, fontSize: 10 }}
        >
          {t('demo.historyStep1')}
        </button>
        {showStep2 && (
          <button
            onClick={() => run('collision', { historyStep: 2 })}
            disabled={simulating}
            title={t('demo.historyTip')}
            style={{ ...demoButtonStyle(simulating), textTransform: 'none', letterSpacing: 0, fontSize: 10 }}
          >
            {t('demo.historyStep2')} ▸
          </button>
        )}
      </div>
      {simulating && (
        <div className="mono" style={{ fontSize: 11, color: 'var(--warn)', marginTop: 6 }}>{t(stageLabel)}</div>
      )}
      {error && (
        <div style={{ marginTop: 8, padding: 8, borderRadius: 6, border: '1px solid var(--critical)' }}>
          <div className="mono" style={{ fontSize: 11, fontWeight: 700, color: 'var(--critical)' }}>
            {t('demo.failed')}
          </div>
          <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 4 }}>{error}</div>
          <button
            onClick={() => run(lastRun.mode, lastRun.opts)}
            style={{
              marginTop: 6, padding: '4px 10px', borderRadius: 6, fontSize: 10, fontWeight: 600,
              letterSpacing: '0.06em', textTransform: 'uppercase',
              background: 'transparent', border: '1px solid var(--text-secondary)', color: 'var(--text-secondary)',
              cursor: 'pointer',
            }}
          >
            {t('common.retry')}
          </button>
        </div>
      )}
    </div>
  )
}

function EmptyState({ onRunScreening, refreshing, error }) {
  const { t, tip } = useI18n()
  const { caps, deniedVars } = useAuth()
  const deniedTip = caps.refresh ? undefined : t('auth.denied', { ...deniedVars(PERMISSIONS.REFRESH), perm: t('perm.refresh') })
  return (
    <div style={{ textAlign: 'center', padding: '48px 16px', color: 'var(--text-secondary)' }}>
      <div style={{
        width: 56, height: 56, margin: '0 auto 16px', borderRadius: '50%',
        background: 'conic-gradient(var(--accent-dim), transparent 60%)',
        animation: 'spin 3s linear infinite',
      }} />
      <div style={{ color: 'var(--text-primary)', fontWeight: 600, marginBottom: 6 }}>{t('header.noEvents')}</div>
      <div style={{ fontSize: 12, marginBottom: 16 }}>{t('feed.emptyBody')}</div>
      <button
        onClick={onRunScreening}
        disabled={refreshing || !caps.refresh}
        title={deniedTip}
        style={{
          background: 'var(--accent)', color: '#08131f', border: 'none', borderRadius: 6,
          padding: '8px 16px', fontWeight: 600, cursor: refreshing ? 'default' : caps.refresh ? 'pointer' : 'not-allowed',
          opacity: refreshing || !caps.refresh ? 0.5 : 1,
        }}
      >
        {refreshing ? t('refresh.screening') : t('refresh.screenButton')}
      </button>
      {deniedTip && <div style={{ fontSize: 11, marginTop: 8 }}>{deniedTip}</div>}
      {refreshing && (
        <div style={{ fontSize: 11, marginTop: 10 }} title={tip(t('refresh.note'))}>
          {t('refresh.note')}
        </div>
      )}
      {error && (
        <div className="mono" style={{ fontSize: 11, color: 'var(--critical)', marginTop: 10 }}>{t('refresh.failed')} — {error}</div>
      )}
    </div>
  )
}

export default function EventFeed({ events, selectedEvent, onSelectEvent, objectsById, onRefresh, onSimulate, status }) {
  const { t } = useI18n()
  const { caps, deniedVars } = useAuth()
  const { scale } = useUiScale()
  const [refreshing, setRefreshing] = useState(false)
  const [refreshError, setRefreshError] = useState(null)
  const seenIds = useRef(new Set())
  const [newIds, setNewIds] = useState(new Set())

  useEffect(() => {
    const fresh = new Set()
    for (const e of events) {
      if (!seenIds.current.has(e.id)) fresh.add(e.id)
      seenIds.current.add(e.id)
    }
    if (fresh.size > 0) {
      setNewIds(fresh)
      const t = setTimeout(() => setNewIds(new Set()), 600)
      return () => clearTimeout(t)
    }
  }, [events])

  const handleRunScreening = async () => {
    setRefreshing(true)
    setRefreshError(null)
    try {
      await onRefresh()
    } catch (err) {
      setRefreshError(refreshErrorText(err, t) || t('refresh.failedGeneric'))
    } finally {
      setRefreshing(false)
    }
  }

  // List rows may not carry `status` (older backend) -- only claim "PENDING"
  // when we can actually count pending rows.
  const hasStatus = events.some((e) => e.status != null)
  const countLabel = hasStatus
    ? t('feed.pending', { count: events.filter((e) => e.status === 'pending').length })
    : (events.length === 1 ? t('feed.eventsOne') : t('feed.eventsMany', { count: events.length }))

  return (
    <div style={{ ...panelStyle, zoom: scale }}>
      <style>{`
        @keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
        @keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
      `}</style>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
        <span className="eyebrow">{t('feed.title')}</span>
        <span className="mono" style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{countLabel}</span>
      </div>
      {onSimulate && caps.demo && <DemoControl onSimulate={onSimulate} demoActive={status?.mode === 'demo'} />}
      {onSimulate && !caps.demo && (
        // Demo scenarios need the demo permission: one muted line instead of
        // the controls (the backend refuses the call for this role anyway).
        <div className="mono" style={{ fontSize: 10, color: 'var(--text-secondary)', marginBottom: 10 }}
          title={t('auth.denied', { ...deniedVars(PERMISSIONS.DEMO), perm: t('perm.demo') })}>
          ◆ {t('demo.heading')} · {t('auth.denied', { ...deniedVars(PERMISSIONS.DEMO), perm: t('perm.demo') })}
        </div>
      )}
      <div style={{ overflowY: 'auto', flex: 1 }}>
        {events.length === 0 ? (
          <EmptyState onRunScreening={handleRunScreening} refreshing={refreshing} error={refreshError} />
        ) : (
          events.map((e) => (
            <EventRow
              key={e.id}
              event={e}
              selected={selectedEvent?.id === e.id}
              isNew={newIds.has(e.id)}
              onSelect={onSelectEvent}
              objectsById={objectsById}
            />
          ))
        )}
      </div>
    </div>
  )
}

const panelStyle = {
  position: 'absolute',
  top: 64, left: 16, bottom: 16, width: 340,
  background: 'var(--bg-panel)',
  backdropFilter: 'blur(8px)',
  WebkitBackdropFilter: 'blur(8px)',
  border: '1px solid var(--border-line)',
  borderRadius: 10,
  padding: 16,
  display: 'flex',
  flexDirection: 'column',
  minHeight: 0,
  overflow: 'hidden', // the list inside scrolls; the panel never grows off-screen
  zIndex: 10,
}
