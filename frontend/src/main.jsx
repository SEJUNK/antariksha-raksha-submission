import React from 'react'
import ReactDOM from 'react-dom/client'
import AuthGate from './AuthGate.jsx'
import { AuthProvider } from './authContext'
import { I18nProvider } from './i18n'
import { UiScaleProvider } from './uiScaleContext'
import './theme.css'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <I18nProvider>
      <UiScaleProvider>
        <AuthProvider>
          <AuthGate />
        </AuthProvider>
      </UiScaleProvider>
    </I18nProvider>
  </React.StrictMode>,
)
