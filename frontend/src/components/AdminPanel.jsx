import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import { useAuth } from '../authContext'
import { PERMISSIONS, ROLES } from '../auth'
import { adminSections, horizonSavePayload, horizonViewModel } from '../opsView'
import { MIN_PASSWORD_LENGTH, adminErrorText, createUserPayload, userActions, usersViewModel, validateNewUser, validatePassword } from '../adminView'
import { auditRows } from '../assetAdmin'
import { telegramTestMessage, telegramViewModel } from '../telegramView'
import { useI18n } from '../i18n'
import { formatUtc } from '../mode'
import { useUiScale } from '../uiScaleContext'

// Local account administration (manage_users) and the governance audit
// (view_admin_audit). Overlay like the Analytics panel. Passwords are typed
// into password fields, sent once and cleared; they are never shown.

const cell = { padding: '4px 6px', borderBottom: '1px solid var(--border-line)', verticalAlign: 'top' }
const input = {
  background: 'rgba(11, 16, 32, 0.6)', color: 'var(--text-primary)', border: '1px solid var(--border-line)',
  borderRadius: 6, padding: '4px 8px', fontSize: 12, minWidth: 0,
}
const smallBtn = (tone = 'accent') => ({
  background: 'transparent', color: tone === 'warn' ? 'var(--warn)' : tone === 'muted' ? 'var(--text-secondary)' : 'var(--accent)',
  border: `1px solid ${tone === 'warn' ? 'var(--warn)' : tone === 'muted' ? 'var(--border-line)' : 'var(--accent-dim)'}`,
  borderRadius: 6, padding: '2px 8px', fontSize: 10, cursor: 'pointer', whiteSpace: 'nowrap',
})

function PasswordReset({ user, onDone, onCancel }) {
  const { t } = useI18n()
  const [pw, setPw] = useState('')
  const [busy, setBusy] = useState(false)
  const submit = async (e) => {
    e.preventDefault()
    if (!validatePassword(pw) || busy) return
    setBusy(true)
    try {
      await onDone(user, pw)
    } finally {
      setPw('')
      setBusy(false)
    }
  }
  return (
    <form onSubmit={submit} style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginTop: 4 }}>
      <input type="password" autoComplete="new-password" value={pw} onChange={(e) => setPw(e.target.value)}
        aria-label={t('admin.newPassword', { user: user.username })} placeholder={t('admin.passwordHint', { n: MIN_PASSWORD_LENGTH })}
        style={{ ...input, flex: '1 1 120px' }} autoFocus />
      <button type="submit" disabled={!validatePassword(pw) || busy} style={smallBtn()}>{t('admin.save')}</button>
      <button type="button" onClick={onCancel} style={smallBtn('muted')}>{t('admin.cancel')}</button>
    </form>
  )
}

function CreateUser({ onCreate }) {
  const { t } = useI18n()
  const empty = { username: '', display_name: '', role: 'VIEWER', password: '' }
  const [form, setForm] = useState(empty)
  const [busy, setBusy] = useState(false)
  const [touched, setTouched] = useState(false)
  const v = validateNewUser(form)
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }))
  const submit = async (e) => {
    e.preventDefault()
    setTouched(true)
    if (!v.ok || busy) return
    setBusy(true)
    try {
      const ok = await onCreate(createUserPayload(form))
      setForm(ok ? empty : { ...form, password: '' })
      if (ok) setTouched(false)
    } finally {
      setBusy(false)
    }
  }
  const err = (k) => (touched && v.errors[k] ? <div style={{ fontSize: 10, color: 'var(--warn)' }}>{t(v.errors[k], { n: MIN_PASSWORD_LENGTH })}</div> : null)
  const label = { display: 'flex', flexDirection: 'column', gap: 2, fontSize: 11, color: 'var(--text-secondary)', flex: '1 1 140px', minWidth: 0 }
  return (
    <form onSubmit={submit} style={{ border: '1px solid var(--border-line)', borderRadius: 8, padding: 10, marginTop: 12 }}>
      <div className="eyebrow" style={{ fontSize: 11, color: 'var(--text-primary)', marginBottom: 6 }}>{t('admin.createTitle')}</div>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <label style={label}>{t('admin.col.username')}
          <input type="text" autoComplete="off" autoCapitalize="none" spellCheck={false} value={form.username} onChange={set('username')} style={input} />
          {err('username')}
        </label>
        <label style={label}>{t('admin.col.displayName')}
          <input type="text" autoComplete="off" value={form.display_name} onChange={set('display_name')} style={input} />
        </label>
        <label style={label}>{t('admin.col.role')}
          <select value={form.role} onChange={set('role')} style={input}>
            {ROLES.map((r) => <option key={r} value={r} style={{ background: 'var(--bg-panel-solid)' }}>{r}</option>)}
          </select>
          {err('role')}
        </label>
        <label style={label}>{t('admin.password')}
          <input type="password" autoComplete="new-password" value={form.password} onChange={set('password')}
            placeholder={t('admin.passwordHint', { n: MIN_PASSWORD_LENGTH })} style={input} />
          {err('password')}
        </label>
      </div>
      <button type="submit" disabled={busy} style={{ ...smallBtn(), marginTop: 8, padding: '4px 12px', fontSize: 11 }}>
        {busy ? t('admin.saving') : t('admin.create')}
      </button>
    </form>
  )
}

// Screening horizon: the future window the NEXT screening run screens over.
// Values come from the backend (allowed list, current, last change); only
// configure_system may save, everyone else reads text.
function HorizonSection() {
  const { t } = useI18n()
  const { session, deniedVars } = useAuth()
  const perms = session?.permissions
  const [vm, setVm] = useState(null)
  const [loadError, setLoadError] = useState(null)
  const [selected, setSelected] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState(null) // { tone, text }

  const apply = useCallback((resp) => {
    const next = horizonViewModel(resp, perms)
    setVm(next)
    setSelected(next?.hours != null ? String(next.hours) : '')
    return next
  }, [perms])

  useEffect(() => {
    let alive = true
    setLoadError(null)
    api.getScreeningHorizon()
      .then((r) => { if (alive && !apply(r)) setLoadError(t('horizon.unavailable')) })
      .catch((err) => { if (alive) setLoadError(adminErrorText(err, t) || t('horizon.unavailable')) })
    return () => { alive = false }
  }, [apply, t])

  const payload = horizonSavePayload(selected === '' ? null : Number(selected), vm)
  const save = async (e) => {
    e.preventDefault()
    if (!payload || busy) return
    setBusy(true)
    setMessage(null)
    try {
      const r = await api.setScreeningHorizon(payload.hours)
      if (!apply(r)) {
        // Unexpected body: re-read the setting rather than guess.
        apply(await api.getScreeningHorizon())
      }
      setMessage({ tone: 'ok', text: t('horizon.saved') })
    } catch (err) {
      setMessage({ tone: 'error', text: adminErrorText(err, t) })
    } finally {
      setBusy(false)
    }
  }

  const deniedTip = t('auth.denied', { ...deniedVars(PERMISSIONS.CONFIGURE_SYSTEM), perm: t('perm.configure_system') })
  const muted = { fontSize: 11, color: 'var(--text-secondary)', lineHeight: 1.45 }
  return (
    <section aria-label={t('horizon.title')} style={{ border: '1px solid var(--border-line)', borderRadius: 8, padding: 10, marginBottom: 12 }}>
      <div className="eyebrow" style={{ fontSize: 11, color: 'var(--text-primary)', marginBottom: 6 }}>{t('horizon.title')}</div>
      <div style={{ ...muted, marginBottom: 4 }}>{t('horizon.explain')}</div>
      <div style={{ ...muted, marginBottom: 8 }}>{t('horizon.applies')}</div>
      {loadError && <div role="alert" style={{ fontSize: 11, color: 'var(--warn)' }}>{loadError}</div>}
      {!loadError && !vm && <div style={muted}>{t('common.loading')}</div>}
      {vm && (
        <>
          <div className="mono" style={{ fontSize: 12, marginBottom: 4 }}>
            {t('horizon.current')}: <b>{vm.hours != null ? t('horizon.hoursValue', { h: vm.hours }) : 'N/A'}</b>
            {vm.stepSeconds != null && <span style={{ color: 'var(--text-secondary)' }}> · {t('horizon.step', { s: vm.stepSeconds })}</span>}
          </div>
          <div className="mono" style={{ ...muted, marginBottom: 8 }}>
            {vm.updatedAt || vm.updatedBy
              ? t('horizon.lastChange', { by: vm.updatedBy || '—', at: vm.updatedAt ? formatUtc(vm.updatedAt) : '—' })
              : t('horizon.noChange')}
          </div>
          {vm.canEdit ? (
            <form onSubmit={save} style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
              <label style={{ fontSize: 11, color: 'var(--text-secondary)', display: 'flex', gap: 6, alignItems: 'center' }}>
                {t('horizon.select')}
                <select value={selected} disabled={busy} onChange={(e) => { setSelected(e.target.value); setMessage(null) }} style={input}>
                  {vm.allowed.map((h) => (
                    <option key={h} value={String(h)} style={{ background: 'var(--bg-panel-solid)' }}>
                      {t('horizon.hoursValue', { h })}{h === vm.defaultHours ? ` (${t('horizon.default')})` : ''}
                    </option>
                  ))}
                </select>
              </label>
              <button type="submit" disabled={!payload || busy} style={{ ...smallBtn(), padding: '4px 12px', fontSize: 11, opacity: !payload || busy ? 0.5 : 1 }}>
                {busy ? t('admin.saving') : t('admin.save')}
              </button>
            </form>
          ) : (
            <div style={muted}>{t('horizon.readOnly')} <span style={{ fontSize: 10 }}>({deniedTip})</span></div>
          )}
        </>
      )}
      {message && (
        <div role={message.tone === 'error' ? 'alert' : 'status'} className="mono"
          style={{ fontSize: 11, marginTop: 6, color: message.tone === 'error' ? 'var(--critical)' : 'var(--safe)' }}>
          {message.text}
        </div>
      )}
    </section>
  )
}

// Optional Telegram notifications (configure_system only): non-secret status
// and a server-built, cooldown-limited test send. Credentials are backend
// deployment secrets and are never sent to or shown in the browser.
function TelegramSection() {
  const { t } = useI18n()
  const [vm, setVm] = useState(null)
  const [loadError, setLoadError] = useState(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState(null) // { tone, text }

  useEffect(() => {
    let alive = true
    api.getTelegramStatus()
      .then((r) => { if (alive) setVm(telegramViewModel(r)) })
      .catch((err) => { if (alive) setLoadError(adminErrorText(err, t)) })
    return () => { alive = false }
  }, [t])

  const sendTest = async () => {
    if (busy) return
    setBusy(true)
    setMessage(null)
    try {
      const r = await api.sendTelegramTest()
      const m = telegramTestMessage(r, null)
      setMessage({ tone: m.tone, text: t(m.key, m.vars) })
      if (r && r.telegram) setVm(telegramViewModel(r.telegram))
    } catch (err) {
      const m = telegramTestMessage(null, err)
      setMessage({ tone: 'error', text: m ? t(m.key) : adminErrorText(err, t) })
    } finally {
      setBusy(false)
    }
  }

  const muted = { fontSize: 11, color: 'var(--text-secondary)', lineHeight: 1.45 }
  const stateColor = vm?.state === 'active' ? 'var(--safe)' : vm?.state === 'unconfigured' ? 'var(--warn)' : 'var(--text-secondary)'
  return (
    <section aria-label={t('tg.title')} style={{ border: '1px solid var(--border-line)', borderRadius: 8, padding: 10, marginBottom: 12 }}>
      <div className="eyebrow" style={{ fontSize: 11, color: 'var(--text-primary)', marginBottom: 6 }}>{t('tg.title')}</div>
      <div style={{ ...muted, marginBottom: 6 }}>{t('tg.explain')}</div>
      {loadError && <div role="alert" style={{ fontSize: 11, color: 'var(--warn)' }}>{loadError}</div>}
      {!loadError && !vm && <div style={muted}>{t('common.loading')}</div>}
      {vm && (
        <>
          <div className="mono" style={{ fontSize: 12, marginBottom: 4, color: stateColor }}>
            {vm.state === 'active' ? '●' : '○'} {t(`tg.${vm.state}`)}
          </div>
          <div className="mono" style={{ ...muted, marginBottom: 4 }}>
            {t('tg.policy', { tier: vm.minRisk, demo: t(vm.notifyDemo ? 'tg.demoOn' : 'tg.demoOff') })}
          </div>
          <div style={{ ...muted, marginBottom: 8 }}>{t('tg.secret')}</div>
          {vm.state === 'active' && (
            <button type="button" onClick={sendTest} disabled={busy}
              style={{ ...smallBtn(), padding: '4px 12px', fontSize: 11, opacity: busy ? 0.5 : 1 }}>
              {t('tg.test')}
            </button>
          )}
        </>
      )}
      {message && (
        <div role={message.tone === 'error' ? 'alert' : 'status'} className="mono"
          style={{ fontSize: 11, marginTop: 6, color: message.tone === 'error' ? 'var(--critical)' : 'var(--safe)' }}>
          {message.text}
        </div>
      )}
    </section>
  )
}

export default function AdminPanel({ onClose }) {
  const { t } = useI18n()
  const { scale, layout } = useUiScale()
  const { session, caps } = useAuth()
  const [users, setUsers] = useState(null)
  const [audit, setAudit] = useState(null)
  const [loadError, setLoadError] = useState(null)
  const [message, setMessage] = useState(null) // { tone: 'ok' | 'error', text }
  const [busyId, setBusyId] = useState(null)
  const [resetFor, setResetFor] = useState(null)
  const selfId = session?.user?.id ?? null
  const sections = adminSections(session?.permissions)

  const load = useCallback(() => {
    setLoadError(null)
    if (caps.manageUsers) {
      api.getUsers()
        .then((r) => setUsers(usersViewModel(r) || []))
        .catch((err) => setLoadError(adminErrorText(err, t)))
    }
    if (caps.viewAdminAudit) {
      api.getAdminAudit().then((r) => setAudit(auditRows(r) || [])).catch(() => setAudit(null))
    }
  }, [caps.manageUsers, caps.viewAdminAudit, t])
  useEffect(() => { load() }, [load])

  const act = async (id, call, okKey) => {
    setBusyId(id)
    setMessage(null)
    try {
      await call()
      setMessage({ tone: 'ok', text: t(okKey) })
      load()
      return true
    } catch (err) {
      setMessage({ tone: 'error', text: adminErrorText(err, t) })
      load()
      return false
    } finally {
      setBusyId(null)
    }
  }

  const changeRole = (u, role) => act(u.id, () => api.updateUser(u.id, { role }), 'admin.ok.role')
  const setActive = (u, active) => act(u.id, () => api.updateUser(u.id, { active }), active ? 'admin.ok.activated' : 'admin.ok.deactivated')
  const resetPassword = async (u, password) => {
    const ok = await act(u.id, () => api.updateUser(u.id, { password }), 'admin.ok.password')
    if (ok) setResetFor(null)
  }
  const create = (payload) => act('new', () => api.createUser(payload), 'admin.ok.created')

  return (
    <div role="dialog" aria-label={t('admin.title')} style={{
      position: 'absolute', top: 64, right: 16, width: 620, maxHeight: layout.analyticsMaxHeight,
      maxWidth: 'calc(100% - 32px)', zoom: scale, overflow: 'hidden',
      background: 'var(--bg-panel-solid)', border: '1px solid var(--border-line)', borderRadius: 10, padding: 16,
      display: 'flex', flexDirection: 'column', zIndex: 40, animation: 'slideUp 200ms ease-out',
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8, gap: 8 }}>
        <span className="eyebrow">{t('admin.title')}</span>
        <button onClick={onClose} aria-label={t('common.close')}
          style={{ background: 'none', border: 'none', color: 'var(--text-secondary)', fontSize: 18, cursor: 'pointer', lineHeight: 1 }}>×</button>
      </div>
      <div style={{ overflowY: 'auto', minHeight: 0, paddingRight: 2 }}>
        {sections.horizon && <HorizonSection />}
        {caps.configureSystem && <TelegramSection />}
        {sections.users && (<>
        <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginBottom: 8, lineHeight: 1.45 }}>{t('admin.note')}</div>
        {message && (
          <div role={message.tone === 'error' ? 'alert' : 'status'} className="mono"
            style={{ fontSize: 11, marginBottom: 8, color: message.tone === 'error' ? 'var(--critical)' : 'var(--safe)' }}>
            {message.text}
          </div>
        )}
        {loadError && <div style={{ fontSize: 11, color: 'var(--warn)' }}>{loadError}</div>}
        {!loadError && !users && <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{t('common.loading')}</div>}
        {users && (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: 12 }}>
              <thead>
                <tr className="mono" style={{ fontSize: 11, color: 'var(--text-secondary)', textAlign: 'left' }}>
                  <th style={cell}>{t('admin.col.username')}</th>
                  <th style={cell}>{t('admin.col.role')}</th>
                  <th style={cell}>{t('admin.col.active')}</th>
                  <th style={cell}>{t('admin.col.lastLogin')}</th>
                  <th style={cell}>{t('admin.col.actions')}</th>
                </tr>
              </thead>
              <tbody>
                {users.map((u) => {
                  const acts = userActions(u, selfId)
                  const busy = busyId === u.id
                  return (
                    <tr key={u.id ?? u.username} style={{ opacity: u.active ? 1 : 0.65 }}>
                      <td style={cell}>
                        <span className="mono">{u.username}</span>
                        {u.id === selfId && <span style={{ fontSize: 10, color: 'var(--accent)' }}> · {t('admin.you')}</span>}
                        {u.displayName && <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{u.displayName}</div>}
                      </td>
                      <td style={cell}>
                        {acts.includes('role') ? (
                          <select value={u.role} disabled={busy} onChange={(e) => changeRole(u, e.target.value)}
                            aria-label={t('admin.changeRole', { user: u.username })} style={{ ...input, padding: '2px 4px', fontSize: 11 }}>
                            {(ROLES.includes(u.role) ? ROLES : [u.role, ...ROLES]).map((r) => (
                              <option key={r} value={r} style={{ background: 'var(--bg-panel-solid)' }}>{r}</option>
                            ))}
                          </select>
                        ) : <span className="mono" style={{ fontSize: 11 }} title={t('admin.selfTip')}>{u.role}</span>}
                      </td>
                      <td style={{ ...cell, color: u.active ? 'var(--safe)' : 'var(--text-secondary)' }}>
                        {u.active ? t('admin.active') : t('admin.inactive')}
                      </td>
                      <td style={cell} className="mono">
                        <span style={{ fontSize: 11 }}>{u.lastLoginAt ? formatUtc(u.lastLoginAt) : t('admin.never')}</span>
                      </td>
                      <td style={cell}>
                        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                          {acts.includes('deactivate') && (
                            <button type="button" disabled={busy} onClick={() => setActive(u, false)} style={smallBtn('warn')}>{t('admin.deactivate')}</button>
                          )}
                          {acts.includes('activate') && (
                            <button type="button" disabled={busy} onClick={() => setActive(u, true)} style={smallBtn()}>{t('admin.activate')}</button>
                          )}
                          {acts.includes('password') && resetFor !== u.id && (
                            <button type="button" disabled={busy} onClick={() => setResetFor(u.id)} style={smallBtn('muted')}>{t('admin.resetPassword')}</button>
                          )}
                        </div>
                        {resetFor === u.id && <PasswordReset user={u} onDone={resetPassword} onCancel={() => setResetFor(null)} />}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
        <CreateUser onCreate={create} />
        </>)}
        {caps.viewAdminAudit && (
          <div style={{ marginTop: 14, borderTop: '1px solid var(--border-line)', paddingTop: 10 }}>
            <div className="eyebrow" style={{ fontSize: 11, color: 'var(--text-primary)', marginBottom: 6 }}>{t('admin.auditTitle')}</div>
            {!audit ? (
              <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{t('admin.auditUnavailable')}</div>
            ) : audit.length === 0 ? (
              <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{t('admin.auditEmpty')}</div>
            ) : (
              <div style={{ maxHeight: 220, overflowY: 'auto', border: '1px solid var(--border-line)', borderRadius: 6 }}>
                {audit.map((a) => (
                  <div key={a.key} className="mono" style={{ fontSize: 11, padding: '4px 8px', borderBottom: '1px solid var(--border-line)', lineHeight: 1.45 }}>
                    <span style={{ color: 'var(--text-secondary)' }}>{a.at ? formatUtc(a.at) : '—'}</span>
                    {' · '}{a.action}
                    {a.target ? ` · ${a.target}` : ''}
                    {a.actor ? ` · ${a.actor.text}` : ''}
                    {a.detail && <div style={{ color: 'var(--text-secondary)', fontFamily: 'inherit' }}>{a.detail}</div>}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
