import { useEffect, useState } from 'react'
import { api } from '../api'
import { useI18n } from '../i18n'
import { useUiScale } from '../uiScaleContext'

function StatBlock({ label, value, color }) {
  return (
    <div style={{ flex: 1, textAlign: 'center' }}>
      <div className="mono" style={{ fontSize: 28, fontWeight: 700, color: color || 'var(--text-primary)' }}>
        {value}
      </div>
      <div className="eyebrow" style={{ marginTop: 2 }}>{label}</div>
    </div>
  )
}

export default function AnalyticsPanel({ onClose }) {
  const { t } = useI18n()
  const { scale, layout } = useUiScale()
  const [stats, setStats] = useState(null)

  useEffect(() => {
    const load = () => api.getDecisionStats().then(setStats).catch((err) => console.error('Failed to fetch decision stats', err))
    load()
    const id = setInterval(load, 10000)
    return () => clearInterval(id)
  }, [])

  const rate = stats?.acceptance_rate

  return (
    <div style={{
      // maxHeight in unscaled px computed from the real viewport (zoom multiplies it).
      position: 'absolute', top: 64, right: 16, width: 420, maxHeight: layout.analyticsMaxHeight,
      maxWidth: 'calc(100% - 32px)', zoom: scale, overflow: 'hidden',
      background: 'var(--bg-panel)', backdropFilter: 'blur(8px)', WebkitBackdropFilter: 'blur(8px)',
      border: '1px solid var(--border-line)', borderRadius: 10, padding: 16,
      display: 'flex', flexDirection: 'column', zIndex: 40,
      animation: 'slideUp 200ms ease-out',
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <span className="eyebrow">{t('analytics.title')}</span>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          <a
            href={api.decisionsCsvUrl}
            download="antariksha_decision_log.csv"
            style={{
              fontSize: 10, fontWeight: 600, letterSpacing: '0.08em',
              color: 'var(--accent)', textDecoration: 'none',
              border: '1px solid var(--accent-dim)', borderRadius: 6, padding: '4px 8px',
            }}
          >
            ⤓ CSV
          </a>
        <button
          onClick={onClose}
          aria-label={t('analytics.close')}
          style={{ background: 'none', border: 'none', color: 'var(--text-secondary)', fontSize: 18, cursor: 'pointer', lineHeight: 1 }}
        >
          ×
        </button>
        </div>
      </div>

      {!stats ? (
        <div style={{ color: 'var(--text-secondary)', fontSize: 12 }}>{t('common.loading')}</div>
      ) : stats.total === 0 ? (
        <div style={{ color: 'var(--text-secondary)', fontSize: 12 }}>
          {t('analytics.empty')}
        </div>
      ) : (
        <>
          <div style={{ display: 'flex', gap: 8, marginBottom: 14 }}>
            <StatBlock label={t('analytics.suggestions')} value={stats.total} />
            <StatBlock label={t('analytics.accepted')} value={stats.approved} color="var(--safe)" />
            <StatBlock label={t('analytics.rejected')} value={stats.dismissed} color="var(--warn)" />
          </div>

          {rate != null && (
            <div style={{ marginBottom: 16 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                <span className="eyebrow">{t('analytics.rate')}</span>
                <span className="mono" style={{ fontSize: 12 }}>{Math.round(rate * 100)}%</span>
              </div>
              <div style={{ height: 6, borderRadius: 3, background: 'rgba(242, 153, 74, 0.25)', overflow: 'hidden' }}>
                <div style={{ width: `${rate * 100}%`, height: '100%', background: 'var(--safe)', borderRadius: 3 }} />
              </div>
            </div>
          )}

          <div className="eyebrow" style={{ marginBottom: 8 }}>{t('analytics.recent')}</div>
          <div style={{ overflowY: 'auto', flex: 1 }}>
            {!Array.isArray(stats.recent_rejections) || stats.recent_rejections.length === 0 ? (
              <div style={{ color: 'var(--text-secondary)', fontSize: 12 }}>
                {t('analytics.noRejections')}
              </div>
            ) : (
              stats.recent_rejections.map((r, i) => (
                <div key={i} style={{
                  padding: '8px 10px', marginBottom: 6, borderRadius: 6,
                  background: 'rgba(11, 16, 32, 0.5)', borderLeft: '3px solid var(--warn)',
                }}>
                  <div style={{ fontSize: 12, marginBottom: 2 }}>
                    {r.object_a_name} ⇔ {r.object_b_name}
                    <span className="mono" style={{ fontSize: 10, color: 'var(--text-secondary)', marginLeft: 6 }}>
                      {r.risk_tier?.toUpperCase()} · {r.decided_at?.slice(0, 16).replace('T', ' ')} UTC
                    </span>
                    {r.is_demo ? (
                      <span className="mono" style={{ fontSize: 10, color: 'var(--warn)', marginLeft: 6 }}>◆ {t('common.demo')}</span>
                    ) : null}
                  </div>
                  <div style={{ fontSize: 12, color: 'var(--text-secondary)', fontStyle: r.rejection_reason ? 'normal' : 'italic' }}>
                    {r.rejection_reason || t('analytics.noReason')}
                  </div>
                </div>
              ))
            )}
          </div>
        </>
      )}
    </div>
  )
}
