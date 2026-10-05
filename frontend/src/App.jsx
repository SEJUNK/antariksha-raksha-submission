import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import GlobeView from './components/GlobeView'
import GlobeErrorBoundary from './components/GlobeErrorBoundary'
import EventFeed from './components/EventFeed'
import EventDetail from './components/EventDetail'
import AssetPanel from './components/AssetPanel'
import AnalyticsPanel from './components/AnalyticsPanel'
import CatalogPanel from './components/CatalogPanel'
import AdminPanel from './components/AdminPanel'
import { useAuth } from './authContext'
import { permLabelKey, roleLabelKey, userIndicator } from './auth'
import { headerAiStatus } from './eventTrust'
import { adminSections } from './opsView'
import ScreeningDeltaCard from './components/ScreeningDeltaCard'
import { deltaViewModel, shouldAutoShow } from './screeningDelta'
import { api } from './api'
import { modeInfo, modeChipText } from './mode'
import { useI18n } from './i18n'
import { tierLabelKey } from './i18n/core'
import { useUiScale } from './uiScaleContext'
import { HEADER_MAX_COMPACT, nextHeaderCompact, shortEventChip } from './uiScale'
import { LanguageSelector, TextSizeControl } from './components/HeaderControls'
import { BRIEF_POLL_INTERVAL_MS, BRIEF_POLL_MAX_TRIES, shouldPollBrief } from './briefState'

// Two-tone console ping via Web Audio (no assets, no deps). Browsers allow
// audio only after the first user gesture; before that this silently no-ops.
function playAlertPing() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)()
    const gain = ctx.createGain()
    gain.gain.setValueAtTime(0.12, ctx.currentTime)
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.7)
    gain.connect(ctx.destination)
    ;[880, 1174.7].forEach((freq, i) => {
      const osc = ctx.createOscillator()
      osc.type = 'sine'
      osc.frequency.value = freq
      osc.connect(gain)
      osc.start(ctx.currentTime + i * 0.18)
      osc.stop(ctx.currentTime + i * 0.18 + 0.32)
    })
    setTimeout(() => ctx.close(), 1200)
  } catch {
    /* audio unavailable -- alerts remain visual-only */
  }
}

function useUtcClock() {
  const [time, setTime] = useState(() => new Date().toISOString().slice(11, 19) + ' UTC')
  useEffect(() => {
    const id = setInterval(() => setTime(new Date().toISOString().slice(11, 19) + ' UTC'), 1000)
    return () => clearInterval(id)
  }, [])
  return time
}

const TIER_RANK = { Critical: 3, High: 2, Medium: 1, Low: 0 }
const TIER_CHIP_COLOR = {
  Critical: 'var(--critical)', High: 'var(--warn)',
  Medium: 'var(--medium)', Low: 'var(--safe)',
}

// Neutral event summary -- an "event" is a screened close approach, not a
// hostile act, so no threat wording here.
function EventStatusChip({ events, isDemo }) {
  const { t } = useI18n()
  const demoSuffix = isDemo ? ` ${t('header.demoSuffix')}` : ''
  if (events.length === 0) {
    const full = `● ${t('header.noEvents')}${demoSuffix}`
    return (
      <span className="mono hdr-nowrap hdr-evchip" title={full} style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
        <span className="hdr-evchip-full">{full}</span>
        <span className="hdr-evchip-short">{shortEventChip(0)}</span>
      </span>
    )
  }
  const worst = events.reduce((a, b) => ((TIER_RANK[b.risk_tier] || 0) > (TIER_RANK[a.risk_tier] || 0) ? b : a))
  const color = TIER_CHIP_COLOR[worst.risk_tier] || 'var(--warn)'
  // Risk tier code stays in English (data value); translated word is a tooltip.
  const tierCode = String(worst.risk_tier || 'N/A').toUpperCase()
  const tierKey = tierLabelKey(worst.risk_tier)
  const full = `● ${events.length > 1
    ? t('header.eventsMany', { count: events.length, tier: tierCode })
    : t('header.eventsOne', { tier: tierCode })}${demoSuffix}`
  return (
    <span className="mono hdr-nowrap hdr-evchip" title={tierKey ? `${full} — ${worst.risk_tier}: ${t(tierKey)}` : full} style={{
      fontSize: 12, color, padding: '3px 10px', borderRadius: 10,
      border: `1px solid ${color}`,
      animation: worst.risk_tier === 'Critical' ? 'headerPulse 1.5s ease-in-out infinite' : undefined,
    }}>
      <span className="hdr-evchip-full">{full}</span>
      {/* Narrow header: count + tier code only (never an ellipsis). */}
      <span className="hdr-evchip-short">{shortEventChip(events.length, tierCode)}</span>
    </span>
  )
}

// Application-level data-mode chip (from /api/status). Never reads LIVE
// unless the backend explicitly reports mode === 'live'.
function ModeChip({ status }) {
  const { t } = useI18n()
  const info = modeInfo(status)
  const title = Array.isArray(status?.warnings) && status.warnings.length
    ? status.warnings.join(' | ')
    : (status?.mode_label || info.label)
  return (
    <span
      className="mono hdr-nowrap"
      title={title}
      style={{
        fontSize: 11, fontWeight: 600, color: info.color, padding: '3px 10px', borderRadius: 10,
        border: `1px solid ${info.color}`,
        background: info.demo ? 'color-mix(in srgb, var(--warn) 18%, transparent)' : 'transparent',
        letterSpacing: '0.06em',
      }}
    >
      {modeChipText(info, t)}
    </span>
  )
}

// Signed-in user: role code (Latin, data) + display name. At high header
// compaction only the role chip stays, with the name in its tooltip. The
// Admin entry appears with any administrative permission (manage_users,
// view_admin_audit or configure_system); sections inside are gated per
// permission string.
function UserControls({ showAdmin, onToggleAdmin }) {
  const { t } = useI18n()
  const { session, logout } = useAuth()
  const who = userIndicator(session)
  if (!who) return null
  const btn = (active) => ({
    background: active ? 'var(--accent-dim)' : 'transparent',
    border: '1px solid var(--border-line)', borderRadius: 6,
    color: active ? 'var(--accent)' : 'var(--text-secondary)',
    padding: '4px 8px', fontSize: 11, fontWeight: 600, letterSpacing: '0.06em', cursor: 'pointer', whiteSpace: 'nowrap',
  })
  const permList = (session?.permissions || []).map((p) => t(permLabelKey(p))).join(', ')
  const tipText = `${t('auth.signedInAs', { name: who.name, user: who.username })} — ${who.role}: ${t(roleLabelKey(who.role))}`
    + (permList ? ` · ${t('auth.permissions', { list: permList })}` : '')
  return (
    <span className="hdr-user" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
      <span className="mono hdr-nowrap" title={tipText} aria-label={tipText}
        style={{ fontSize: 11, color: 'var(--text-primary)', padding: '3px 8px', borderRadius: 10, border: '1px solid var(--accent-dim)' }}>
        <span style={{ color: 'var(--accent)', fontWeight: 600 }}>{who.role}</span>
        <span className="hdr-user-name"> · {who.name}</span>
      </span>
      {adminSections(session?.permissions).open && (
        <button type="button" onClick={onToggleAdmin} title={t('admin.open')} aria-label={t('admin.open')} style={btn(showAdmin)}>
          <span className="hdr-btn-label">{t('admin.users')}</span>
          <span className="hdr-btn-icon" aria-hidden="true">☷</span>
        </button>
      )}
      <button type="button" onClick={logout} title={t('auth.logout')} aria-label={t('auth.logout')} style={btn(false)}>
        <span className="hdr-btn-label">{t('auth.logout')}</span>
        <span className="hdr-btn-icon" aria-hidden="true">⏻</span>
      </button>
    </span>
  )
}

function HeaderBar({ health, status, events, showAnalytics, onToggleAnalytics, showAdmin, onToggleAdmin }) {
  const clock = useUtcClock()
  const { t, tip, lang } = useI18n()
  const { scale, layout } = useUiScale()
  const brandLocal = t('header.brandLocal')
  // Adaptive compaction: the width-based level plus extra steps while the
  // content still overflows (long labels in some languages / large text).
  // Reset on size, language, scale or mode change; re-measured every render.
  const barRef = useRef(null)
  const [extraCompact, setExtraCompact] = useState(0)
  const compact = Math.min(HEADER_MAX_COMPACT, layout.headerCompact + extraCompact)
  useLayoutEffect(() => { setExtraCompact(0) }, [lang, scale, layout.headerCompact, status?.mode])
  useLayoutEffect(() => {
    const el = barRef.current
    if (!el) return
    const next = nextHeaderCompact(compact, el.scrollWidth, el.clientWidth)
    if (next !== compact) setExtraCompact((e) => e + 1)
  })
  useEffect(() => {
    const el = barRef.current
    if (!el || typeof ResizeObserver === 'undefined') return undefined
    let lastW = el.clientWidth
    const ro = new ResizeObserver(() => {
      if (el.clientWidth !== lastW) {
        lastW = el.clientWidth
        setExtraCompact(0)
      }
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  // AI availability (from /api/health). An AI outage is shown as such -- the
  // computed physics evidence stays available either way.
  const aiStatus = headerAiStatus(health)
  const statusText = `● ${t(aiStatus ? aiStatus.key : 'header.systemNominal')}`
  const statusColor = aiStatus?.tone === 'warn' ? 'var(--warn)' : 'var(--safe)'

  return (
    <div ref={barRef} className="hdr-bar" data-compact={compact} style={{
      zoom: scale,
      position: 'absolute', top: 0, left: 0, right: 0, height: 48, zIndex: 30,
      background: 'var(--bg-panel-solid)',
      display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      padding: '0 20px',
      borderBottom: '2px solid transparent',
      borderImage: 'linear-gradient(90deg, var(--tricolor-saffron), white, var(--tricolor-green)) 1',
    }}>
      <div className="hdr-left" style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span className="hdr-dot" title="ANTARIKSHA-RAKSHA" style={{
          width: 10, height: 10, borderRadius: '50%', background: 'var(--accent)',
          animation: 'headerPulse 2s ease-in-out infinite',
        }} />
        <span className="hdr-nowrap hdr-brand" style={{ fontWeight: 700, letterSpacing: '0.08em' }}>ANTARIKSHA-RAKSHA</span>
        {brandLocal && <span className="hdr-brand-local hdr-nowrap">{brandLocal}</span>}
        <span className="hdr-subtitle hdr-nowrap" style={{ color: 'var(--text-secondary)' }} title={tip(t('header.subtitle'))}>&middot; {t('header.subtitle')}</span>
      </div>
      <div className="hdr-right" style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
        <ModeChip status={status} />
        <EventStatusChip events={events} isDemo={status?.mode === 'demo'} />
        <button
          onClick={onToggleAnalytics}
          title={t('header.analytics')}
          aria-label={t('header.analytics')}
          style={{
            background: showAnalytics ? 'var(--accent-dim)' : 'transparent',
            border: '1px solid var(--border-line)', borderRadius: 6,
            color: showAnalytics ? 'var(--accent)' : 'var(--text-secondary)',
            padding: '4px 10px', fontSize: 11, fontWeight: 600,
            letterSpacing: '0.08em', cursor: 'pointer', whiteSpace: 'nowrap',
          }}
        >
          <span className="hdr-analytics-label">{t('header.analytics')}</span>
          <span className="hdr-analytics-icon" aria-hidden="true">▦</span>
        </button>
        <LanguageSelector />
        <TextSizeControl />
        <span className="mono hdr-clock hdr-nowrap" style={{ fontSize: 13 }}>{clock}</span>
        <span className="mono hdr-sys hdr-nowrap" style={{ fontSize: 12, color: statusColor }} title={statusText}>
          <span className="hdr-sys-text">{statusText}</span>
          <span className="hdr-sys-dot" aria-label={statusText}>●</span>
        </span>
        <UserControls showAdmin={showAdmin} onToggleAdmin={onToggleAdmin} />
      </div>
    </div>
  )
}

export default function App() {
  const [objects, setObjects] = useState([])
  const [events, setEvents] = useState([])
  const [selectedEventId, setSelectedEventId] = useState(null)
  const [selectedEventDetail, setSelectedEventDetail] = useState(null)
  const [health, setHealth] = useState(null)
  const [status, setStatus] = useState(null)
  const [reloadKey, setReloadKey] = useState(0)
  const [showAnalytics, setShowAnalytics] = useState(false)
  const [showCatalog, setShowCatalog] = useState(false)
  const [showAdmin, setShowAdmin] = useState(false)
  const { session } = useAuth()
  const { t } = useI18n()
  const [briefPollExhausted, setBriefPollExhausted] = useState(false)
  // "What changed since last refresh?" card: raw delta response, visibility,
  // fetch error, and the run id it was last auto-shown for.
  const [delta, setDelta] = useState(null)
  const [deltaError, setDeltaError] = useState(false)
  const [showDelta, setShowDelta] = useState(false)
  // Pill mode only (short screen + large text + open drawer): expanded over
  // the drawer with internal scrolling.
  const [deltaExpanded, setDeltaExpanded] = useState(false)
  const deltaShownRunRef = useRef(undefined)
  // Current selection, read by async callbacks so a late response for an
  // older selection can never overwrite the newer one.
  const selectedEventIdRef = useRef(null)
  selectedEventIdRef.current = selectedEventId
  // Statuses set locally by operator decisions, re-applied to re-fetched
  // details so a brief-poll response can't revert a just-made decision.
  const decidedStatusRef = useRef({})

  const selectedEvent = events.find((e) => e.id === selectedEventId) || null

  // Audible ping when a NEW Critical/High event enters the feed (skipped on
  // the very first poll so page load stays quiet).
  const knownEventIdsRef = useRef(null)
  useEffect(() => {
    const ids = new Set(events.map((e) => e.id))
    if (knownEventIdsRef.current === null) {
      knownEventIdsRef.current = ids
      return
    }
    const hasNewAlert = events.some(
      (e) => !knownEventIdsRef.current.has(e.id) && (e.risk_tier === 'Critical' || e.risk_tier === 'High'),
    )
    if (hasNewAlert) playAlertPing()
    knownEventIdsRef.current = ids
  }, [events])

  const applyFetchedDetail = useCallback((id, detail) => {
    if (selectedEventIdRef.current !== id) return // stale response for an old selection
    const decided = decidedStatusRef.current[id]
    setSelectedEventDetail(decided ? { ...detail, status: decided } : detail)
  }, [])

  useEffect(() => {
    setBriefPollExhausted(false)
    if (selectedEventId == null) {
      setSelectedEventDetail(null)
      return
    }
    const id = selectedEventId
    api.getEvent(id).then((d) => applyFetchedDetail(id, d)).catch((err) => console.error('Failed to fetch event detail', err))
  }, [selectedEventId, applyFetchedDetail])

  // The backend inserts an event before its LLM brief is generated (~20-30 s),
  // so a freshly selected event can have generated_by = null. While that brief
  // is pending, re-fetch the detail every few seconds; stop as soon as
  // generated_by is set, on selection change/close/unmount, on any fetch
  // error (e.g. 404 after a new screening run removed the event), or after a cap.
  const briefPending = selectedEventDetail != null
    && selectedEventDetail.id === selectedEventId
    && shouldPollBrief(selectedEventDetail)
  useEffect(() => {
    if (!briefPending || briefPollExhausted) return
    const id = selectedEventId
    let cancelled = false
    let tries = 0
    let timer = null
    const tick = () => {
      if (cancelled) return
      if (tries >= BRIEF_POLL_MAX_TRIES) {
        setBriefPollExhausted(true)
        return
      }
      tries += 1
      api.getEvent(id)
        .then((d) => {
          if (cancelled) return
          applyFetchedDetail(id, d)
          if (shouldPollBrief(d)) timer = setTimeout(tick, BRIEF_POLL_INTERVAL_MS)
        })
        .catch((err) => {
          if (!cancelled) console.error('Brief poll: failed to re-fetch event detail; stopping', err)
        })
    }
    timer = setTimeout(tick, BRIEF_POLL_INTERVAL_MS)
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [briefPending, briefPollExhausted, selectedEventId, applyFetchedDetail])

  // A new screening run (refresh / demo / exit demo) deletes and re-creates
  // events; if the selected event is gone, close its drawer instead of
  // showing a stale event. Reads the selection via ref so selecting an event
  // that a pending poll hasn't delivered yet doesn't close it.
  useEffect(() => {
    const id = selectedEventIdRef.current
    if (id != null && !events.some((e) => e.id === id)) setSelectedEventId(null)
  }, [events])

  const pollEvents = useCallback(() => {
    return api.getEvents().then(setEvents).catch((err) => console.error('Failed to poll /api/events', err))
  }, [])

  const pollObjects = useCallback(() => {
    return api.getObjects().then(setObjects).catch((err) => console.error('Failed to poll /api/objects', err))
  }, [])

  const pollHealth = useCallback(() => {
    api.getHealth().then(setHealth).catch((err) => console.error('Failed to poll /api/health', err))
  }, [])

  // Single /api/status poll for the whole app (header mode chip + the
  // AssetPanel data-status block). Returns the promise so callers can await.
  const pollStatus = useCallback(() => (
    api.getStatus().then(setStatus).catch((err) => console.error('Failed to poll /api/status', err))
  ), [])

  useEffect(() => {
    pollObjects()
    pollEvents()
    pollHealth()
    pollStatus()
    const objInterval = setInterval(pollObjects, 10000)
    const evtInterval = setInterval(pollEvents, 8000)
    const healthInterval = setInterval(pollHealth, 30000)
    const statusInterval = setInterval(pollStatus, 15000)
    return () => {
      clearInterval(statusInterval)
      clearInterval(objInterval)
      clearInterval(evtInterval)
      clearInterval(healthInterval)
    }
  }, [pollObjects, pollEvents, pollHealth, pollStatus])

  // ESC collapses an expanded change-summary pill first, then closes the
  // drawer, then the change summary, then analytics.
  useEffect(() => {
    const onKey = (e) => {
      if (e.key !== 'Escape') return
      if (showDelta && deltaExpanded) setDeltaExpanded(false)
      else if (selectedEventId != null) setSelectedEventId(null)
      else if (showDelta) setShowDelta(false)
      else { setShowAnalytics(false); setShowCatalog(false); setShowAdmin(false) }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [selectedEventId, showDelta, deltaExpanded])

  // Fetch the latest-vs-previous screening comparison for a domain. A demo
  // comparison is only ever requested as domain=demo and is labelled demo.
  const loadDelta = useCallback((domain, fallback) => (
    api.getScreeningDelta(domain)
      .then((d) => { setDelta(d); setDeltaError(false); return d })
      .catch((err) => {
        console.error('Failed to fetch /api/screening/delta', err)
        // Older backend: fall back to the summary in the refresh response.
        if (fallback && typeof fallback === 'object') {
          const d = { ...fallback, domain }
          setDelta(d)
          return d
        }
        setDelta(null)
        setDeltaError(true)
        return null
      })
  ), [])

  // "What changed?" link in the asset panel: compare runs of the domain the
  // dashboard is currently showing.
  const openDelta = () => {
    setDelta(null)
    setDeltaError(false)
    setDeltaExpanded(false)
    setShowDelta(true)
    loadDelta(status?.mode === 'demo' ? 'demo' : 'real')
  }

  const objectsById = Object.fromEntries(objects.map((o) => [o.norad_id, o]))

  // Re-ingest from CelesTrak + re-screen. Throws on failure so the caller
  // (AssetPanel / EventFeed button) can show the error; status is refreshed
  // either way so the mode chip reflects what actually happened.
  const handleRefresh = async () => {
    let result
    try {
      result = await api.refresh()
    } finally {
      await Promise.all([pollObjects(), pollEvents(), pollStatus()])
      setReloadKey((k) => k + 1)
    }
    // Only after a successful manual refresh: show what changed (real data
    // domain -- a refresh, including "exit demo", is a real screening run).
    // Not awaited so the refresh button is released immediately.
    loadDelta('real', result?.delta).then((d) => {
      if (shouldAutoShow(d, deltaShownRunRef.current)) {
        deltaShownRunRef.current = d?.latest_run?.id
        setDeltaExpanded(false)
        setShowDelta(true)
      }
    })
    return result
  }

  // Live demo: raise a controlled demo event on camera. Runs the disclosed simulation seed
  // (Section 10) server-side, then re-polls so the new event flashes into
  // the feed and the seeded object's new track loads on the globe. Throws
  // on failure (DemoScenarioError via the API) -- the caller (EventFeed's
  // demo control) is responsible for catching and displaying that.
  // opts.historyStep (1 | 2): controlled two-step DEMO HISTORY of one demo
  // track. Step 2 adds the second observation; afterwards the demo-domain
  // change summary (always labelled demo) is shown and Event Evolution has
  // two rows to compare.
  const handleSimulate = async (mode, opts = {}) => {
    // The demo flow jumps straight to its event; hide any real-data change
    // summary so it can't be read as describing the demo scenario.
    setShowDelta(false)
    setDeltaExpanded(false)
    let result
    try {
      result = await api.demoSeed(mode, opts)
    } finally {
      await Promise.all([pollEvents(), pollObjects(), pollStatus()])
    }
    setReloadKey((k) => k + 1)
    // Jump straight to the scenario's own event -- don't make the presenter
    // hunt for it in a feed that may also contain unrelated real events.
    if (result?.event_id != null) setSelectedEventId(result.event_id)
    if (opts.historyStep === 2) {
      setDelta(null)
      setDeltaError(false)
      setShowDelta(true)
      loadDelta('demo').then((d) => { if (d?.latest_run?.id != null) deltaShownRunRef.current = d.latest_run.id })
    }
    return result
  }

  const handleDecided = (eventId, action) => {
    decidedStatusRef.current[eventId] = action
    setEvents((prev) => prev.map((e) => (e.id === eventId ? { ...e, status: action } : e)))
    setSelectedEventDetail((prev) => (prev && prev.id === eventId ? { ...prev, status: action } : prev))
  }

  const handleSelectAsset = (asset) => {
    const relatedEvent = events.find((e) => e.object_a_id === asset.norad_id)
    if (relatedEvent) setSelectedEventId(relatedEvent.id)
  }

  return (
    <div style={{ width: '100vw', height: '100vh', position: 'relative' }}>
      <HeaderBar
        health={health}
        status={status}
        events={events}
        showAnalytics={showAnalytics}
        onToggleAnalytics={() => { setShowCatalog(false); setShowAdmin(false); setShowAnalytics((v) => !v) }}
        showAdmin={showAdmin}
        onToggleAdmin={() => { setShowCatalog(false); setShowAnalytics(false); setShowAdmin((v) => !v) }}
      />
      {showAnalytics && <AnalyticsPanel onClose={() => setShowAnalytics(false)} />}
      {showCatalog && <CatalogPanel objects={objects} onClose={() => setShowCatalog(false)} />}
      {showAdmin && adminSections(session?.permissions).open && <AdminPanel onClose={() => setShowAdmin(false)} />}
      <div style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0 }}>
        <GlobeErrorBoundary fallbackTitle={t('globe.unavailableTitle')} fallbackText={t('globe.unavailableBody')}>
        <GlobeView
          events={events}
          selectedEvent={selectedEvent}
          reloadKey={reloadKey}
          objectsById={objectsById}
          onSelectObject={(obj) => {
            const relatedEvent = events.find((e) => e.object_a_id === obj.norad_id || e.object_b_id === obj.norad_id)
            if (relatedEvent) setSelectedEventId(relatedEvent.id)
          }}
        />
        </GlobeErrorBoundary>
      </div>
      <EventFeed
        events={events}
        selectedEvent={selectedEvent}
        onSelectEvent={(e) => setSelectedEventId(e.id)}
        objectsById={objectsById}
        onRefresh={handleRefresh}
        onSimulate={handleSimulate}
        status={status}
      />
      <AssetPanel
        objects={objects}
        events={events}
        status={status}
        onRefresh={handleRefresh}
        onSelectAsset={handleSelectAsset}
        onShowDelta={openDelta}
        onOpenCatalog={() => { setShowAnalytics(false); setShowAdmin(false); setShowCatalog((v) => !v) }}
      />
      {showDelta && (
        <ScreeningDeltaCard
          vm={deltaViewModel(delta)}
          error={deltaError}
          onClose={() => setShowDelta(false)}
          onSelectEvent={(id) => setSelectedEventId(id)}
          eventIds={new Set(events.map((e) => e.id))}
          drawerOpen={selectedEventDetail != null}
          expanded={deltaExpanded}
          onExpandedChange={setDeltaExpanded}
        />
      )}
      {selectedEventDetail && (
        <EventDetail
          event={selectedEventDetail}
          objectsById={objectsById}
          onClose={() => setSelectedEventId(null)}
          onDecided={handleDecided}
          briefPollExhausted={briefPollExhausted}
        />
      )}
    </div>
  )
}
