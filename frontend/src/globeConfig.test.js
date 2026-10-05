import test from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, readFileSync, statSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import {
  EARTH_TEXTURE_FILE, EARTH_TEXTURE_LAYER, GLOBE_FALLBACK_TEXT, INDIA_RECTANGLE, INDIA_VIEW, REGIONAL_HEIGHT_RANGE,
  earthTextureUrl, isRemoteUrl, shouldApplyInitialView, shouldFlyToEvent, webglSupported,
} from './globeConfig.js'

const here = dirname(fileURLToPath(import.meta.url))

test('globe: INDIA_VIEW is centred on India at a regional altitude', () => {
  assert.ok(Math.abs(INDIA_VIEW.lat - 20.6) < 0.5)
  assert.ok(Math.abs(INDIA_VIEW.lon - 79.0) < 0.5)
  assert.ok(INDIA_VIEW.height >= REGIONAL_HEIGHT_RANGE.min && INDIA_VIEW.height <= REGIONAL_HEIGHT_RANGE.max)
  assert.equal(REGIONAL_HEIGHT_RANGE.min, 12_000_000)
  assert.equal(REGIONAL_HEIGHT_RANGE.max, 20_000_000)
  assert.ok(INDIA_RECTANGLE.west < INDIA_VIEW.lon && INDIA_VIEW.lon < INDIA_RECTANGLE.east)
  assert.ok(INDIA_RECTANGLE.south < INDIA_VIEW.lat && INDIA_VIEW.lat < INDIA_RECTANGLE.north)
})

test('globe: initial view is applied once per viewer instance only', () => {
  const v1 = {}
  const v2 = {}
  assert.equal(shouldApplyInitialView(undefined), false)
  assert.equal(shouldApplyInitialView({ viewer: null, appliedTo: null }), false)
  assert.equal(shouldApplyInitialView({ viewer: v1, appliedTo: null }), true)
  // Re-renders, refreshes, language/text-size changes: same viewer -> never again.
  assert.equal(shouldApplyInitialView({ viewer: v1, appliedTo: v1 }), false)
  // A genuinely new viewer instance (remount) gets its own first view.
  assert.equal(shouldApplyInitialView({ viewer: v2, appliedTo: v1 }), true)
})

test('globe: event fly-to only on a newly selected event id', () => {
  assert.equal(shouldFlyToEvent(null, 5), true)
  assert.equal(shouldFlyToEvent(5, 5), false) // poll handed a fresh object for the same event
  assert.equal(shouldFlyToEvent(5, 6), true)
  assert.equal(shouldFlyToEvent(5, null), false)
})

test('globe: webglSupported uses the injected canvas factory', () => {
  const canvas = (ctx) => () => ({ getContext: (k) => ctx[k] || null })
  assert.equal(webglSupported(canvas({ webgl2: {} })), true)
  assert.equal(webglSupported(canvas({ webgl: {} })), true)
  assert.equal(webglSupported(canvas({})), false)
  assert.equal(webglSupported(() => null), false)
  assert.equal(webglSupported(() => { throw new Error('no canvas') }), false)
  assert.equal(webglSupported(() => ({ getContext: () => { throw new Error('blocked') } })), false)
  // No DOM under node and no factory -> unsupported, never throws.
  assert.equal(webglSupported(), false)
})

test('globe: Earth texture is a local file, never a runtime http(s) URL', () => {
  assert.equal(isRemoteUrl(EARTH_TEXTURE_FILE), false)
  assert.equal(isRemoteUrl(earthTextureUrl()), false)
  assert.equal(earthTextureUrl(), `/${EARTH_TEXTURE_FILE}`)
  assert.equal(isRemoteUrl('https:' + '//example.org/x.jpg'), true)
  assert.equal(isRemoteUrl('//cdn.example.org/x.jpg'), true)
  const file = join(here, '..', 'public', EARTH_TEXTURE_FILE)
  assert.ok(existsSync(file), 'texture missing from public/')
  assert.ok(statSync(file).size <= 1_600_000, 'texture too large')
  assert.ok(existsSync(join(here, '..', 'public', 'textures', 'ATTRIBUTION.md')))
  for (const k of ['brightness', 'contrast', 'saturation']) assert.equal(typeof EARTH_TEXTURE_LAYER[k], 'number')
})

test('globe: GlobeView.jsx has no remote http(s) texture/imagery URL', () => {
  const src = readFileSync(join(here, 'components', 'GlobeView.jsx'), 'utf8')
  assert.doesNotMatch(src, /https?:\/\//i)
  assert.match(src, /SingleTileImageryProvider\.fromUrl\(earthTextureUrl\(\)\)/)
  assert.match(src, /NaturalEarthII/) // bundled offline fallback retained
})

test('globe: base imagery layer is created per Viewer, never shared at module level', () => {
  // Cesium destroys a viewer's layers with the viewer; a shared layer left a
  // re-mounted globe (log out -> log in) without its Earth texture.
  const src = readFileSync(join(here, 'components', 'GlobeView.jsx'), 'utf8')
  assert.doesNotMatch(src, /^const \w+ = Cesium\.ImageryLayer\./m)
  assert.match(src, /baseLayer=\{false\}/)
  assert.match(src, /viewer\.imageryLayers\.add\(createBaseLayer\(\), 0\)/)
})

test('globe: fallback panel text and boundary component exist', () => {
  assert.match(GLOBE_FALLBACK_TEXT.title, /3D globe unavailable/)
  assert.match(GLOBE_FALLBACK_TEXT.body, /WebGL could not be initialised/)
  const src = readFileSync(join(here, 'components', 'GlobeErrorBoundary.jsx'), 'utf8')
  assert.match(src, /export default class GlobeErrorBoundary/)
  assert.match(src, /getDerivedStateFromError/)
})
