import { useState } from 'react'
import { useAuth } from '../authContext'
import { useI18n } from '../i18n'
import { useUiScale } from '../uiScaleContext'
import { loginErrorKey } from '../auth'
import { LanguageSelector, TextSizeControl } from './HeaderControls'

// Mission-console sign-in card on the dark background (no globe). Local
// prototype accounts with role-based access; no credentials or username hints
// are shown. Language and text size stay available before sign-in.
const field = {
  width: '100%', background: 'rgba(11, 16, 32, 0.6)', color: 'var(--text-primary)',
  border: '1px solid var(--border-line)', borderRadius: 6, padding: '8px 10px', fontSize: 13,
}

export default function LoginScreen() {
  const { t } = useI18n()
  const { scale } = useUiScale()
  const { login, expired, error: checkError, recheck } = useAuth()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  const submit = async (e) => {
    e.preventDefault()
    if (busy || !username.trim() || !password) return
    setBusy(true)
    setError(null)
    try {
      await login(username.trim(), password)
    } catch (err) {
      const key = loginErrorKey(err)
      setError(key ? t(key) : (err?.message || t('login.failed')))
      setPassword('')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div style={{
      width: '100vw', height: '100vh', overflow: 'auto', background: 'var(--bg-space)',
      display: 'flex', flexDirection: 'column',
    }}>
      <div style={{ zoom: scale, flex: '1 0 auto', display: 'flex', flexDirection: 'column' }}>
        <div style={{
          display: 'flex', justifyContent: 'flex-end', alignItems: 'center', gap: 10, padding: '10px 16px',
          borderBottom: '2px solid transparent',
          borderImage: 'linear-gradient(90deg, var(--tricolor-saffron), white, var(--tricolor-green)) 1',
        }}>
          <LanguageSelector />
          <TextSizeControl />
        </div>
        <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16 }}>
          <form
            onSubmit={submit}
            aria-labelledby="login-title"
            style={{
              width: 360, maxWidth: '100%', background: 'var(--bg-panel-solid)', border: '1px solid var(--border-line)',
              borderRadius: 10, padding: 20, display: 'flex', flexDirection: 'column', gap: 12,
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <span style={{ width: 10, height: 10, borderRadius: '50%', background: 'var(--accent)', flexShrink: 0 }} />
              <span id="login-title" style={{ fontWeight: 700, letterSpacing: '0.08em' }}>ANTARIKSHA-RAKSHA</span>
            </div>
            <div className="eyebrow">{t('login.title')}</div>
            {expired && (
              <div role="status" style={{ fontSize: 12, color: 'var(--warn)' }}>{t('login.expired')}</div>
            )}
            {checkError && !expired && (
              <div role="status" style={{ fontSize: 12, color: 'var(--warn)' }}>
                {t('login.unreachable')}{' '}
                <button type="button" onClick={recheck}
                  style={{ background: 'none', border: 'none', color: 'var(--accent)', textDecoration: 'underline', cursor: 'pointer', padding: 0, fontSize: 12 }}>
                  {t('common.retry')}
                </button>
              </div>
            )}
            <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 11, color: 'var(--text-secondary)' }}>
              {t('login.username')}
              <input
                type="text" name="username" autoComplete="username" autoCapitalize="none" spellCheck={false}
                value={username} onChange={(e) => setUsername(e.target.value)} autoFocus required style={field}
              />
            </label>
            <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 11, color: 'var(--text-secondary)' }}>
              {t('login.password')}
              <input
                type="password" name="password" autoComplete="current-password"
                value={password} onChange={(e) => setPassword(e.target.value)} required style={field}
              />
            </label>
            {error && (
              <div role="alert" className="mono" style={{ fontSize: 12, color: 'var(--critical)' }}>{error}</div>
            )}
            <button
              type="submit"
              disabled={busy || !username.trim() || !password}
              style={{
                background: 'var(--accent)', color: '#08131f', border: 'none', borderRadius: 6,
                padding: '10px 12px', fontWeight: 700, fontSize: 13, letterSpacing: '0.06em',
                cursor: busy ? 'default' : 'pointer', opacity: busy || !username.trim() || !password ? 0.6 : 1,
              }}
            >
              {busy ? t('login.signingIn') : t('login.submit')}
            </button>
            <div style={{ fontSize: 11, color: 'var(--text-secondary)', lineHeight: 1.45 }}>{t('login.note')}</div>
          </form>
        </div>
      </div>
    </div>
  )
}
