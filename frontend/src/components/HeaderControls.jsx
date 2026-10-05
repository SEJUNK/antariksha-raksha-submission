// Header controls shared by the console header and the login screen:
// local UI language selector and text-size (A− 100% A+) control.
import { useI18n } from '../i18n'
import { LANGUAGES } from '../i18n/core'
import { useUiScale } from '../uiScaleContext'
import { canDecrease, canIncrease, formatScale } from '../uiScale'

// Text size for projectors / large monitors (A− 100% A+). Applied with CSS
// zoom to the header and overlay panels only -- never the Cesium canvas.
export function TextSizeControl() {
  const { t } = useI18n()
  const { selected, increase, decrease, reset } = useUiScale()
  return (
    <span className="hdr-scale" role="group" aria-label={t('scale.label')} title={t('scale.label')}
      style={{ display: 'flex', alignItems: 'center', gap: 3 }}>
      <button type="button" className="ui-scale-btn" onClick={decrease} disabled={!canDecrease(selected)}
        aria-label={t('scale.decrease')} title={t('scale.decrease')}>A−</button>
      <button type="button" className="ui-scale-btn mono hdr-scale-reset" onClick={reset}
        aria-label={t('scale.reset')} title={t('scale.reset')} style={{ minWidth: 44 }}>{formatScale(selected)}</button>
      <button type="button" className="ui-scale-btn" onClick={increase} disabled={!canIncrease(selected)}
        aria-label={t('scale.increase')} title={t('scale.increase')}>A+</button>
    </span>
  )
}

// Local UI language selector (bundled translations, no network call).
export function LanguageSelector() {
  const { lang, setLang, t, info } = useI18n()
  return (
    <span className="hdr-lang" style={{ display: 'flex', alignItems: 'center', gap: 6 }} title={`${t('lang.status', { name: info.native })} — ${t('lang.tooltip')}`}>
      <select
        value={lang}
        onChange={(e) => setLang(e.target.value)}
        aria-label={t('lang.label')}
        style={{
          background: 'transparent', color: 'var(--text-secondary)',
          border: '1px solid var(--border-line)', borderRadius: 6,
          padding: '3px 4px', fontSize: 11, cursor: 'pointer', maxWidth: 110,
        }}
      >
        {LANGUAGES.map((l) => (
          <option key={l.code} value={l.code} style={{ background: 'var(--bg-panel-solid)', color: 'var(--text-primary)' }}>
            {l.native}
          </option>
        ))}
      </select>
    </span>
  )
}

