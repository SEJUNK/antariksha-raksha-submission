import { useState } from 'react'
import { api } from '../api'
import { useI18n } from '../i18n'
import { useAuth } from '../authContext'
import { actionErrorText } from '../auth'

function nowUtcHms() {
  return new Date().toISOString().slice(11, 19) + ' UTC'
}

// Operator decision. Decision-support only: approving/dismissing records a
// decision in the audit trail -- nothing is ever commanded to a spacecraft.
export default function ApprovalPanel({ event, onDecided }) {
  const { t, tip } = useI18n()
  const { caps, session } = useAuth()
  const initial = event.status && event.status !== 'pending' ? event.status : null
  const [decision, setDecision] = useState(initial)
  const [loggedAt, setLoggedAt] = useState(null)
  const [showReason, setShowReason] = useState(false)
  const [reason, setReason] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState(null)
  const isProximity = event.event_class === 'proximity_watch'

  const record = async (action, call) => {
    setError(null)
    setSubmitting(true)
    setDecision(action)
    setLoggedAt(nowUtcHms())
    setShowReason(false)
    try {
      await call()
      onDecided && onDecided(event.id, action)
    } catch (err) {
      console.error('Decision failed', err)
      // Revert -- the decision was NOT recorded, don't pretend it was.
      setDecision(null)
      setLoggedAt(null)
      if (action === 'dismissed') setShowReason(true)
      setError(actionErrorText(err, t) || t('approval.errorGeneric'))
    } finally {
      setSubmitting(false)
    }
  }

  const approve = () => record('approved', () => api.approveEvent(event.id))
  const submitDismiss = () => record('dismissed', () => api.dismissEvent(event.id, reason.trim()))

  const approvedText = `✓ ${isProximity ? t('approval.acknowledged') : t('approval.approved')}`

  return (
    <div>
      {decision !== null ? (
        <div className="mono" style={{ fontSize: 13, color: decision === 'approved' ? 'var(--safe)' : 'var(--text-secondary)' }}>
          {decision === 'approved' ? approvedText : `✓ ${t('approval.dismissed')}`} &middot; {loggedAt ? t('approval.logged', { t: loggedAt }) : t('approval.recorded')}
          {submitting && <span style={{ color: 'var(--text-secondary)' }}> {t('approval.saving')}</span>}
          {decision === 'dismissed' && reason.trim() && !submitting && (
            <div style={{ marginTop: 6, fontSize: 11, fontStyle: 'italic' }}>
              {t('approval.reasonRecorded')}
            </div>
          )}
        </div>
      ) : !caps.decide ? (
        // No decide permission: the decision history (shown alongside) stays
        // visible; the decision controls are not offered.
        <div className="mono" style={{ fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.45 }}>
          {t('approval.viewOnly', { role: session?.user?.role || '—' })}
        </div>
      ) : showReason ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
            {t('approval.whyDismiss')}
          </div>
          <textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            maxLength={500}
            autoFocus
            rows={3}
            placeholder={t('approval.placeholder')}
            title={tip(t('approval.placeholder'))}
            style={{
              background: 'rgba(11, 16, 32, 0.6)', color: 'var(--text-primary)',
              border: '1px solid var(--border-line)', borderRadius: 6,
              padding: '8px 10px', fontSize: 12, fontFamily: "'Inter', var(--font-indic), sans-serif",
              resize: 'vertical',
            }}
          />
          <div style={{ display: 'flex', gap: 8 }}>
            <button
              onClick={submitDismiss}
              disabled={!reason.trim() || submitting}
              style={{
                flex: 1, background: 'var(--warn)', color: '#08131f', border: 'none',
                borderRadius: 6, padding: '10px 12px', fontWeight: 700, fontSize: 12,
                cursor: reason.trim() ? 'pointer' : 'default',
                opacity: reason.trim() ? 1 : 0.5,
              }}
            >
              {t('approval.confirm')}
            </button>
            <button
              onClick={() => { setShowReason(false); setReason(''); setError(null) }}
              style={{
                background: 'transparent', color: 'var(--text-secondary)',
                border: '1px solid var(--border-line)', borderRadius: 6,
                padding: '10px 12px', fontWeight: 600, fontSize: 12, cursor: 'pointer',
              }}
            >
              {t('approval.back')}
            </button>
          </div>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          <button
            onClick={approve}
            disabled={submitting}
            style={{
              background: 'var(--safe)', color: '#08131f', border: 'none', borderRadius: 6,
              padding: '12px 16px', fontWeight: 700, cursor: 'pointer', fontSize: 13,
            }}
          >
            {isProximity ? t('approval.acknowledge') : t('approval.approve')}
          </button>
          <button
            onClick={() => setShowReason(true)}
            disabled={submitting}
            style={{
              background: 'transparent', color: 'var(--text-secondary)', border: '1px solid var(--border-line)',
              borderRadius: 6, padding: '12px 16px', fontWeight: 600, cursor: 'pointer', fontSize: 13,
            }}
          >
            {t('approval.dismiss')}
          </button>
        </div>
      )}
      {error && (
        <div className="mono" style={{ fontSize: 11, color: 'var(--critical)', marginTop: 8 }}>
          {t('approval.notRecorded')} — {error}
        </div>
      )}
      <div style={{ fontSize: 10, color: 'var(--text-secondary)', marginTop: 8, lineHeight: 1.4 }}>
        {t('approval.note')}
      </div>
    </div>
  )
}
