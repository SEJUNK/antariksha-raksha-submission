import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import {
  DEFAULT_SCALE, SCALE_STEPS, SCALE_STORAGE_KEY, canDecrease, canIncrease, effectiveScale, formatScale,
  HEADER_MAX_COMPACT, loadScale, nextHeaderCompact, normalizeScale, overlayLayout, saveScale, shortEventChip, stepScale,
} from './uiScale.js'
import { LANGUAGE_CODES } from './i18n/core.js'

const here = dirname(fileURLToPath(import.meta.url))

function memoryStorage(initial = {}) {
  const data = { ...initial }
  return { getItem: (k) => (k in data ? data[k] : null), setItem: (k, v) => { data[k] = String(v) }, data }
}

test('steps are 90/100/115/130/150/175 % with default 100 %', () => {
  assert.deepEqual(SCALE_STEPS.map(formatScale), ['90%', '100%', '115%', '130%', '150%', '175%'])
  assert.equal(DEFAULT_SCALE, 1)
})

test('normalizeScale snaps and clamps; invalid -> default', () => {
  assert.equal(normalizeScale(1.2), 1.15)
  assert.equal(normalizeScale(5), 1.75)
  assert.equal(normalizeScale(0.1), 0.9)
  assert.equal(normalizeScale('1.3'), 1.3)
  assert.equal(normalizeScale('abc'), 1)
  assert.equal(normalizeScale(NaN), 1)
  assert.equal(normalizeScale(undefined), 1)
})

test('stepScale moves one step and stops at the ends', () => {
  assert.equal(stepScale(1, +1), 1.15)
  assert.equal(stepScale(1, -1), 0.9)
  assert.equal(stepScale(1.75, +1), 1.75)
  assert.equal(stepScale(0.9, -1), 0.9)
  assert.equal(canIncrease(1.75), false)
  assert.equal(canDecrease(0.9), false)
  assert.equal(canIncrease(1), true)
})

test('persistence via injectable storage, robust to failures', () => {
  const s = memoryStorage()
  assert.equal(loadScale(s), 1)
  assert.equal(saveScale(s, 1.5), true)
  assert.equal(s.data[SCALE_STORAGE_KEY], '1.5')
  assert.equal(loadScale(s), 1.5)
  assert.equal(loadScale(memoryStorage({ [SCALE_STORAGE_KEY]: 'junk' })), 1)
  const broken = { getItem: () => { throw new Error('x') }, setItem: () => { throw new Error('x') } }
  assert.equal(loadScale(broken), 1)
  assert.equal(saveScale(broken, 1.3), false)
  assert.equal(loadScale(null), 1)
})

test('on 1920x1080 every step applies unchanged; tiny viewports are reduced', () => {
  for (const s of SCALE_STEPS) assert.equal(effectiveScale(s, 1920, 1080), s)
  assert.ok(effectiveScale(1.75, 1024, 768) < 1.75)
  assert.ok(effectiveScale(1.75, 1024, 768) >= 0.9)
  assert.equal(effectiveScale(0.9, 300, 300), 0.9)
})

test('1920x1080 layout keeps every overlay on-screen at all steps', () => {
  const W = 1920, H = 1080
  for (const s of SCALE_STEPS) {
    const L = overlayLayout(s, W, H)
    // Side panels (real px) never overlap each other.
    assert.ok((16 + 340 + 16 + 280 + 16) * s < W, `panels overlap at ${s}`)
    // Drawer: real top edge stays below the header, real width positive.
    const drawerRealH = L.drawerMaxHeight * s
    const drawerRealTop = H - 16 * s - drawerRealH
    assert.ok(drawerRealTop >= 48 * s, `drawer covers header at ${s}`)
    const drawerRealW = W - (L.drawer.left + L.drawer.right) * s
    assert.ok(drawerRealW > 400, `drawer too narrow at ${s}`)
    // Analytics panel fits vertically below the header.
    assert.ok(64 * s + L.analyticsMaxHeight * s <= H, `analytics overflows at ${s}`)
  }
  // At 175 % the drawer goes full-width (wide enough for its 3 columns) and
  // the header compacts; narrower screens wrap the drawer grid instead.
  const big = overlayLayout(1.75, W, H)
  assert.equal(big.drawerFull, true)
  assert.equal(big.compactGrid, false)
  assert.equal(overlayLayout(1.5, 960, 700).compactGrid, true)
  assert.ok(big.headerCompact >= 2)
  // At 100 % the original tactical layout is unchanged.
  const normal = overlayLayout(1, W, H)
  assert.deepEqual(normal.drawer, { left: 372, right: 312 })
  assert.equal(normal.compactGrid, false)
  assert.equal(normal.headerCompact, 0)
  assert.equal(normal.drawerMaxHeight, Math.round(H * 0.55))
})

test('text-size labels exist in all 11 languages', () => {
  for (const c of LANGUAGE_CODES) {
    const d = JSON.parse(readFileSync(join(here, 'i18n', `${c}.json`), 'utf8'))
    for (const k of ['scale.label', 'scale.decrease', 'scale.increase', 'scale.reset']) assert.ok(d[k], `${c}:${k}`)
    assert.match(d['scale.reset'], /100%/)
  }
})

test('change-summary card stays on-screen and clear of the drawer at every step', () => {
  for (const [W, H] of [[1920, 1080], [1366, 768], [1024, 768]]) {
    for (const sel of SCALE_STEPS) {
      const s = effectiveScale(sel, W, H)
      const L = overlayLayout(s, W, H)
      const c = L.deltaCard
      // Real width (px) positive and inside the viewport.
      const realW = Math.min(W - (c.left + c.right) * s, c.maxWidth * s)
      assert.ok(realW > 300, `delta card too narrow at ${sel} on ${W}x${H}`)
      assert.ok((c.left + c.right) * s < W)
      // Below the header, fits vertically on its own.
      assert.ok(c.top * s >= 48 * s)
      assert.ok((c.top + L.deltaMaxHeight) * s <= H, `delta card overflows at ${sel} on ${W}x${H}`)
      // With the drawer open, card bottom stays above the drawer's top edge.
      // (On short screens at large text the card is hidden while the drawer is open.)
      const drawerTop = H - 16 * s - L.drawerMaxHeight * s
      if (L.deltaFitsWithDrawer) {
        assert.ok(L.deltaMaxHeightWithDrawer >= 130)
        assert.ok((c.top + L.deltaMaxHeightWithDrawer) * s <= drawerTop, `delta card overlaps drawer at ${sel} on ${W}x${H}`)
      }
    }
  }
  // 100 %: between the side panels; 175 %: full width above them.
  const normal = overlayLayout(1, 1920, 1080)
  assert.equal(normal.deltaFull, false)
  assert.equal(normal.deltaCard.left, 372)
  assert.equal(normal.deltaCard.right, 312)
  const big = overlayLayout(1.75, 1920, 1080)
  assert.equal(big.deltaFull, true)
  assert.equal(big.deltaCard.left, 16)
  // 1920x1080 at 175 %: card and drawer both fit.
  assert.equal(big.deltaFitsWithDrawer, true)
  for (const st of SCALE_STEPS) assert.equal(overlayLayout(st, 1920, 1080).deltaFitsWithDrawer, true)
})

test('evolution table switches to stacked cards on narrow drawers (compactGrid)', () => {
  // EventDetail passes layout.compactGrid to EventEvolution: wide drawer -> table.
  assert.equal(overlayLayout(1.75, 1920, 1080).compactGrid, false)
  assert.equal(overlayLayout(1, 1920, 1080).compactGrid, false)
  assert.equal(overlayLayout(1.5, 960, 700).compactGrid, true)
})

test('change summary is never hidden with the drawer open: card or one-line pill above the drawer', () => {
  for (const [W, H] of [[1920, 1080], [1366, 768], [1280, 720], [1024, 768]]) {
    for (const sel of SCALE_STEPS) {
      const s = effectiveScale(sel, W, H)
      const L = overlayLayout(s, W, H)
      assert.ok(['card', 'pill'].includes(L.deltaModeWithDrawer))
      assert.equal(L.deltaModeWithDrawer === 'card', L.deltaFitsWithDrawer)
      const drawerTop = H - 16 * s - L.drawerMaxHeight * s
      if (L.deltaModeWithDrawer === 'pill') {
        // Pill sits under the header and above the drawer's top edge.
        assert.ok((L.deltaCard.top + L.deltaPillHeight) * s <= drawerTop, `pill overlaps drawer at ${sel} on ${W}x${H}`)
        // Expanded pill: usable height, still inside the viewport.
        assert.ok(L.deltaExpandedMaxHeight >= 120)
        assert.ok((L.deltaCard.top + L.deltaExpandedMaxHeight) * s <= H, `expanded card overflows at ${sel} on ${W}x${H}`)
      }
    }
  }
  // The known short-screen case: 1366x768 at 175 % -> pill (was hidden).
  assert.equal(overlayLayout(effectiveScale(1.75, 1366, 768), 1366, 768).deltaModeWithDrawer, 'pill')
  assert.equal(overlayLayout(1.75, 1920, 1080).deltaModeWithDrawer, 'card')
})

test('adaptive header compaction steps up only while the content overflows', () => {
  assert.equal(nextHeaderCompact(0, 900, 900), 0)
  assert.equal(nextHeaderCompact(0, 985, 781), 1)
  assert.equal(nextHeaderCompact(3, 985, 781), 4)
  assert.equal(nextHeaderCompact(HEADER_MAX_COMPACT, 2000, 781), HEADER_MAX_COMPACT)
  assert.equal(nextHeaderCompact(2, 781.5, 781), 2) // sub-pixel rounding is not overflow
  assert.equal(nextHeaderCompact(undefined, 0, 0), 0)
  // Header CSS never lets the right-hand controls shrink.
  const css = readFileSync(join(here, 'theme.css'), 'utf8')
  assert.match(css, /\.hdr-right \{ flex: 0 0 auto;/)
  for (let l = 1; l <= HEADER_MAX_COMPACT; l++) assert.ok(css.includes(`data-compact="${l}"`), `level ${l} styled`)
})

test('short event chip keeps the count and the English tier code', () => {
  assert.equal(shortEventChip(1, 'Critical'), '● 1 · CRITICAL')
  assert.equal(shortEventChip(3, 'HIGH'), '● 3 · HIGH')
  assert.equal(shortEventChip(0), '● 0')
  assert.equal(shortEventChip(2, undefined), '● 2 · N/A')
})
