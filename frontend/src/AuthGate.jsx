// Chooses between the session check, the login screen and the console.
import App from './App'
import LoginScreen from './components/LoginScreen'
import { useAuth } from './authContext'
import { useI18n } from './i18n'

export default function AuthGate() {
  const { status } = useAuth()
  const { t } = useI18n()
  if (status === 'loading') {
    return (
      <div className="mono" role="status" style={{
        width: '100vw', height: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center',
        background: 'var(--bg-space)', color: 'var(--text-secondary)', fontSize: 12,
      }}>
        {t('login.checking')}
      </div>
    )
  }
  if (status !== 'authenticated') return <LoginScreen />
  return <App />
}
