// Pure globe configuration helpers -- importable WITHOUT Cesium so they can be
// unit-tested under node:test. Presentation only: nothing here touches
// orbital calculations, event detection or track data.

// Default / Home camera: centred on India from a regional altitude so India,
// South Asia and part of the Indian Ocean are visible with surrounding Earth
// context (not city level).
export const INDIA_VIEW = Object.freeze({
  lat: 20.6, // degrees N
  lon: 79.0, // degrees E
  height: 17_000_000, // metres above the ellipsoid (~17,000 km: whole Earth disc visible, India centred)
})

// Acceptable regional-altitude band for the India view (metres).
export const REGIONAL_HEIGHT_RANGE = Object.freeze({ min: 12_000_000, max: 20_000_000 })

// Rectangle (degrees) around South Asia used as Cesium's DEFAULT_VIEW_RECTANGLE
// so the very first frame (before the explicit setView) already faces India.
export const INDIA_RECTANGLE = Object.freeze({ west: 55, south: -5, east: 103, north: 45 })

// Locally served NASA Blue Marble texture (public domain; see
// public/textures/ATTRIBUTION.md). Never a remote URL at runtime.
export const EARTH_TEXTURE_FILE = 'textures/earth_blue_marble_4096.jpg'

function baseUrl() {
  try {
    const b = import.meta.env && import.meta.env.BASE_URL
    if (typeof b === 'string' && b) return b.endsWith('/') ? b : `${b}/`
  } catch { /* not running under Vite */ }
  return '/'
}

export function earthTextureUrl() {
  return `${baseUrl()}${EARTH_TEXTURE_FILE}`
}

// Imagery tuning: slightly dimmed and desaturated so the globe sits inside the
// dark mission-console palette without looking like a flat blue sphere.
export const EARTH_TEXTURE_LAYER = Object.freeze({
  brightness: 0.9,
  contrast: 1.1,
  saturation: 0.85,
  gamma: 1.0,
})

// Fallback imagery layer (Natural Earth II bundled with Cesium) tuning.
export const FALLBACK_LAYER = Object.freeze({ brightness: 0.55 })

export function isRemoteUrl(url) {
  return /^(https?:)?\/\//i.test(String(url || ''))
}

// Once-only initial camera logic: apply the India view only when a viewer
// exists and this viewer instance has not had it applied yet.
// state = { viewer, appliedTo } where appliedTo is the viewer instance the
// view was last applied to (or null).
export function shouldApplyInitialView(state) {
  if (!state || !state.viewer) return false
  return state.appliedTo !== state.viewer
}

// Event-focus fly-to only when the user selects a DIFFERENT event; polls that
// hand us a fresh object for the same event id must not move the camera.
export function shouldFlyToEvent(prevId, nextId) {
  return nextId != null && nextId !== prevId
}

// WebGL pre-check with an injectable canvas factory (defaults to the DOM).
export function webglSupported(createCanvas) {
  try {
    const make = createCanvas || (
      typeof document !== 'undefined' ? () => document.createElement('canvas') : null
    )
    if (!make) return false
    const canvas = make()
    if (!canvas || typeof canvas.getContext !== 'function') return false
    const gl = canvas.getContext('webgl2') || canvas.getContext('webgl')
      || canvas.getContext('experimental-webgl')
    return Boolean(gl)
  } catch {
    return false
  }
}

export const GLOBE_FALLBACK_TEXT = Object.freeze({
  title: '3D globe unavailable',
  body: 'WebGL could not be initialised. Event, risk and evidence panels remain fully available.',
})
