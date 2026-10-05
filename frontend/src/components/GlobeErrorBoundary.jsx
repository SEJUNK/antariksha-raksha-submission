import { Component } from 'react'
import { GLOBE_FALLBACK_TEXT } from '../globeConfig.js'

// Dark-theme panel shown instead of the Cesium globe when WebGL is missing or
// the viewer fails to initialise/render. The rest of the console (event feed,
// risk, evidence panels) keeps working.
export function GlobeFallback({ title, body }) {
  return (
    <div
      role="status"
      style={{
        position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center',
        background: 'radial-gradient(ellipse at center, #13294B 0%, #0B1020 70%)', padding: 16,
      }}
    >
      <div style={{
        maxWidth: 440, padding: '18px 22px', borderRadius: 12, textAlign: 'center',
        background: 'rgba(19, 41, 75, 0.72)', border: '1px solid rgba(0, 194, 255, 0.25)',
        color: '#E8EEF7', fontFamily: '"IBM Plex Sans", var(--font-indic), sans-serif',
      }}>
        <div style={{ fontSize: 15, fontWeight: 600, color: '#00C2FF', marginBottom: 8 }}>
          {title || GLOBE_FALLBACK_TEXT.title}
        </div>
        <div style={{ fontSize: 13, lineHeight: 1.5, color: '#8FA3BF' }}>
          {body || GLOBE_FALLBACK_TEXT.body}
        </div>
      </div>
    </div>
  )
}

export default class GlobeErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { failed: false }
  }

  static getDerivedStateFromError() {
    return { failed: true }
  }

  componentDidCatch(error) {
    console.error('3D globe failed to initialise; showing fallback panel', error)
  }

  render() {
    if (this.state.failed) {
      const { fallbackTitle, fallbackText } = this.props
      return <GlobeFallback title={fallbackTitle} body={fallbackText} />
    }
    return this.props.children
  }
}
