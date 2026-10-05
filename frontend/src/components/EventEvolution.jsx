import { useI18n } from '../i18n'
import { tierLabelKey } from '../i18n/core'

// "Event Evolution": stored observations of this tracked encounter across
// screening runs (view model: ../eventEvolution.js). Neutral wording only --
// trends describe stored values, not intent or confidence.

const cell = { padding: '4px 8px', borderBottom: '1px solid var(--border-line)', whiteSpace: 'nowrap', verticalAlign: 'top' }
const head = { ...cell, fontSize: 11, color: 'var(--text-secondary)', fontWeight: 400, textAlign: 'left' }

function TierText({ tier }) {
  const { t } = useI18n()
  if (!tier) return 'N/A'
  const k = tierLabelKey(tier)
  return <span title={k ? `${tier} — ${t(k)}` : undefined}>{tier}</span>
}

function RunLabel({ row }) {
  const { t } = useI18n()
  return (
    <>
      <div style={{ fontWeight: row.isCurrent ? 700 : 600, color: row.isCurrent ? 'var(--accent)' : 'var(--text-primary)' }}>
        {t(row.labelKey, row.labelVars)}
        {row.isDemo && <span style={{ color: 'var(--warn)', fontWeight: 400 }}> · ◆ {t('common.demo')}</span>}
        {row.ambiguous && <span style={{ color: 'var(--text-secondary)', fontWeight: 400 }} title={t('evo.ambiguous')}> *</span>}
      </div>
      {(row.runId != null || row.runTime) && (
        <div style={{ fontSize: 11, color: 'var(--text-secondary)', whiteSpace: 'normal' }}>
          {t('evo.run', { id: row.runId ?? '—', time: row.runTime ?? '—' })}
        </div>
      )}
    </>
  )
}

const pcText = (row, t) => (row.pcNotComputed ? t('evo.pcNA') : row.pc ?? 'N/A')

function EvolutionTable({ rows }) {
  const { t, tip } = useI18n()
  return (
    <div style={{ overflowX: 'auto', maxWidth: '100%' }}>
      <table className="mono" style={{ borderCollapse: 'collapse', fontSize: 11, minWidth: '100%' }}>
        <thead>
          <tr>
            <th style={head}>{t('evo.colObs')}</th>
            <th style={head} title={tip('TCA')}>{t('evo.colTca')}</th>
            <th style={head}>{t('evo.colMiss')}</th>
            <th style={head}>{t('evo.colRelVel')}</th>
            <th style={head} title={tip('Pc')}>{t('evo.colPc')}</th>
            <th style={head}>{t('evo.colTier')}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.key} style={{ background: r.isCurrent ? 'var(--accent-dim)' : 'transparent' }}>
              <td style={cell}><RunLabel row={r} /></td>
              <td style={cell}>{r.tca ?? 'N/A'}</td>
              <td style={cell}>{r.miss ?? 'N/A'}</td>
              <td style={cell}>{r.relVel ?? 'N/A'}</td>
              <td style={{ ...cell, color: r.pcNotComputed ? 'var(--text-secondary)' : undefined }}>{pcText(r, t)}</td>
              <td style={cell}><TierText tier={r.tier} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// Narrow drawer / large text: one stacked card per observation.
function EvolutionCards({ rows }) {
  const { t, tip } = useI18n()
  const line = (label, value, title) => (
    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
      <span style={{ color: 'var(--text-secondary)', fontSize: 11 }} title={title}>{label}</span>
      <span>{value}</span>
    </div>
  )
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 8 }}>
      {rows.map((r) => (
        <div key={r.key} className="mono" style={{
          fontSize: 11, padding: 8, borderRadius: 6, minWidth: 0,
          border: `1px solid ${r.isCurrent ? 'var(--accent)' : 'var(--border-line)'}`,
        }}>
          <RunLabel row={r} />
          <div style={{ marginTop: 4 }}>
            {line(t('evo.colTca'), r.tca ?? 'N/A', tip('TCA'))}
            {line(t('evo.colMiss'), r.miss ?? 'N/A')}
            {line(t('evo.colRelVel'), r.relVel ?? 'N/A')}
            {line(t('evo.colPc'), pcText(r, t), tip('Pc'))}
            {line(t('evo.colTier'), <TierText tier={r.tier} />)}
          </div>
        </div>
      ))}
    </div>
  )
}

export default function EventEvolution({ vm, error, compact }) {
  const { t, tip } = useI18n()
  if (error && !vm) return <div className="mono" style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{t('evo.unavailable')}</div>
  if (!vm) return <div className="mono" style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{t('common.loading')}</div>
  return (
    <div>
      {vm.demoBanner && (
        <div className="mono" style={{
          marginBottom: 8, padding: '4px 8px', borderRadius: 6, fontSize: 11, color: 'var(--warn)',
          border: '1px solid var(--warn)', background: 'color-mix(in srgb, var(--warn) 12%, transparent)',
        }}>
          ◆ {t(vm.demoBanner.key)}
          {vm.demoBanner.stepKey && <span> · {t(vm.demoBanner.stepKey, vm.demoBanner.stepVars)}</span>}
        </div>
      )}
      {vm.rows.length > 0 && (compact ? <EvolutionCards rows={vm.rows} /> : <EvolutionTable rows={vm.rows} />)}
      {vm.noteKeys.map((k) => (
        <div key={k} style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 6, fontStyle: 'italic' }}>{t(k)}</div>
      ))}
      {vm.trends.length > 0 && (
        <div style={{ marginTop: 8 }}>
          <div className="mono" style={{ fontSize: 11, color: 'var(--text-secondary)', marginBottom: 4 }}
            title={vm.stableTip ? t(vm.stableTip.key, vm.stableTip.vars) : undefined}>
            {t('evo.trends')}
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            {vm.trends.map((tr) => (
              <span key={tr.id} className="mono" style={{
                fontSize: 11, padding: '2px 8px', borderRadius: 10,
                border: '1px solid var(--border-line)', color: 'var(--text-primary)',
              }}>
                {tr.glyph} {t(tr.key, tr.vars)}
              </span>
            ))}
          </div>
        </div>
      )}
      {vm.pcChange && (
        <div style={{ marginTop: 8 }}>
          {/* Collision indicator change, separate from the risk-tier trend:
              stored Pc values only, no direction wording. */}
          <span className="mono" title={tip('Pc')} style={{
            fontSize: 11, padding: '2px 8px', borderRadius: 10,
            border: '1px solid var(--border-line)', color: 'var(--text-primary)', display: 'inline-block',
          }}>
            {t(vm.pcChange.key, vm.pcChange.vars)}
            {vm.pcChange.ratio && <span title={t('delta.pcRatioTip')} style={{ color: 'var(--text-secondary)' }}> (×{vm.pcChange.ratio})</span>}
          </span>
        </div>
      )}
      {vm.ambiguous && (
        <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 6 }}>* {t('evo.ambiguous')}</div>
      )}
      {vm.correlation && (
        <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 4 }}>{t(vm.correlation.key, vm.correlation.vars)}</div>
      )}
    </div>
  )
}
