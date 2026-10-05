import { useState } from 'react'
import { modeInfo, modeChipText, formatRefreshParts, formatUtc, screeningHorizon } from '../mode'
import { useI18n } from '../i18n'
import { useAuth } from '../authContext'
import { PERMISSIONS } from '../auth'
import { refreshErrorText, schedulerViewModel } from '../opsView'
import { useUiScale } from '../uiScaleContext'

// Criticality group labels are i18n keys (criticality.Tier1 ...).

// One label + value row, mission-control style (matches EventDetail's
// provenance fields).
function StatusField({ label, children }) {
  const { tip } = useI18n()
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, marginBottom: 4 }}>
      <span className="mono" style={{ fontSize: 11, color: 'var(--text-secondary)' }} title={tip(label)}>{label}</span>
      <span className="mono" style={{ fontSize: 12, textAlign: 'right' }}>{children}</span>
    </div>
  )
}

const fmt = (v, suffix = '') => (v === null || v === undefined || v === '' ? 'N/A' : `${v}${suffix}`)

function RefreshButton({ onRefresh, label, small }) {
  const { t, tip } = useI18n()
  const { caps, deniedVars } = useAuth()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  // Without the refresh permission the button stays visible but disabled,
  // with a tooltip naming the missing permission (backend enforces it too).
  const allowed = caps.refresh
  const deniedTip = allowed ? undefined : t('auth.denied', { ...deniedVars(PERMISSIONS.REFRESH), perm: t('perm.refresh') })
  const run = async () => {
    if (!onRefresh || busy || !allowed) return
    setBusy(true)
    setError(null)
    try {
      await onRefresh()
    } catch (err) {
      setError(refreshErrorText(err, t) || t('refresh.failedGeneric'))
    } finally {
      setBusy(false)
    }
  }
  return (
    <div style={{ marginTop: 8 }}>
      <button
        onClick={run}
        disabled={busy || !allowed}
        title={deniedTip}
        aria-disabled={!allowed || undefined}
        style={{
          width: small ? 'auto' : '100%',
          background: small ? 'transparent' : 'var(--accent)',
          color: small ? 'var(--accent)' : '#08131f',
          border: small ? '1px solid var(--accent-dim)' : 'none',
          borderRadius: 6, padding: small ? '4px 10px' : '7px 10px',
          fontSize: small ? 10 : 11, fontWeight: 700, letterSpacing: '0.06em',
          cursor: busy ? 'default' : !allowed ? 'not-allowed' : 'pointer', opacity: busy || !allowed ? 0.5 : 1,
        }}
      >
        {busy ? t('refresh.busy') : label}
      </button>
      {!allowed && (
        <div style={{ fontSize: 10, color: 'var(--text-secondary)', marginTop: 4 }}>{deniedTip}</div>
      )}
      {busy && (
        <div style={{ fontSize: 10, color: 'var(--text-secondary)', marginTop: 4 }} title={tip(t('refresh.note'))}>
          {t('refresh.note')}
        </div>
      )}
      {error && (
        <div className="mono" style={{ fontSize: 10, color: 'var(--critical)', marginTop: 4 }}>
          {t('refresh.failed')} — {error}
        </div>
      )}
    </div>
  )
}

// System-wide data status: which mode the dashboard is in (LIVE / CACHED /
// STALE / DEMO / NOT SCREENED), where the working catalog came from, how
// fresh it is, and the current screening output. Status is polled once in
// App and passed down. Never reads LIVE unless status.mode === 'live'.
// Small link that reopens the "What changed since last refresh?" card.
function DeltaLink({ onShowDelta }) {
  const { t } = useI18n()
  if (!onShowDelta) return null
  return (
    <div style={{ textAlign: 'right', marginBottom: 4 }}>
      <button
        type="button"
        onClick={onShowDelta}
        title={t('delta.openTip')}
        style={{
          background: 'transparent', border: 'none', padding: 0, cursor: 'pointer',
          color: 'var(--accent)', fontSize: 10, textDecoration: 'underline',
        }}
      >
        {t('delta.open')}
      </button>
    </div>
  )
}

// Periodic public orbital-data refresh (backend scheduler). Every value comes
// from /api/status `scheduler`; status words are text + colour. Older
// backends without `scheduler` render nothing here.
function SchedulerBlock({ status }) {
  const { t } = useI18n()
  const vm = schedulerViewModel(status)
  if (!vm) return null
  const small = { fontSize: 10, color: 'var(--text-secondary)' }
  const a = vm.lastAttempt
  return (
    <div style={{ margin: '6px 0', padding: '6px 0', borderTop: '1px dashed var(--border-line)', borderBottom: '1px dashed var(--border-line)' }}>
      <div style={{ ...small, marginBottom: 4 }}>{t('sched.descriptor')}</div>
      <StatusField label={t('sched.auto')}>
        <span style={{ color: vm.enabled ? 'var(--safe)' : 'var(--warn)' }}>{vm.enabled ? t('sched.enabled') : t('sched.disabled')}</span>
        {vm.modeKey && <div style={small}>{t(vm.modeKey)}</div>}
      </StatusField>
      {vm.cadence && (
        <StatusField label={t('sched.cadence')}>{vm.cadence.key ? t(vm.cadence.key, vm.cadence.vars) : vm.cadence.text}</StatusField>
      )}
      <StatusField label={t('sched.lastAttempt')}>
        {a ? (
          <>
            {a.at ? formatUtc(a.at) : 'N/A'}
            {(a.statusText || a.triggerKey) && (
              <div style={{ fontSize: 11 }}>
                {a.statusText && <span style={{ color: a.tone, fontWeight: 700 }}>{a.statusKey ? t(a.statusKey) : a.statusText}</span>}
                {a.triggerKey && <span style={{ color: 'var(--text-secondary)' }}> ({t(a.triggerKey)})</span>}
              </div>
            )}
            {a.reason && <div style={{ ...small, whiteSpace: 'normal', overflowWrap: 'anywhere' }}>{t('sched.reason', { reason: a.reason })}</div>}
          </>
        ) : 'N/A'}
      </StatusField>
      <StatusField label={t('sched.next')}>
        {vm.nextAt ? formatUtc(vm.nextAt) : t('sched.nextNone')}
      </StatusField>
    </div>
  )
}

function DataStatus({ status, onRefresh, onShowDelta }) {
  const { t } = useI18n()
  if (!status) {
    return (
      <div style={{ marginBottom: 16, paddingBottom: 12, borderBottom: '1px solid var(--border-line)' }}>
        <div className="eyebrow" style={{ fontSize: 10, color: 'var(--text-secondary)' }}>{t('asset.statusUnavailable')}</div>
        <RefreshButton onRefresh={onRefresh} label={t('refresh.button')} small />
      </div>
    )
  }

  const info = modeInfo(status)
  const ingested = status.objects_ingested || {}
  const age = status.tle_age_days || {}
  const ageText = age.avg != null ? t('asset.ageValue', { min: fmt(age.min), max: fmt(age.max), avg: age.avg }) : 'N/A'
  const run = status.latest_run && typeof status.latest_run === 'object' ? status.latest_run : null
  const total = status.object_count ?? ((ingested.satellite ?? 0) + (ingested.debris ?? 0) + (ingested.foreign_sat ?? 0))
  // Completion time of the latest SUCCESSFUL ingest (failed attempts never
  // count); a later refused/failed attempt is shown on its own line.
  const lastRefresh = status.last_refresh_at
  const refreshParts = formatRefreshParts(lastRefresh)
  const failure = status.ingest?.last_failure
  const lastFailure = failure?.at && (!lastRefresh || String(failure.at) > String(lastRefresh)) ? failure : null
  const horizon = screeningHorizon(status)
  const nextRunHours = horizon.nextRunHours
  const candidatePairs = run?.candidate_pairs ?? status.conjunction_candidates
  const collisionEvents = run?.collision_events
  const proximityEvents = run?.proximity_events ?? status.proximity_candidates
  const warnings = Array.isArray(status.warnings) ? status.warnings : []
  const degraded = info.mode === 'cached' || info.mode === 'stale' || info.mode === 'not_screened'
  const demo = status.demo_scenario && typeof status.demo_scenario === 'object' ? status.demo_scenario : null
  const adj = demo?.adjusted_name || demo?.adjusted_object
  const demoObject = adj && typeof adj === 'object' ? (adj.name || adj.norad_id) : adj

  return (
    <div style={{ marginBottom: 16, paddingBottom: 12, borderBottom: '1px solid var(--border-line)' }}>
      <div className="eyebrow" style={{ fontSize: 10, color: info.color, marginBottom: 8 }}>
        {modeChipText(info, t)}
        {status.mode_label && String(status.mode_label).toUpperCase() !== info.label && (
          <span style={{ color: 'var(--text-secondary)', textTransform: 'none', letterSpacing: 0 }}> · {status.mode_label}</span>
        )}
      </div>
      <StatusField label={t('asset.source')}>{fmt(status.source)}</StatusField>
      <StatusField label={t('asset.lastRefresh')}>
        {refreshParts.utc || 'N/A'}
        {refreshParts.local && <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{refreshParts.local}</div>}
      </StatusField>
      {lastFailure && (
        <div className="mono" style={{ fontSize: 11, color: 'var(--warn)', marginBottom: 4, textAlign: 'right' }} title={lastFailure.reason || undefined}>
          {t('asset.lastFailed', { at: formatUtc(lastFailure.at) })}
        </div>
      )}
      <SchedulerBlock status={status} />
      <DeltaLink onShowDelta={onShowDelta} />
      {status.last_refresh_used_cache === true && (
        <div className="mono" style={{ fontSize: 10, color: 'var(--warn)', marginBottom: 4, textAlign: 'right' }}>
          {t('asset.usedCache')}
        </div>
      )}
      <StatusField label={t('asset.ingested')}>{fmt(total)}</StatusField>
      <div className="mono" style={{ fontSize: 10, color: 'var(--text-secondary)', textAlign: 'right', marginBottom: 4 }}>
        {t('asset.breakdown', { sat: ingested.satellite ?? 0, debris: ingested.debris ?? 0, foreign: ingested.foreign_sat ?? 0 })}
      </div>
      <StatusField label={t('asset.tleAge')}>{ageText}</StatusField>
      <StatusField label={t('asset.window')}>
        {horizon.hours != null || horizon.step != null
          ? t('asset.windowValue', { h: horizon.hours ?? 'N/A', s: horizon.step ?? 'N/A' })
          : 'N/A'}
        {nextRunHours != null && (
          <div style={{ fontSize: 10, color: 'var(--accent)' }}>{t('sched.nextRunUses', { h: nextRunHours })}</div>
        )}
      </StatusField>
      <StatusField label={t('asset.pairs')}>{fmt(candidatePairs)}</StatusField>
      <StatusField label={t('asset.collisionEvents')}>{fmt(collisionEvents)}</StatusField>
      <StatusField label={t('asset.proximityEvents')}>{fmt(proximityEvents)}</StatusField>
      {status.objects_excluded_stale > 0 && (
        <StatusField label={t('asset.excluded', { days: fmt(status.max_tle_age_days) })}>{status.objects_excluded_stale}</StatusField>
      )}

      {info.demo && (
        <div style={{ marginTop: 8, padding: 8, borderRadius: 6, border: '1px solid var(--warn)', background: 'color-mix(in srgb, var(--warn) 10%, transparent)' }}>
          <div style={{ fontSize: 11, color: 'var(--warn)', lineHeight: 1.45 }}>
            <b>◆ {t('mode.demo')}</b> — {t('asset.demoBody', { obj: demoObject ? ` (${demoObject})` : '' })}
          </div>
          <RefreshButton onRefresh={onRefresh} label={t('refresh.exitDemo')} />
        </div>
      )}

      {degraded && (
        <div style={{ marginTop: 8, padding: 8, borderRadius: 6, border: '1px solid var(--warn)' }}>
          {warnings.length > 0 && (
            <ul style={{ margin: '0 0 6px', paddingLeft: 16, fontSize: 11, color: 'var(--warn)' }}>
              {warnings.map((w, i) => <li key={i}>{String(w)}</li>)}
            </ul>
          )}
          <div style={{ fontSize: 11, color: 'var(--text-secondary)', lineHeight: 1.45 }}>
            {t('asset.degradedNote')}
          </div>
          <RefreshButton onRefresh={onRefresh} label={t('refresh.button')} />
        </div>
      )}

      {!degraded && !info.demo && warnings.length > 0 && (
        <ul style={{ margin: '6px 0 0', paddingLeft: 16, fontSize: 10, color: 'var(--warn)' }}>
          {warnings.map((w, i) => <li key={i}>{String(w)}</li>)}
        </ul>
      )}

      {!degraded && !info.demo && (
        <RefreshButton onRefresh={onRefresh} label={t('refresh.button')} small />
      )}
    </div>
  )
}

const TIER_COLOR = {
  Critical: 'var(--critical)',
  High: 'var(--warn)',
  Medium: 'var(--medium)',
  Low: 'var(--safe)',
}

export default function AssetPanel({ objects, events, status, onRefresh, onSelectAsset, onShowDelta, onOpenCatalog }) {
  const { t } = useI18n()
  const { scale } = useUiScale()
  const assets = objects.filter((o) => o.object_type === 'satellite' && o.criticality)
  const eventTierByAsset = {}
  for (const e of events) {
    if (e.risk_tier === 'Critical' || !eventTierByAsset[e.object_a_id]) {
      eventTierByAsset[e.object_a_id] = e.risk_tier
    }
  }

  const groups = ['Tier1', 'Tier2', 'Tier3'].map((tier) => ({
    tier,
    label: t(`criticality.${tier}`),
    items: assets.filter((a) => a.criticality === tier),
  }))

  return (
    <div style={{ ...panelStyle, zoom: scale }}>
      <div style={{ overflowY: 'auto', flex: 1 }}>
        <DataStatus status={status} onRefresh={onRefresh} onShowDelta={onShowDelta} />
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 8, marginBottom: 12 }}>
          <span className="eyebrow">{t('asset.title')}</span>
          {onOpenCatalog && (
            <button type="button" onClick={onOpenCatalog} title={t('catalog.openTip')}
              style={{ background: 'none', border: 'none', color: 'var(--accent)', fontSize: 10, cursor: 'pointer', textDecoration: 'underline', padding: 0 }}>
              {t('catalog.open')}
            </button>
          )}
        </div>
        {groups.map((g) => (
          g.items.length === 0 ? null : (
            <div key={g.tier} style={{ marginBottom: 16 }}>
              <div className="eyebrow" style={{ fontSize: 10, marginBottom: 8 }}>{g.label}</div>
              {g.items.map((a) => {
                const pendingTier = eventTierByAsset[a.norad_id]
                const dotColor = pendingTier ? TIER_COLOR[pendingTier] : 'var(--safe)'
                const pulse = pendingTier === 'Critical'
                return (
                  <div
                    key={a.norad_id}
                    onClick={() => onSelectAsset && onSelectAsset(a)}
                    style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 0', cursor: 'pointer' }}
                  >
                    <span style={{
                      width: 8, height: 8, borderRadius: '50%', background: dotColor, flexShrink: 0,
                      animation: pulse ? 'assetPulse 2s ease-in-out infinite' : undefined,
                    }} />
                    <span style={{ fontSize: 13, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{a.name}</span>
                    <span className="mono" style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{a.norad_id}</span>
                  </div>
                )
              })}
            </div>
          )
        ))}
      </div>
      <style>{`
        @keyframes assetPulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }
      `}</style>
    </div>
  )
}

const panelStyle = {
  position: 'absolute',
  top: 64, right: 16, bottom: 16, width: 280,
  background: 'var(--bg-panel)',
  backdropFilter: 'blur(8px)',
  WebkitBackdropFilter: 'blur(8px)',
  border: '1px solid var(--border-line)',
  borderRadius: 10,
  padding: 16,
  display: 'flex',
  flexDirection: 'column',
  minHeight: 0,
  overflow: 'hidden', // content area scrolls
  zIndex: 10,
}
