// Pure UI text-size (zoom) logic -- no React, no DOM -- so node:test can cover
// it. The scale is applied with CSS `zoom` to the header and the overlay
// panels/drawers only, NEVER to the Cesium globe canvas (zooming the canvas
// breaks entity picking).

export const SCALE_STEPS = [0.9, 1, 1.15, 1.3, 1.5, 1.75]
export const DEFAULT_SCALE = 1
export const SCALE_STORAGE_KEY = 'antariksha.uiScale'

// Layout constants (unscaled CSS px) used to keep panels on-screen.
const FEED_SPAN = 16 + 340 + 16 // left gutter + event feed + gap
const ASSET_SPAN = 280 + 16 + 16 // asset panel + right gutter + gap
const MIN_GLOBE_GAP = 40 // keep a sliver of globe visible between side panels
const MIN_PANEL_CONTENT_H = 360 // header + a usable panel body
const DRAWER_MIN_WIDTH = 520 // below this the drawer spans the full width
const DELTA_MIN_WIDTH = 480 // below this the change-summary card spans the full width
const DELTA_MAX_WIDTH = 680
const HEADER_SPAN = 64 // header (48) + gap
const DELTA_MIN_HEIGHT_WITH_DRAWER = 130 // title + run line + demo banner + counts row; less -> pill
export const DELTA_PILL_HEIGHT = 40 // one-line "What changed?" pill (unscaled px)
export const HEADER_MAX_COMPACT = 7

/** Snap any value to the nearest allowed step (invalid -> default). */
export function normalizeScale(value) {
  const n = typeof value === 'string' ? Number.parseFloat(value) : value
  if (typeof n !== 'number' || !Number.isFinite(n)) return DEFAULT_SCALE
  let best = SCALE_STEPS[0]
  for (const s of SCALE_STEPS) if (Math.abs(s - n) < Math.abs(best - n)) best = s
  return best
}

export function stepScale(current, direction) {
  const idx = SCALE_STEPS.indexOf(normalizeScale(current))
  const next = Math.min(SCALE_STEPS.length - 1, Math.max(0, idx + (direction > 0 ? 1 : -1)))
  return SCALE_STEPS[next]
}

export const canIncrease = (s) => normalizeScale(s) < SCALE_STEPS[SCALE_STEPS.length - 1]
export const canDecrease = (s) => normalizeScale(s) > SCALE_STEPS[0]

/** "115%" -- numeric display, identical in every language. */
export function formatScale(s) {
  return `${Math.round(normalizeScale(s) * 100)}%`
}

export function loadScale(storage) {
  try {
    const v = storage?.getItem?.(SCALE_STORAGE_KEY)
    return v == null ? DEFAULT_SCALE : normalizeScale(v)
  } catch {
    return DEFAULT_SCALE
  }
}

export function saveScale(storage, scale) {
  try {
    storage?.setItem?.(SCALE_STORAGE_KEY, String(normalizeScale(scale)))
    return true
  } catch {
    return false
  }
}

/**
 * Scale actually applied for this viewport: the selected step, reduced (never
 * below the smallest step) only if the side panels would otherwise overlap
 * each other or the panels could not fit vertically. On 1920x1080 every step
 * up to 175% applies unchanged.
 */
export function effectiveScale(selected, viewportW, viewportH) {
  const s = normalizeScale(selected)
  if (!(viewportW > 0) || !(viewportH > 0)) return s
  const fitW = viewportW / (FEED_SPAN + ASSET_SPAN + MIN_GLOBE_GAP)
  const fitH = viewportH / MIN_PANEL_CONTENT_H
  return Math.max(SCALE_STEPS[0], Math.min(s, fitW, fitH))
}

/**
 * Geometry for the zoomed overlays, in UNSCALED px (CSS zoom multiplies them).
 * Heights are computed from the real viewport so nothing relies on vh inside
 * a zoomed element.
 */
export function overlayLayout(scale, viewportW, viewportH) {
  const z = scale > 0 ? scale : 1
  const vw = viewportW / z // viewport width in zoomed units
  const vh = viewportH / z
  const middle = vw - FEED_SPAN - ASSET_SPAN
  const drawerFull = middle < DRAWER_MIN_WIDTH
  const drawerMaxHeight = Math.max(160, Math.round(vh * (z > 1.2 ? 0.62 : 0.55)))
  const deltaFull = middle < DELTA_MIN_WIDTH
  return {
    // Drawer: between the side panels when there is room, otherwise full
    // width above them (it is closable with ESC / ✕).
    drawer: drawerFull
      ? { left: 16, right: 16 }
      : { left: FEED_SPAN, right: ASSET_SPAN },
    drawerFull,
    // Drawer may use more of the screen when large text makes it taller.
    drawerMaxHeight,
    analyticsMaxHeight: Math.max(160, Math.round(vh - 64 - 32)),
    // Drawer content width in zoomed units: narrow -> single/auto-fit columns.
    compactGrid: (drawerFull ? vw - 32 : middle) - 40 < 600,
    // Globe caption must fit in the gap between the side panels (else it hides).
    captionMaxWidth: Math.max(0, Math.round(middle - 24)),
    // Header width in zoomed units drives which low-priority items hide.
    // "What changed since last refresh?" card, under the header. Between the
    // side panels when there is room, otherwise full width above them. When
    // the drawer is open the card is capped so the two never overlap.
    deltaCard: deltaFull
      ? { left: 16, right: 16, top: HEADER_SPAN, maxWidth: DELTA_MAX_WIDTH }
      : { left: FEED_SPAN, right: ASSET_SPAN, top: HEADER_SPAN, maxWidth: DELTA_MAX_WIDTH },
    deltaFull,
    deltaMaxHeight: Math.max(120, Math.round(vh * 0.45)),
    deltaMaxHeightWithDrawer: Math.max(0, Math.round(vh - HEADER_SPAN - 16 - drawerMaxHeight - 12)),
    // Too little height for both (short screens at large text): the card
    // collapses to a one-line pill above the drawer (never hidden); the pill
    // expands over the drawer with internal scrolling.
    deltaFitsWithDrawer: vh - HEADER_SPAN - 16 - drawerMaxHeight - 12 >= DELTA_MIN_HEIGHT_WITH_DRAWER,
    deltaModeWithDrawer: vh - HEADER_SPAN - 16 - drawerMaxHeight - 12 >= DELTA_MIN_HEIGHT_WITH_DRAWER ? 'card' : 'pill',
    deltaPillHeight: DELTA_PILL_HEIGHT,
    deltaExpandedMaxHeight: Math.max(120, Math.round(vh - HEADER_SPAN - 16)),
    headerCompact: vw < 900 ? 4 : vw < 1100 ? 3 : vw < 1280 ? 2 : vw < 1440 ? 1 : 0,
  }
}

/**
 * Adaptive header compaction. `base` comes from the viewport width (see
 * overlayLayout().headerCompact); while the header content is still wider
 * than the header (long labels in some languages, large text), step one more
 * level. Levels: 1 tighter gaps, 2 no subtitle / language status, 3 no clock
 * + short event chip, 4 status dot only + no local-script brand, 5 icon-only
 * analytics + narrower language list, 6 brand text hidden (dot keeps title),
 * 7 text-size reset (percentage) hidden. The signed-in user's name hides from
 * level 3 (role chip stays) and Users / Log out turn into icons from level 5.
 */
export function nextHeaderCompact(level, scrollWidth, clientWidth) {
  const l = Number.isFinite(level) ? Math.max(0, Math.min(HEADER_MAX_COMPACT, level)) : 0
  if (!(scrollWidth > 0) || !(clientWidth > 0)) return l
  return scrollWidth > clientWidth + 1 && l < HEADER_MAX_COMPACT ? l + 1 : l
}

/** Short, language-independent event chip: "● 1 · CRITICAL" / "● 0". */
export function shortEventChip(count, tierCode) {
  const n = Number.isFinite(count) && count > 0 ? count : 0
  if (n === 0) return '● 0'
  return `● ${n} · ${String(tierCode || 'N/A').toUpperCase()}`
}
