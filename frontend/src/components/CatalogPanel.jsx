import { Fragment, useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import { CATALOG_FILTERS, filterCatalogObjects, newObjectsViewModel, registryViewModel } from '../catalogView'
import { useI18n } from '../i18n'
import { formatUtc } from '../mode'
import { useUiScale } from '../uiScaleContext'
import { useAuth } from '../authContext'
import { PERMISSIONS, actionErrorText } from '../auth'
import {
  ACTION_TARGET, CONFIRM_ACTIONS, CRITICALITIES, assetActions, auditRows, createAssetPayload, patchAssetPayload, validateAssetForm,
} from '../assetAdmin'

// Catalog panel: PROTECTED ASSET REGISTRY (operator configuration) kept apart
// from the public OBJECT CATALOG, plus objects new to the LOCAL catalog with
// an operator review acknowledgement. Nothing here changes classification or
// protected status.

const cell = { padding: '4px 6px', borderBottom: '1px solid var(--border-line)', verticalAlign: 'top' }

function Tab({ active, onClick, children }) {
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        background: active ? 'var(--accent-dim)' : 'transparent', color: active ? 'var(--accent)' : 'var(--text-secondary)',
        border: '1px solid var(--border-line)', borderRadius: 6, padding: '4px 10px', fontSize: 10,
        fontWeight: 600, letterSpacing: '0.06em', cursor: 'pointer',
      }}
    >
      {children}
    </button>
  )
}

function FilterChip({ active, onClick, children }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      style={{
        background: active ? 'var(--accent-dim)' : 'transparent', color: active ? 'var(--accent)' : 'var(--text-secondary)',
        border: '1px solid var(--border-line)', borderRadius: 10, padding: '2px 9px', fontSize: 11, cursor: 'pointer',
      }}
    >
      {children}
    </button>
  )
}

const fieldStyle = {
  background: 'rgba(11, 16, 32, 0.6)', color: 'var(--text-primary)', border: '1px solid var(--border-line)',
  borderRadius: 6, padding: '3px 6px', fontSize: 12, minWidth: 0,
}
const actBtn = (tone = 'accent') => ({
  background: 'transparent', whiteSpace: 'nowrap', cursor: 'pointer', borderRadius: 6, padding: '2px 7px', fontSize: 10,
  color: tone === 'warn' ? 'var(--warn)' : tone === 'muted' ? 'var(--text-secondary)' : 'var(--accent)',
  border: `1px solid ${tone === 'warn' ? 'var(--warn)' : tone === 'muted' ? 'var(--border-line)' : 'var(--accent-dim)'}`,
})

// Inline editor for one registry row (criticality / note / exact match).
function AssetEditor({ row, busy, onSave, onCancel }) {
  const { t } = useI18n()
  const [edit, setEdit] = useState({ criticality: row.criticality, note: row.note || '', exact_match: row.exactMatch })
  const patch = patchAssetPayload(row, edit)
  return (
    <form
      onSubmit={(e) => { e.preventDefault(); if (patch) onSave(row, patch) }}
      style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center', padding: '4px 0' }}
    >
      <label style={{ fontSize: 11, color: 'var(--text-secondary)', display: 'flex', gap: 4, alignItems: 'center' }}>
        {t('catalog.col.tier')}
        <select value={edit.criticality} onChange={(e) => setEdit((x) => ({ ...x, criticality: e.target.value }))} style={fieldStyle}>
          {CRITICALITIES.map((c) => <option key={c} value={c} style={{ background: 'var(--bg-panel-solid)' }}>{c}</option>)}
        </select>
      </label>
      <input type="text" value={edit.note} maxLength={500} onChange={(e) => setEdit((x) => ({ ...x, note: e.target.value }))}
        placeholder={t('assets.note')} aria-label={t('assets.note')} style={{ ...fieldStyle, flex: '1 1 140px' }} />
      <label style={{ fontSize: 11, color: 'var(--text-secondary)', display: 'flex', gap: 4, alignItems: 'center' }}>
        <input type="checkbox" checked={edit.exact_match} onChange={(e) => setEdit((x) => ({ ...x, exact_match: e.target.checked }))} />
        {t('assets.exactMatch')}
      </label>
      <button type="submit" disabled={!patch || busy} style={actBtn()}>{t('admin.save')}</button>
      <button type="button" onClick={onCancel} style={actBtn('muted')}>{t('admin.cancel')}</button>
    </form>
  )
}

// "Add protected asset" form (manage_assets).
function AddAsset({ busy, onCreate }) {
  const { t } = useI18n()
  const empty = { name_query: '', exact_match: false, criticality: 'Tier2', note: '' }
  const [open, setOpen] = useState(false)
  const [form, setForm] = useState(empty)
  const [touched, setTouched] = useState(false)
  const v = validateAssetForm(form)
  if (!open) {
    return (
      <button type="button" onClick={() => setOpen(true)} style={{ ...actBtn(), marginTop: 8, fontSize: 11, padding: '3px 10px' }}>
        + {t('assets.add')}
      </button>
    )
  }
  const submit = async (e) => {
    e.preventDefault()
    setTouched(true)
    if (!v.ok || busy) return
    const ok = await onCreate(createAssetPayload(form))
    if (ok) { setForm(empty); setTouched(false); setOpen(false) }
  }
  const label = { display: 'flex', flexDirection: 'column', gap: 2, fontSize: 11, color: 'var(--text-secondary)', minWidth: 0 }
  return (
    <form onSubmit={submit} style={{ marginTop: 8, border: '1px solid var(--border-line)', borderRadius: 8, padding: 8 }}>
      <div className="eyebrow" style={{ fontSize: 10, color: 'var(--text-primary)', marginBottom: 6 }}>{t('assets.add')}</div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'flex-start' }}>
        <label style={{ ...label, flex: '2 1 160px' }}>
          {t('assets.nameQuery')}
          <input type="text" value={form.name_query} maxLength={120} onChange={(e) => setForm((f) => ({ ...f, name_query: e.target.value }))} style={fieldStyle} />
          {touched && v.errors.name_query && <span style={{ fontSize: 10, color: 'var(--warn)' }}>{t(v.errors.name_query)}</span>}
        </label>
        <label style={{ ...label, flex: '0 1 90px' }}>
          {t('catalog.col.tier')}
          <select value={form.criticality} onChange={(e) => setForm((f) => ({ ...f, criticality: e.target.value }))} style={fieldStyle}>
            {CRITICALITIES.map((c) => <option key={c} value={c} style={{ background: 'var(--bg-panel-solid)' }}>{c}</option>)}
          </select>
        </label>
        <label style={{ ...label, flex: '2 1 160px' }}>
          {t('assets.note')}
          <input type="text" value={form.note} maxLength={500} onChange={(e) => setForm((f) => ({ ...f, note: e.target.value }))} style={fieldStyle} />
        </label>
      </div>
      <label style={{ display: 'flex', gap: 4, alignItems: 'center', fontSize: 11, color: 'var(--text-secondary)', marginTop: 6 }}>
        <input type="checkbox" checked={form.exact_match} onChange={(e) => setForm((f) => ({ ...f, exact_match: e.target.checked }))} />
        {t('assets.exactMatchLong')}
      </label>
      <div style={{ display: 'flex', gap: 6, marginTop: 6 }}>
        <button type="submit" disabled={busy} style={{ ...actBtn(), fontSize: 11, padding: '3px 10px' }}>{t('assets.addSubmit')}</button>
        <button type="button" onClick={() => { setOpen(false); setForm(empty); setTouched(false) }} style={{ ...actBtn('muted'), fontSize: 11, padding: '3px 10px' }}>{t('admin.cancel')}</button>
      </div>
    </form>
  )
}

// Compact registry change audit (who changed what, when).
function RegistryAudit() {
  const { t } = useI18n()
  const [open, setOpen] = useState(false)
  const [rows, setRows] = useState(null)
  const [error, setError] = useState(false)
  useEffect(() => {
    if (!open) return
    setError(false)
    api.getProtectedAssetAudit().then((r) => setRows(auditRows(r) || [])).catch(() => setError(true))
  }, [open])
  return (
    <div style={{ marginTop: 8 }}>
      <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open}
        style={{ background: 'none', border: 'none', padding: 0, color: 'var(--accent)', fontSize: 11, cursor: 'pointer' }}>
        {open ? '▾' : '▸'} {t('assets.auditTitle')}
      </button>
      {open && (error ? (
        <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{t('admin.auditUnavailable')}</div>
      ) : !rows ? (
        <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{t('common.loading')}</div>
      ) : rows.length === 0 ? (
        <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{t('admin.auditEmpty')}</div>
      ) : (
        <div style={{ maxHeight: 150, overflowY: 'auto', border: '1px solid var(--border-line)', borderRadius: 6, marginTop: 4 }}>
          {rows.map((a) => (
            <div key={a.key} className="mono" style={{ fontSize: 11, padding: '3px 8px', borderBottom: '1px solid var(--border-line)' }}>
              <span style={{ color: 'var(--text-secondary)' }}>{a.at ? formatUtc(a.at) : '—'}</span>
              {' · '}{a.action}{a.target ? ` · ${a.target}` : ''}{a.actor ? ` · ${a.actor.text}` : ''}
              {a.detail && <div style={{ color: 'var(--text-secondary)' }}>{a.detail}</div>}
            </div>
          ))}
        </div>
      ))}
    </div>
  )
}

function Registry({ vm, objects, onChanged }) {
  const { t } = useI18n()
  const { caps } = useAuth()
  const canManage = caps.manageAssets
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState('all')
  const found = filterCatalogObjects(objects, query, filter)
  const types = vm.catalog.byType
  const [editing, setEditing] = useState(null)
  const [confirm, setConfirm] = useState(null) // { row, action }
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState(null) // { tone, text }

  // Every change goes to the backend; its 4xx detail (e.g. 409 "would leave
  // no active protected asset") is shown as-is.
  const run = async (call) => {
    setBusy(true)
    setMsg(null)
    try {
      await call()
      setMsg({ tone: 'ok', text: t('assets.saved') })
      if (onChanged) onChanged()
      return true
    } catch (err) {
      setMsg({ tone: 'error', text: actionErrorText(err, t) })
      return false
    } finally {
      setBusy(false)
    }
  }
  const save = (row, patch) => run(() => api.updateProtectedAsset(row.assetId, patch)).then((ok) => { if (ok) setEditing(null) })
  const transition = (row, action) => run(() => api.setProtectedAssetStatus(row.assetId, ACTION_TARGET[action])).then(() => setConfirm(null))
  const onAction = (row, action) => {
    if (action === 'edit') { setConfirm(null); setEditing(row.key); return }
    if (CONFIRM_ACTIONS.includes(action)) { setEditing(null); setConfirm({ row, action }); return }
    transition(row, action)
  }
  const create = (payload) => run(() => api.createProtectedAsset(payload))
  const cols = canManage ? 6 : 5
  return (
    <div>
      {/* PROTECTED ASSET REGISTRY -- operator configuration only */}
      <div className="eyebrow" style={{ fontSize: 11, color: 'var(--text-primary)', marginBottom: 4 }}>
        {t('catalog.protectedHeading', { n: vm.counts.total })}
      </div>
      <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginBottom: 8, lineHeight: 1.45 }}>{t('catalog.registryNote')}</div>
      {canManage && (
        <div style={{ fontSize: 11, color: 'var(--accent)', marginBottom: 6, lineHeight: 1.45 }}>{t('assets.effective')}</div>
      )}
      {msg && (
        <div role={msg.tone === 'error' ? 'alert' : 'status'} className="mono"
          style={{ fontSize: 11, marginBottom: 6, color: msg.tone === 'error' ? 'var(--critical)' : 'var(--safe)' }}>
          {msg.text}{msg.tone === 'ok' ? ` ${t('assets.effective')}` : ''}
        </div>
      )}
      <div style={{ overflowX: 'auto' }}>
        <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: 12 }}>
          <thead>
            <tr className="mono" style={{ fontSize: 11, color: 'var(--text-secondary)', textAlign: 'left' }}>
              <th style={cell}>{t('catalog.col.asset')}</th>
              <th style={cell}>NORAD</th>
              <th style={cell}>{t('catalog.col.tier')}</th>
              <th style={cell}>{t('catalog.col.data')}</th>
              <th style={cell}>{t('catalog.col.protected')}</th>
              {canManage && <th style={cell}>{t('admin.col.actions')}</th>}
            </tr>
          </thead>
          <tbody>
            {vm.rows.map((r) => (
              <Fragment key={r.key}>
              <tr style={{ opacity: r.status === 'retired' ? 0.6 : 1 }}>
                <td style={cell} title={r.note || undefined}>
                  {r.name}
                  <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{[r.owner !== '—' ? r.owner : null, r.note].filter(Boolean).join(' · ')}</div>
                </td>
                <td style={cell} className="mono">{r.noradId}</td>
                <td style={cell} className="mono">{r.criticality}</td>
                <td style={{ ...cell, color: r.statusTone === 'safe' ? 'var(--safe)' : 'var(--warn)' }} title={r.reason || undefined}>
                  {r.statusTone === 'safe' ? '● ' : '▲ '}{t(r.statusKey)}
                  <div className="mono" style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{r.format} · {r.age}</div>
                  {r.reason ? <div style={{ fontSize: 11 }}>{r.reason}</div> : null}
                </td>
                {r.status === 'active' ? (
                  <td style={cell} title={t('catalog.configuredTip')}>{r.protected ? t('catalog.configured') : '—'}</td>
                ) : (
                  <td style={{ ...cell, color: 'var(--text-secondary)' }} title={t('assets.notProtectedTip')}>{t(r.statusLabelKey)}</td>
                )}
                {canManage && (
                  <td style={cell}>
                    <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                      {assetActions(r).map((a) => (
                        <button key={a} type="button" disabled={busy} onClick={() => onAction(r, a)}
                          style={actBtn(a === 'retire' ? 'warn' : a === 'edit' ? 'muted' : 'accent')}>
                          {t(`assets.action.${a}`)}
                        </button>
                      ))}
                    </div>
                  </td>
                )}
              </tr>
              {canManage && editing === r.key && (
                <tr><td colSpan={cols} style={cell}>
                  <AssetEditor row={r} busy={busy} onSave={save} onCancel={() => setEditing(null)} />
                </td></tr>
              )}
              {canManage && confirm?.row.key === r.key && (
                <tr><td colSpan={cols} style={cell}>
                  <div role="alertdialog" aria-label={t('assets.confirmRetire', { name: r.name })}
                    style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center', fontSize: 11, color: 'var(--warn)' }}>
                    <span>{t('assets.confirmRetire', { name: r.name })}</span>
                    <button type="button" disabled={busy} onClick={() => transition(r, confirm.action)} style={actBtn('warn')} autoFocus>
                      {t('assets.action.retire')}
                    </button>
                    <button type="button" onClick={() => setConfirm(null)} style={actBtn('muted')}>{t('admin.cancel')}</button>
                  </div>
                </td></tr>
              )}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>
      {canManage && <AddAsset busy={busy} onCreate={create} />}
      {canManage && <RegistryAudit />}

      {/* PUBLIC OBJECT CATALOG -- every ingested object, protected or not */}
      <div style={{ marginTop: 14, borderTop: '1px solid var(--border-line)', paddingTop: 10 }}>
        <div className="eyebrow" style={{ fontSize: 11, color: 'var(--text-primary)', marginBottom: 4 }}>{t('catalog.objectCatalog')}</div>
        <div className="mono" style={{ fontSize: 12, lineHeight: 1.55 }}>
          {t('catalog.catalogCounts', { active: vm.catalog.active ?? '—', inactive: vm.catalog.inactive ?? '—' })}
        </div>
        <div className="mono" style={{ fontSize: 11, color: 'var(--text-secondary)', lineHeight: 1.55 }}>
          {[['satellite', types.satellite], ['debris', types.debris], ['foreign_sat', types.foreign_sat]]
            .filter(([, v]) => v != null).map(([k, v]) => `${v} ${t(`objType.${k}`)}`).join(' · ')}
          {Object.keys(vm.catalog.byFormat).length ? ` · ${Object.entries(vm.catalog.byFormat).map(([k, v]) => `${k.toUpperCase()} ${v}`).join(' · ')}` : ''}
        </div>
        <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginBottom: 6 }}>{t('catalog.catalogSource', { v: vm.catalog.source || '—' })}</div>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center', marginBottom: 6 }}>
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t('catalog.search')}
            aria-label={t('catalog.search')}
            style={{
              flex: '1 1 140px', minWidth: 120, background: 'transparent', color: 'var(--text-primary)',
              border: '1px solid var(--border-line)', borderRadius: 6, padding: '3px 8px', fontSize: 12,
            }}
          />
          {CATALOG_FILTERS.map((f) => (
            <FilterChip key={f} active={filter === f} onClick={() => setFilter(f)}>{t(`catalog.filter.${f}`)}</FilterChip>
          ))}
        </div>
        <div style={{ maxHeight: 180, overflowY: 'auto', border: '1px solid var(--border-line)', borderRadius: 6 }}>
          {found.length === 0 ? (
            <div style={{ fontSize: 11, color: 'var(--text-secondary)', padding: 6 }}>{t('catalog.noMatch')}</div>
          ) : found.map((o) => (
            <div key={o.key} style={{ display: 'flex', gap: 8, alignItems: 'baseline', padding: '3px 8px', fontSize: 12, borderBottom: '1px solid var(--border-line)' }}>
              <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{o.name}</span>
              <span className="mono" style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{o.noradId}</span>
              <span style={{ fontSize: 11, color: o.protected ? 'var(--accent)' : 'var(--text-secondary)', minWidth: 90, textAlign: 'right' }}>
                {o.protected ? t('catalog.configured') : (o.typeKey ? t(o.typeKey) : '—')}
              </span>
            </div>
          ))}
        </div>
        <div style={{ marginTop: 6, fontSize: 11, color: 'var(--text-secondary)', fontStyle: 'italic' }}>{t('catalog.catalogNote')}</div>
      </div>
    </div>
  )
}

function NewObjects({ vm, onReview, busy, error }) {
  const { t } = useI18n()
  const { caps, deniedVars } = useAuth()
  const deniedTip = caps.reviewObjects ? null : t('auth.denied', { ...deniedVars(PERMISSIONS.REVIEW_OBJECTS), perm: t('perm.review_objects') })
  return (
    <div>
      <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginBottom: 8, lineHeight: 1.45 }}>
        {t('catalog.newNote')}{vm.baseline ? ` ${t('catalog.baseline', { at: formatUtc(vm.baseline) })}` : ''}
      </div>
      <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginBottom: 8, lineHeight: 1.45, fontStyle: 'italic' }}>{t('catalog.reviewScope')}</div>
      {deniedTip && <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginBottom: 6 }}>{deniedTip}</div>}
      {error && <div style={{ fontSize: 11, color: 'var(--warn)', marginBottom: 6 }}>{error}</div>}
      {vm.empty ? (
        <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{t('catalog.newEmpty')}</div>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: 12 }}>
            <thead>
              <tr className="mono" style={{ fontSize: 11, color: 'var(--text-secondary)', textAlign: 'left' }}>
                <th style={cell}>{t('catalog.col.object')}</th>
                <th style={cell}>{t('catalog.col.firstSeen')}</th>
                <th style={cell}>{t('catalog.col.data')}</th>
                <th style={cell}>{t('catalog.col.class')}</th>
                <th style={cell}>{t('catalog.col.review')}</th>
              </tr>
            </thead>
            <tbody>
              {vm.rows.map((r) => (
                <tr key={r.key}>
                  <td style={cell}>{r.name}<div className="mono" style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{r.noradId}</div></td>
                  <td style={cell} className="mono">{r.firstSeen ? formatUtc(r.firstSeen) : '—'}</td>
                  <td style={cell}>{t(r.orbitalKey)}<div className="mono" style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{r.format} · {t(r.activeKey)}</div></td>
                  <td style={cell}>{r.typeKey ? t(r.typeKey) : '—'}</td>
                  <td style={cell}>
                    {r.reviewed ? (
                      <span style={{ color: 'var(--safe)' }} title={r.note || undefined}>✓ {t('catalog.reviewed')}
                        <div className="mono" style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{formatUtc(r.reviewedAt)}</div>
                        <div className="mono" style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
                          {r.reviewer ? t('catalog.reviewedBy', { who: r.reviewer.text }) : t('hist.legacy')}
                        </div>
                      </span>
                    ) : !caps.reviewObjects ? (
                      <span style={{ fontSize: 11, color: 'var(--text-secondary)' }} title={deniedTip}>{t('catalog.notReviewed')}</span>
                    ) : (
                      <button
                        type="button"
                        disabled={busy === r.noradId}
                        onClick={() => onReview(r.noradId)}
                        style={{ background: 'transparent', color: 'var(--accent)', border: '1px solid var(--accent-dim)', borderRadius: 6, padding: '2px 8px', fontSize: 10, cursor: 'pointer' }}
                        title={t('catalog.reviewTip')}
                      >
                        {t('catalog.markReviewed')}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

export default function CatalogPanel({ onClose, objects = [] }) {
  const { t } = useI18n()
  const { scale, layout } = useUiScale()
  const [tab, setTab] = useState('registry')
  const [registry, setRegistry] = useState(null)
  const [newObjects, setNewObjects] = useState(null)
  const [loadError, setLoadError] = useState(false)
  const [busy, setBusy] = useState(null)
  const [reviewError, setReviewError] = useState(null)

  const load = useCallback(() => {
    setLoadError(false)
    Promise.all([api.getRegistry(), api.getNewObjects()])
      .then(([reg, objs]) => { setRegistry(reg); setNewObjects(objs) })
      .catch((err) => { console.error('Failed to load catalog views', err); setLoadError(true) })
  }, [])
  useEffect(() => { load() }, [load])

  const review = (noradId) => {
    setBusy(noradId)
    setReviewError(null)
    api.reviewNewObject(noradId)
      .then(load)
      .catch((err) => setReviewError(actionErrorText(err, t)))
      .finally(() => setBusy(null))
  }

  const regVm = registryViewModel(registry)
  const newVm = newObjectsViewModel(newObjects)

  return (
    <div style={{
      position: 'absolute', top: 64, right: 16, width: 560, maxHeight: layout.analyticsMaxHeight,
      maxWidth: 'calc(100% - 32px)', zoom: scale, overflow: 'hidden',
      background: 'var(--bg-panel-solid)', border: '1px solid var(--border-line)', borderRadius: 10, padding: 16,
      display: 'flex', flexDirection: 'column', zIndex: 40, animation: 'slideUp 200ms ease-out',
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10, gap: 8 }}>
        <span className="eyebrow">{t('catalog.title')}</span>
        <button onClick={onClose} aria-label={t('common.close')}
          style={{ background: 'none', border: 'none', color: 'var(--text-secondary)', fontSize: 18, cursor: 'pointer', lineHeight: 1 }}>×</button>
      </div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 10 }}>
        <Tab active={tab === 'registry'} onClick={() => setTab('registry')}>{t('catalog.tabRegistry')}{regVm ? ` (${regVm.counts.total})` : ''}</Tab>
        <Tab active={tab === 'new'} onClick={() => setTab('new')}>{t('catalog.tabNew')}{newVm ? ` (${newVm.notReviewed})` : ''}</Tab>
      </div>
      <div style={{ overflowY: 'auto', minHeight: 0 }}>
        {loadError && <div style={{ fontSize: 11, color: 'var(--warn)' }}>{t('catalog.unavailable')}</div>}
        {!loadError && (!regVm || !newVm) && <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{t('common.loading')}</div>}
        {tab === 'registry' && regVm && <Registry vm={regVm} objects={objects} onChanged={load} />}
        {tab === 'new' && newVm && <NewObjects vm={newVm} onReview={review} busy={busy} error={reviewError} />}
      </div>
    </div>
  )
}
