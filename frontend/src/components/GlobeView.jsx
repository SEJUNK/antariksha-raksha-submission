import { useEffect, useMemo, useRef, useState } from 'react'
import { Viewer, Entity, PolylineGraphics } from 'resium'
import * as Cesium from 'cesium'
import { api } from '../api'
import { useI18n } from '../i18n'
import { useUiScale } from '../uiScaleContext'

import {
  EARTH_TEXTURE_LAYER, FALLBACK_LAYER, GLOBE_FALLBACK_TEXT, INDIA_RECTANGLE, INDIA_VIEW,
  earthTextureUrl, shouldApplyInitialView, shouldFlyToEvent, webglSupported,
} from '../globeConfig.js'
import { GlobeFallback } from './GlobeErrorBoundary.jsx'

// Cesium >= 1.107 removed the Viewer `imageryProvider` option -- the base
// imagery must now be passed as an ImageryLayer via `baseLayer`.
// Base imagery: NASA Blue Marble (public domain) as ONE equirectangular
// texture served locally from /public/textures (no API key, no network
// dependency at runtime -- see public/textures/ATTRIBUTION.md). If it fails
// to load, fall back to the Natural Earth II tiles bundled with Cesium
// (also served locally by vite-plugin-cesium).
//
// A NEW layer is created for every Viewer instance: Cesium destroys a viewer's
// imagery layers with the viewer, so a shared module-level layer left every
// later viewer (e.g. log out -> log in without a page reload) with a destroyed
// layer and a texture-less dark-blue globe.
function createBaseLayer() {
  const layer = Cesium.ImageryLayer.fromProviderAsync(
    Cesium.SingleTileImageryProvider.fromUrl(earthTextureUrl()).catch((err) => {
      console.warn('Blue Marble texture unavailable; using bundled Natural Earth II', err)
      return Cesium.TileMapServiceImageryProvider.fromUrl(
        Cesium.buildModuleUrl('Assets/Textures/NaturalEarthII'),
      ).then((provider) => {
        layer.brightness = FALLBACK_LAYER.brightness
        layer.contrast = 1.0
        layer.saturation = 1.0
        return provider
      })
    }),
    { ...EARTH_TEXTURE_LAYER },
  )
  return layer
}

// First frame (and camera.flyHome) already faces South Asia instead of the
// Cesium default (North America).
Cesium.Camera.DEFAULT_VIEW_RECTANGLE = Cesium.Rectangle.fromDegrees(
  INDIA_RECTANGLE.west, INDIA_RECTANGLE.south, INDIA_RECTANGLE.east, INDIA_RECTANGLE.north,
)
Cesium.Camera.DEFAULT_VIEW_FACTOR = 0.5

const indiaDestination = () => Cesium.Cartesian3.fromDegrees(INDIA_VIEW.lon, INDIA_VIEW.lat, INDIA_VIEW.height)
function flyToIndia(viewer, duration = 1.5) {
  viewer.camera.flyTo({ destination: indiaDestination(), duration })
}
const FLAT_TERRAIN = new Cesium.EllipsoidTerrainProvider()

const TIER_CSS = { Critical: '#EB5757', High: '#F2994A', Medium: '#F2C94C', Low: '#27AE60' }

// Time-lapse speed: 60x real time = one LEO orbit (~95 min) plays in ~95 s.
const CLOCK_MULTIPLIER = 60
const TRACK_WINDOW_MINUTES = 120

// Glowing radial-gradient sprites drawn once per color -- much better
// "satellite" look than flat Cesium points.
const spriteCache = {}
function makeSprite(colorHex, size = 48) {
  const key = `${colorHex}-${size}`
  if (spriteCache[key]) return spriteCache[key]
  const canvas = document.createElement('canvas')
  canvas.width = canvas.height = size
  const ctx = canvas.getContext('2d')
  const c = size / 2
  const g = ctx.createRadialGradient(c, c, 0, c, c, c)
  g.addColorStop(0, '#ffffff')
  g.addColorStop(0.22, colorHex)
  g.addColorStop(0.55, `${colorHex}66`)
  g.addColorStop(1, `${colorHex}00`)
  ctx.fillStyle = g
  ctx.fillRect(0, 0, size, size)
  spriteCache[key] = canvas.toDataURL()
  return spriteCache[key]
}

function styleFor(track, isFlagged, flagColor) {
  if (isFlagged) return { sprite: makeSprite(flagColor), size: 34 }
  if (track.object_type === 'satellite') {
    if (track.criticality === 'Tier1') return { sprite: makeSprite('#00C2FF'), size: 30 }
    return { sprite: makeSprite('#00C2FF'), size: 22 }
  }
  if (track.object_type === 'foreign_sat') return { sprite: makeSprite('#E8EEF7'), size: 18 }
  return { sprite: makeSprite('#5A6B85'), size: 12 }
}

function buildPositionProperty(track) {
  const prop = new Cesium.SampledPositionProperty()
  const start = Cesium.JulianDate.fromIso8601(track.start)
  const scratch = new Cesium.JulianDate()
  for (let i = 0; i < track.lat.length; i++) {
    const t = Cesium.JulianDate.addSeconds(start, i * track.step_seconds, new Cesium.JulianDate())
    prop.addSample(t, Cesium.Cartesian3.fromDegrees(track.lon[i], track.lat[i], track.alt_km[i] * 1000))
  }
  prop.setInterpolationOptions({
    interpolationDegree: 2,
    interpolationAlgorithm: Cesium.LagrangePolynomialApproximation,
  })
  return prop
}

export default function GlobeView({ events = [], selectedEvent, onSelectObject, reloadKey = 0, objectsById = {} }) {
  const [tracks, setTracks] = useState([])
  const { t, tip } = useI18n()
  const { scale, layout } = useUiScale() // caption overlay only; the Cesium canvas is never zoomed
  // Plain strings (not `t`) as memo deps so Cesium entities rebuild only when
  // the language actually changes.
  const demoAdjLabel = t('globe.demoAdjusted')
  const proximityLabel = t('globe.proximity')
  const demoLabel = t('common.demo')
  const viewerRef = useRef(null)
  // WebGL pre-check before mounting the Cesium Viewer; render-loop failures
  // after mount also switch to the fallback panel instead of crashing.
  const [webglOk] = useState(() => webglSupported())
  const [renderFailed, setRenderFailed] = useState(false)
  const fallbackTitle = (() => { const v = t('globe.unavailableTitle'); return v === 'globe.unavailableTitle' ? GLOBE_FALLBACK_TEXT.title : v })()
  const fallbackBody = (() => { const v = t('globe.unavailableBody'); return v === 'globe.unavailableBody' ? GLOBE_FALLBACK_TEXT.body : v })()

  // One-time scene initialisation per viewer instance: India default camera,
  // Home override, atmosphere/background polish. Guarded by the viewer
  // identity so refreshes, data/object updates, language or text-size
  // changes and re-renders never re-apply it or move the camera.
  const initialViewAppliedTo = useRef(null)
  useEffect(() => {
    const viewer = viewerRef.current?.cesiumElement
    if (!shouldApplyInitialView({ viewer, appliedTo: initialViewAppliedTo.current })) return
    initialViewAppliedTo.current = viewer
    viewer.imageryLayers.add(createBaseLayer(), 0) // this viewer's own base imagery
    viewer.camera.setView({ destination: indiaDestination() })
    // Home = India regional view (home button, if shown, and camera.flyHome).
    viewer.camera.flyHome = (duration) => flyToIndia(viewer, duration)
    const homeCmd = viewer.homeButton?.viewModel?.command
    if (homeCmd?.beforeExecute) {
      homeCmd.beforeExecute.addEventListener((e) => { e.cancel = true; flyToIndia(viewer) })
    }
    const scene = viewer.scene
    scene.backgroundColor = Cesium.Color.fromCssColorString('#0B1020')
    scene.globe.baseColor = Cesium.Color.fromCssColorString('#0B1020')
    scene.globe.enableLighting = false // whole globe stays readable (no night side)
    scene.globe.showGroundAtmosphere = true
    if (scene.skyAtmosphere) {
      scene.skyAtmosphere.show = true
      scene.skyAtmosphere.brightnessShift = -0.15 // subtle limb glow
    }
    if (scene.skyBox) scene.skyBox.show = true // keep the star background
    scene.renderError.addEventListener((_scene, err) => {
      console.error('Cesium render loop failed; showing globe fallback', err)
      setRenderFailed(true)
    })
    const creditEl = viewer.bottomContainer
    if (creditEl) {
      creditEl.style.opacity = '0.35'
      creditEl.style.filter = 'grayscale(1)'
      creditEl.style.transform = 'scale(0.8)'
      creditEl.style.transformOrigin = 'bottom left'
    }
  })

  useEffect(() => {
    let cancelled = false
    api.getTracks().then((data) => {
      if (!cancelled) setTracks(data)
    }).catch((err) => console.error('Failed to fetch /api/tracks', err))
    return () => { cancelled = true }
  }, [reloadKey])

  // Time-lapse clock, applied once tracks arrive (never moves the camera).
  useEffect(() => {
    const viewer = viewerRef.current?.cesiumElement
    if (!viewer || tracks.length === 0) return
    const start = Cesium.JulianDate.fromIso8601(tracks[0].start)
    const stop = Cesium.JulianDate.addSeconds(
      start, (tracks[0].lat.length - 1) * tracks[0].step_seconds, new Cesium.JulianDate(),
    )
    viewer.clock.startTime = start
    viewer.clock.currentTime = start.clone()
    viewer.clock.stopTime = stop
    viewer.clock.clockRange = Cesium.ClockRange.LOOP_STOP
    viewer.clock.multiplier = CLOCK_MULTIPLIER
    viewer.clock.shouldAnimate = true
  }, [tracks])

  const flagInfo = useMemo(() => {
    const m = {}
    for (const e of events) {
      const color = TIER_CSS[e.risk_tier] || '#F2994A'
      m[e.object_a_id] = color
      m[e.object_b_id] = color
    }
    return m
  }, [events])

  // Objects whose orbit was adjusted by the disclosed demo seed get a
  // visible "(DEMO-ADJUSTED)" tag wherever their name is shown on the globe.
  // Reduced to a stable string key so App re-renders (new objectsById
  // object every poll) don't rebuild every Cesium position property.
  const demoAdjustedKey = Object.values(objectsById || {})
    .filter((o) => o && o.demo_adjusted)
    .map((o) => o.norad_id)
    .sort()
    .join(',')

  const trackEntities = useMemo(() => {
    const adjusted = new Set(demoAdjustedKey ? demoAdjustedKey.split(',') : [])
    const isDemoAdjusted = (track) => Boolean(track.demo_adjusted || adjusted.has(String(track.norad_id)))
    const displayName = (track) => (isDemoAdjusted(track) ? `${track.name} (${demoAdjLabel})` : track.name)
    return tracks.map((track) => {
    const flagColor = flagInfo[track.norad_id]
    const isFlagged = Boolean(flagColor)
    const { sprite, size } = styleFor(track, isFlagged, flagColor)
    return {
      track,
      positionProperty: buildPositionProperty(track),
      sprite,
      size,
      isFlagged,
      // Flagged objects get NO name label of their own -- the alert marker
      // chip already carries the name, and doubling up caused overlapping
      // garbled text (esp. proximity pairs, which sit within a pixel of
      // each other at globe zoom).
      label: displayName(track),
      showLabel: (track.criticality === 'Tier1' || isDemoAdjusted(track)) && !isFlagged,
      showPath: track.criticality === 'Tier1' || isFlagged,
      pathColor: isFlagged ? flagColor : '#00C2FF',
    }
  })
  }, [tracks, flagInfo, demoAdjustedKey, demoAdjLabel])

  const positionsById = useMemo(() => {
    const m = {}
    for (const te of trackEntities) m[te.track.norad_id] = te.positionProperty
    return m
  }, [trackEntities])

  // Camera fly on event selection, using the asset's current animated
  // position; fly back out to the India home view when the event is
  // deselected (drawer closed, or event resolved via approve/dismiss).
  // Keyed on the event ID: App hands us a fresh event object on every poll
  // and positionsById rebuilds when events/tracks change -- neither may
  // re-fly the camera away from where the user has moved it.
  const flownEventIdRef = useRef(null)
  const selectedEventId = selectedEvent ? selectedEvent.id : null
  useEffect(() => {
    const viewer = viewerRef.current?.cesiumElement
    if (!viewer) return
    if (selectedEvent) {
      if (!shouldFlyToEvent(flownEventIdRef.current, selectedEventId)) return
      const prop = positionsById[selectedEvent.object_a_id]
      if (!prop) return // tracks not loaded yet; retried when they arrive
      const pos = prop.getValue(viewer.clock.currentTime)
      if (!pos) return
      flownEventIdRef.current = selectedEventId
      const carto = Cesium.Cartographic.fromCartesian(pos)
      const dest = Cesium.Cartesian3.fromRadians(
        carto.longitude, carto.latitude, carto.height + 6_000_000,
      )
      viewer.camera.flyTo({ destination: dest, duration: 1.5 })
    } else if (flownEventIdRef.current != null) {
      flownEventIdRef.current = null
      flyToIndia(viewer)
    }
  }, [selectedEventId, positionsById]) // eslint-disable-line react-hooks/exhaustive-deps

  // ONE alert chip per asset, showing its single worst event -- an asset can
  // have several simultaneous events (e.g. a proximity watch AND a
  // conjunction with the same object), and one chip per event stacked
  // unreadably at the same screen position.
  const alertMarkers = useMemo(() => {
    const TIER_RANK = { Critical: 3, High: 2, Medium: 1, Low: 0 }
    const byAsset = new Map()
    for (const evt of events) {
      const cur = byAsset.get(evt.object_a_id)
      if (!cur) byAsset.set(evt.object_a_id, { evt, count: 1 })
      else {
        cur.count += 1
        if ((TIER_RANK[evt.risk_tier] || 0) > (TIER_RANK[cur.evt.risk_tier] || 0)) cur.evt = evt
      }
    }
    return [...byAsset.values()].map(({ evt, count }) => {
      const te = trackEntities.find((t) => t.track.norad_id === evt.object_a_id)
      if (!te) return null
      const other = trackEntities.find((t) => t.track.norad_id === evt.object_b_id)
      const color = TIER_CSS[evt.risk_tier] || '#F2994A'
      let label = evt.event_class === 'collision_risk'
        ? `⚠ ${String(evt.risk_tier || '').toUpperCase()} — ${te.label}${other && other.label !== other.track.name ? ` ⟷ ${other.label}` : ''}`
        : `◉ ${proximityLabel} — ${te.label}${other ? ` ⟷ ${other.label}` : ''}`
      if (evt.is_demo) label += `  [${demoLabel}]`
      if (count > 1) label += `  [${t('globe.more', { n: count - 1 })}]`
      return { evt, te, color, label, pulse: evt.risk_tier === 'Critical' }
    }).filter(Boolean)
  }, [events, trackEntities, proximityLabel, demoLabel])

  const pendingCollisions = events.filter((e) => e.event_class === 'collision_risk')

  if (!webglOk || renderFailed) {
    return <GlobeFallback title={fallbackTitle} body={fallbackBody} />
  }

  return (
    <>
      <Viewer
        ref={viewerRef}
        full
        baseLayer={false}
        terrainProvider={FLAT_TERRAIN}
        baseLayerPicker={false}
        geocoder={false}
        homeButton={false}
        sceneModePicker={false}
        navigationHelpButton={false}
        animation={false}
        timeline={false}
        fullscreenButton={false}
        infoBox={false}
        selectionIndicator={false}
        showRenderLoopErrors={false}
      >
        {trackEntities.map(({ track, label, positionProperty, sprite, size, isFlagged, showLabel, showPath, pathColor }) => (
          <Entity
            key={track.norad_id}
            name={label}
            position={positionProperty}
            billboard={{ image: sprite, width: size, height: size }}
            label={showLabel ? {
              text: label,
              font: '11px "IBM Plex Mono", "Nirmala UI", "Noto Sans Devanagari", sans-serif',
              fillColor: Cesium.Color.fromCssColorString('#E8EEF7'),
              pixelOffset: new Cesium.Cartesian2(14, 10),
              showBackground: true,
              backgroundColor: Cesium.Color.fromCssColorString('#0B1020').withAlpha(0.7),
              backgroundPadding: new Cesium.Cartesian2(5, 3),
              horizontalOrigin: Cesium.HorizontalOrigin.LEFT,
            } : undefined}
            path={showPath ? {
              leadTime: 0,
              trailTime: 2700, // 45 min of orbit trail behind the object
              width: 1.5,
              material: new Cesium.ColorMaterialProperty(
                Cesium.Color.fromCssColorString(pathColor).withAlpha(isFlagged ? 0.6 : 0.3),
              ),
            } : undefined}
            onClick={() => onSelectObject && onSelectObject({ norad_id: track.norad_id, name: track.name })}
          />
        ))}
        {alertMarkers.map(({ evt, te, color, label, pulse }) => (
          <Entity
            key={`alert-${evt.id}`}
            position={te.positionProperty}
            point={{
              // Critical rings pulse to draw the eye; other tiers stay steady.
              pixelSize: pulse
                ? new Cesium.CallbackProperty(
                    () => 28 + 7 * Math.sin(performance.now() / 280), false,
                  )
                : 30,
              color: Cesium.Color.fromCssColorString(color).withAlpha(0.1),
              outlineColor: Cesium.Color.fromCssColorString(color),
              outlineWidth: 2,
            }}
            label={{
              text: label,
              font: '600 13px "IBM Plex Mono", "Nirmala UI", "Noto Sans Devanagari", sans-serif',
              fillColor: Cesium.Color.fromCssColorString(color),
              showBackground: true,
              backgroundColor: Cesium.Color.fromCssColorString('#0B1020').withAlpha(0.85),
              backgroundPadding: new Cesium.Cartesian2(8, 5),
              pixelOffset: new Cesium.Cartesian2(0, -34),
              verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
            }}
            onClick={() => onSelectObject && onSelectObject({ norad_id: evt.object_a_id, name: te.track.name })}
          />
        ))}
        {pendingCollisions.map((evt) => {
          const propA = positionsById[evt.object_a_id]
          const propB = positionsById[evt.object_b_id]
          if (!propA || !propB) return null
          return (
            <Entity key={`line-${evt.id}`}>
              <PolylineGraphics
                positions={new Cesium.CallbackProperty((time) => {
                  const a = propA.getValue(time)
                  const b = propB.getValue(time)
                  return a && b ? [a, b] : []
                }, false)}
                width={2}
                material={new Cesium.PolylineDashMaterialProperty({
                  color: Cesium.Color.fromCssColorString('#F2994A'),
                })}
              />
            </Entity>
          )
        })}
      </Viewer>
      <div title={tip('SGP4')} style={{
        position: 'absolute', bottom: 20, left: '50%', transform: 'translateX(-50%)',
        zIndex: 15, padding: '5px 14px', borderRadius: 12, zoom: scale, whiteSpace: 'nowrap',
        maxWidth: layout.captionMaxWidth, overflow: 'hidden', textOverflow: 'ellipsis',
        display: layout.captionMaxWidth < 160 ? 'none' : undefined,
        background: 'rgba(19, 41, 75, 0.72)', border: '1px solid rgba(0, 194, 255, 0.18)',
        backdropFilter: 'blur(8px)',
        fontFamily: '"IBM Plex Mono", var(--font-indic), monospace', fontSize: 11, color: '#8FA3BF',
        letterSpacing: '0.08em',
      }}>
        {t('globe.caption', { x: CLOCK_MULTIPLIER, min: TRACK_WINDOW_MINUTES })}
      </div>
    </>
  )
}
