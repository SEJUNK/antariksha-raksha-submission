import { test } from 'node:test'
import assert from 'node:assert/strict'
import { parseReport, reportFilename, reportHtml } from './reportExport.js'

const SAMPLE = [
  '# ANTARIKSHA-RAKSHA — Incident Report',
  '',
  '**DEMO SCENARIO — controlled geometry. Not an operational collision warning.**',
  '## Event',
  '- Miss distance at TCA: 0.021 km',
  '- Collision probability: 7.70e-3 (1 in 130)',
  'Plain <brief> & text with Δv 0.05 m/s',
  '---',
].join('\n')

test('parseReport types headings, bullets, banner, rule and paragraphs', () => {
  const b = parseReport(SAMPLE)
  assert.deepEqual(b.map((x) => x.type), ['h1', 'p', 'h2', 'li', 'li', 'p', 'hr'])
  assert.equal(b[1].bold, true)
  assert.equal(b[1].text.startsWith('DEMO SCENARIO'), true)
})

test('numbers and units pass through the export unchanged', () => {
  const b = parseReport(SAMPLE)
  assert.equal(b[3].text, 'Miss distance at TCA: 0.021 km')
  assert.equal(b[4].text, 'Collision probability: 7.70e-3 (1 in 130)')
  const html = reportHtml(b, 'Report')
  for (const s of ['0.021 km', '7.70e-3', '1 in 130', 'Δv 0.05 m/s']) assert.ok(html.includes(s), s)
})

test('reportHtml escapes markup and keeps the demo banner', () => {
  const html = reportHtml(parseReport(SAMPLE), 'Report')
  assert.ok(html.includes('Plain &lt;brief&gt; &amp; text'))
  assert.ok(html.includes('class="banner"'))
  assert.ok(!html.includes('<brief>'))
})

test('reportFilename is filesystem-safe and uses the requested extension', () => {
  assert.equal(reportFilename('CARTOSAT-3', 'FENGYUN 1C DEB', '2026-10-03T07:00:00Z', 'docx'),
    'incident_CARTOSAT-3_FENGYUN-1C-DEB_2026-10-03.docx')
  assert.equal(reportFilename('A/B', 'C', null, 'pdf'), 'incident_A-B_C.pdf')
})
