import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useI18n } from '../i18n'
import { useUiScale } from '../uiScaleContext'

// "What changed since last refresh?" -- dismissible card under the header
// (view model: ../screeningDelta.js). A demo-domain comparison always carries
// the demo badge and banner and is never labelled as live data.
//
// Never hidden: when the event drawer is open and there is not enough height
// for both (short screen + large text), the card collapses to a one-line pill
// ("What changed? · counts · Details") that expands over the drawer with
// internal scrolling. A sticky "More below" cue marks content past the fold.

function ItemRow({ item, onSelectEvent, canSelect }) {
  const { t, tip } = useI18n()
  const selectable = canSelect && item.eventId != null
  const tcaText = t('delta.tca', item.tca)
  return (
    <div
      role={selectable ? 'button' : undefined}
      tabIndex={selectable ? 0 : undefined}
      onClick={selectable ? () => onSelectEvent(item.eventId) : undefined}
      onKeyDown={selectable ? (e) => { if (e.key === 'Enter') onSelectEvent(item.eventId) } : undefined}
      style={{
        padding: '5px 8px', marginBottom: 3, borderRadius: 6, background: 'rgba(11, 16, 32, 0.5)',
        cursor: selectable ? 'pointer' : 'default',
      }}
    >
      <div style={{ fontSize: 12, overflowWrap: 'anywhere' }}>
        {item.names}
        {item.isDemo && <span className="mono" style={{ fontSize: 11, color: 'var(--warn)' }}> · ◆ {t('common.demo')}</span>}
      </div>
      <div className="mono" style={{ fontSize: 11, color: 'var(--text-secondary)', display: 'flex', flexWrap: 'wrap', columnGap: 10 }}>
        <span>{t('delta.miss', item.miss)}</span>
        <span title={tip(tcaText)}>{tcaText}</span>
        <span>{t('delta.tier', item.tier)}</span>
        {item.pc && (
          <span title={tip('Pc')}>
            Pc {item.pc.old} → {item.pc.new}
            {item.pcRatio && <span title={t('delta.pcRatioTip')}> (×{item.pcRatio})</span>}
          </span>
        )}
        {item.tcaShift !== null && <span>{t('evo.tcaShift', { v: item.tcaShift })}</span>}
        {item.missTrendKey && <span>{t(item.missTrendKey)}</span>}
      </div>
    </div>
  )
}

function useOverflowCue(deps) {
  const ref = useRef(null)
  const [more, setMore] = useState(false)
  const check = useCallback(() => {
    const el = ref.current
    if (!el) return
    setMore(el.scrollHeight - el.clientHeight - el.scrollTop > 6)
  }, [])
  useLayoutEffect(check, [check, ...deps]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const el = ref.current
    if (!el || typeof ResizeObserver === 'undefined') return undefined
    const ro = new ResizeObserver(check)
    ro.observe(el)
    return () => ro.disconnect()
  })
  const scrollMore = () => {
    const el = ref.current
    if (el) el.scrollBy({ top: Math.max(60, el.clientHeight * 0.7), behavior: 'smooth' })
  }
  return { ref, more, check, scrollMore }
}

function Badge({ vm }) {
  const { t } = useI18n()
  if (!vm) return null
  const badgeColor = vm.demo ? 'var(--warn)' : 'var(--text-secondary)'
  return (
    <span className="mono" style={{ fontSize: 11, marginLeft: 8, padding: '1px 8px', borderRadius: 10, border: `1px solid ${badgeColor}`, color: badgeColor, whiteSpace: 'nowrap' }}>
      {vm.demo ? '◆ ' : '● '}{t(vm.badgeKey)}
    </span>
  )
}

const closeBtn = { background: 'none', border: 'none', color: 'var(--text-secondary)', fontSize: 16, cursor: 'pointer', lineHeight: 1, flexShrink: 0 }

// One-line collapsed form (never hides the counts or the demo label).
function DeltaPill({ vm, error, onExpand, onClose }) {
  const { t } = useI18n()
  const { scale, layout } = useUiScale()
  const counts = vm?.state === 'ok'
    ? vm.groups.map((g) => `${t(g.labelKey)} ${g.count}`).join(' · ')
    : vm?.state === 'none' ? t(vm.reasonKey) : error ? t('delta.unavailable') : t('common.loading')
  return (
    <div
      role="region"
      aria-label={t('delta.title')}
      data-delta-mode="pill"
      style={{
        position: 'absolute', ...layout.deltaCard, marginLeft: 'auto', marginRight: 'auto',
        height: layout.deltaPillHeight, boxSizing: 'border-box', zoom: scale, zIndex: 36,
        display: 'flex', alignItems: 'center', gap: 8,
        background: 'var(--bg-panel-solid)', border: `1px solid ${vm?.demo ? 'var(--warn)' : 'var(--accent-dim)'}`,
        borderRadius: 20, padding: '0 10px 0 14px',
      }}
    >
      <button
        type="button"
        onClick={onExpand}
        aria-expanded="false"
        title={`${t('delta.title')} — ${counts}`}
        style={{
          flex: 1, minWidth: 0, display: 'flex', alignItems: 'center', gap: 8, background: 'none', border: 'none',
          padding: 0, cursor: 'pointer', color: 'var(--text-primary)', textAlign: 'left', font: 'inherit',
        }}
      >
        <span className="eyebrow" style={{ color: vm?.demo ? 'var(--warn)' : 'var(--accent)', whiteSpace: 'nowrap', flexShrink: 0 }}>
          {vm?.demo ? `◆ ${t('common.demo')} · ` : ''}{t('delta.open')}
        </span>
        <span className="mono" style={{ fontSize: 11, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: 'var(--text-secondary)' }}>
          {counts}
        </span>
        <span style={{ color: 'var(--accent)', fontSize: 11, flexShrink: 0, whiteSpace: 'nowrap', marginLeft: 'auto' }}>{t('delta.details')} ▸</span>
      </button>
      <button onClick={onClose} aria-label={t('delta.dismiss')} title={t('delta.dismiss')} style={closeBtn}>✕</button>
    </div>
  )
}

export default function ScreeningDeltaCard({ vm, error, onClose, onSelectEvent, eventIds, drawerOpen, expanded = false, onExpandedChange }) {
  const { t } = useI18n()
  const { scale, layout } = useUiScale()
  const [open, setOpen] = useState(false)
  const ids = eventIds instanceof Set ? eventIds : new Set()
  const pillMode = Boolean(drawerOpen) && layout.deltaModeWithDrawer === 'pill'
  const isExpanded = pillMode && expanded
  // Leaving pill mode (drawer closed / more room) drops the expanded state.
  useEffect(() => {
    if (!pillMode && expanded && onExpandedChange) onExpandedChange(false)
  }, [pillMode, expanded, onExpandedChange])
  const showDetails = open || isExpanded
  const { ref, more, check, scrollMore } = useOverflowCue([vm, error, showDetails, pillMode, isExpanded, scale])

  if (pillMode && !expanded) {
    return <DeltaPill vm={vm} error={error} onExpand={() => onExpandedChange && onExpandedChange(true)} onClose={onClose} />
  }

  const maxHeight = isExpanded
    ? layout.deltaExpandedMaxHeight
    : drawerOpen ? layout.deltaMaxHeightWithDrawer : layout.deltaMaxHeight
  return (
    <div
      ref={ref}
      onScroll={check}
      role="region"
      aria-label={t('delta.title')}
      data-delta-mode={isExpanded ? 'expanded' : 'card'}
      style={{
        position: 'absolute', ...layout.deltaCard, marginLeft: 'auto', marginRight: 'auto',
        maxHeight, zoom: scale, zIndex: isExpanded ? 37 : 34, overflowY: 'auto',
        background: 'var(--bg-panel-solid)', border: `1px solid ${vm?.demo ? 'var(--warn)' : 'var(--border-line)'}`,
        borderRadius: 10, padding: '10px 14px', animation: 'slideUp 200ms ease-out',
        boxShadow: isExpanded ? '0 8px 24px rgba(0, 0, 0, 0.5)' : undefined,
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 8 }}>
        <div style={{ minWidth: 0 }}>
          <span className="eyebrow" style={{ color: 'var(--accent)' }}>{t('delta.title')}</span>
          <Badge vm={vm} />
          {vm && vm.latestRunId != null && (
            <div className="mono" style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 3, lineHeight: 1.5 }}>
              <div style={{ color: 'var(--text-primary)' }}>
                {t('delta.currentRun', { mode: vm.latestMode || '', id: vm.latestRunId, at: vm.latestStartedAt || '—' })}
              </div>
              {vm.previousRunId != null && (
                <div>{t('delta.previousRun', { mode: vm.previousMode || vm.latestMode || '', id: vm.previousRunId, at: vm.previousStartedAt || '—' })}</div>
              )}
              <div>{t(vm.domainNoteKey)}</div>
              {vm.between.length > 0 && (
                <div title={t('delta.betweenTip')}>
                  {t('delta.between', {
                    list: vm.between.map((r) => `#${r.id} ${r.mode}${r.recorded ? '' : ` (${t('delta.notRecorded')})`}`).join(', '),
                  })}
                </div>
              )}
            </div>
          )}
        </div>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexShrink: 0 }}>
          {isExpanded && (
            <button onClick={() => onExpandedChange && onExpandedChange(false)} aria-expanded="true"
              aria-label={t('delta.hide')} title={t('delta.hide')}
              style={{ ...closeBtn, fontSize: 14, color: 'var(--accent)' }}>
              ▴
            </button>
          )}
          <button onClick={onClose} aria-label={t('delta.dismiss')} title={t('delta.dismiss')} style={closeBtn}>
            ✕
          </button>
        </div>
      </div>

      {vm?.demo && (
        <div style={{
          marginTop: 6, padding: '4px 8px', borderRadius: 6, fontSize: 11, color: 'var(--warn)',
          border: '1px solid var(--warn)', background: 'color-mix(in srgb, var(--warn) 12%, transparent)',
        }}>
          ◆ {t('delta.demoBanner')}
        </div>
      )}

      {!vm && error && <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 6 }}>{t('delta.unavailable')}</div>}
      {!vm && !error && <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 6 }}>{t('common.loading')}</div>}
      {vm?.state === 'none' && (
        <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 6 }}>{t(vm.reasonKey)}</div>
      )}

      {vm?.state === 'ok' && (
        <>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 8, alignItems: 'center' }}>
            {vm.groups.map((g) => (
              <span key={g.key} className="mono" style={{
                fontSize: 11, padding: '2px 8px', borderRadius: 10, border: '1px solid var(--border-line)',
                color: g.count > 0 ? 'var(--text-primary)' : 'var(--text-secondary)',
              }}>
                {t(g.labelKey)} <b>{g.count}</b>
              </span>
            ))}
            {vm.hasItems && !isExpanded && (
              <button onClick={() => setOpen((o) => !o)} style={{
                background: 'transparent', color: 'var(--accent)', border: '1px solid var(--accent-dim)',
                borderRadius: 6, padding: '2px 8px', fontSize: 11, cursor: 'pointer',
              }}>
                {open ? `${t('delta.hide')} ▾` : `${t('delta.details')} ▸`}
              </button>
            )}
          </div>
          {showDetails && vm.groups.map((g) => (g.items.length === 0 ? null : (
            <div key={g.key} style={{ marginTop: 8 }}>
              <div className="mono" style={{ fontSize: 11, color: 'var(--text-secondary)', marginBottom: 3 }}>
                {t(g.labelKey)} ({g.count})
              </div>
              {g.items.map((it) => (
                <ItemRow key={it.key} item={it} onSelectEvent={onSelectEvent} canSelect={ids.has(it.eventId)} />
              ))}
            </div>
          )))}
        </>
      )}
      {more && (
        <div className="delta-more" role="button" tabIndex={0} onClick={scrollMore}
          onKeyDown={(e) => { if (e.key === 'Enter') scrollMore() }}>
          {t('delta.more')}
        </div>
      )}
    </div>
  )
}
