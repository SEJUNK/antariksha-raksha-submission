// Regression: the numbered event-detail sections must read 1 -> 7. The drawer
// uses no CSS `order` / grid placement, so DOM (JSX) order is the visual
// reading order; section 7 (Event Evolution) previously rendered before
// section 3 and section 4 sat in the top grid ahead of section 3.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const src = readFileSync(join(here, 'components', 'EventDetail.jsx'), 'utf8')
// The drawer body: from the evidence trace to the end of the component.
const body = src.slice(src.indexOf('<EvidenceChain event={event}'), src.indexOf('const drawerStyle'))

const SECTION_MARKERS = [
  [1, "<SectionHeading n={1} title={t('detail.s1')} />"],
  [2, '<RiskAssessment event={event}'], // renders SectionHeading n={2}
  [3, '<DataProvenance event={event}'], // renders Collapsible n={3}
  [4, '<AiAnalysis key={event.id}'], // renders SectionHeading n={4}
  [5, "<SectionHeading n={5} title={t('detail.s5')} />"],
  [6, 'n={6} id={CHAIN_SECTIONS.basis}'],
  [7, 'n={7}\n        id={CHAIN_SECTIONS.evolution}'],
]

test('event detail: numbered sections are rendered in order 1..7', () => {
  const positions = SECTION_MARKERS.map(([n, marker]) => {
    const normalized = body.replace(/\r\n/g, '\n')
    const i = normalized.indexOf(marker)
    assert.notEqual(i, -1, `section ${n} marker not found in the drawer body`)
    assert.equal(normalized.indexOf(marker, i + 1), -1, `section ${n} rendered more than once`)
    return [n, i]
  })
  for (let k = 1; k < positions.length; k++) {
    assert.ok(positions[k][1] > positions[k - 1][1], `section ${positions[k][0]} must come after section ${positions[k - 1][0]}`)
  }
})

test('event detail: top grid holds only sections 1 and 2; no CSS reordering', () => {
  const normalized = body.replace(/\r\n/g, '\n')
  const gridStart = normalized.indexOf("gridTemplateColumns: gridCols('1fr 1fr')")
  const provenance = normalized.indexOf('<DataProvenance event={event}')
  assert.ok(gridStart !== -1 && gridStart < provenance)
  assert.equal(normalized.slice(gridStart, provenance).includes('<AiAnalysis'), false)
  assert.doesNotMatch(normalized, /\border:\s*\d|gridRow|gridArea|gridColumn:|column-reverse|row-reverse/)
})
