// Incident report export: Word (.docx) and print-ready PDF.
//
// The report text (English source-of-record) is built by EventDetail's
// buildIncidentReport as simple markdown-style lines. parseReport turns those
// lines into typed blocks (pure, unit-tested); the exporters render the
// blocks. Everything runs in the browser -- no network request, no external
// service.

export function parseReport(text) {
  const blocks = []
  for (const raw of String(text ?? '').split('\n')) {
    const line = raw.trimEnd()
    if (!line.trim()) continue
    if (line.startsWith('## ')) blocks.push({ type: 'h2', text: line.slice(3) })
    else if (line.startsWith('# ')) blocks.push({ type: 'h1', text: line.slice(2) })
    else if (line.startsWith('- ')) blocks.push({ type: 'li', text: line.slice(2) })
    else if (line.trim() === '---') blocks.push({ type: 'hr' })
    else if (/^\*\*.+\*\*$/.test(line.trim())) blocks.push({ type: 'p', text: line.trim().slice(2, -2), bold: true })
    else blocks.push({ type: 'p', text: line })
  }
  return blocks
}

export function reportFilename(nameA, nameB, tcaTimestamp, ext) {
  const safe = (s) => String(s ?? '').replace(/[^A-Za-z0-9._-]+/g, '-').replace(/^-+|-+$/g, '')
  const stamp = String(tcaTimestamp || '').slice(0, 10)
  return `incident_${safe(nameA)}_${safe(nameB)}${stamp ? `_${stamp}` : ''}.${ext}`
}

const escapeHtml = (s) => String(s)
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')

export function reportHtml(blocks, title) {
  const body = []
  let inList = false
  for (const b of blocks) {
    if (b.type === 'li' && !inList) { body.push('<ul>'); inList = true }
    if (b.type !== 'li' && inList) { body.push('</ul>'); inList = false }
    if (b.type === 'h1') body.push(`<h1>${escapeHtml(b.text)}</h1>`)
    else if (b.type === 'h2') body.push(`<h2>${escapeHtml(b.text)}</h2>`)
    else if (b.type === 'li') body.push(`<li>${escapeHtml(b.text)}</li>`)
    else if (b.type === 'hr') body.push('<hr>')
    else body.push(`<p${b.bold ? ' class="banner"' : ''}>${escapeHtml(b.text)}</p>`)
  }
  if (inList) body.push('</ul>')
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>${escapeHtml(title)}</title>
<style>
  @page { size: A4; margin: 18mm; }
  body { font-family: "Segoe UI", Arial, "Nirmala UI", sans-serif; color: #111; font-size: 11pt; line-height: 1.45; }
  h1 { font-size: 18pt; margin: 0 0 6pt; }
  h2 { font-size: 13pt; margin: 14pt 0 4pt; border-bottom: 1px solid #999; padding-bottom: 2pt; }
  ul { margin: 0 0 0 16pt; padding: 0; }
  p { margin: 0 0 6pt; white-space: pre-wrap; }
  .banner { font-weight: 700; border: 1px solid #b45309; padding: 6pt; }
  hr { border: 0; border-top: 1px solid #999; margin: 12pt 0 6pt; }
</style></head><body>${body.join('\n')}</body></html>`
}

// PDF via the browser's own print pipeline ("Save as PDF"): full Unicode
// (Δv, σ, ⇔) without bundling fonts. Uses a hidden same-origin iframe.
export function printReportPdf(blocks, title) {
  const iframe = document.createElement('iframe')
  iframe.style.cssText = 'position:fixed;right:0;bottom:0;width:0;height:0;border:0'
  document.body.appendChild(iframe)
  const doc = iframe.contentDocument
  doc.open()
  doc.write(reportHtml(blocks, title))
  doc.close()
  const win = iframe.contentWindow
  const cleanup = () => setTimeout(() => iframe.remove(), 1000)
  win.addEventListener('afterprint', cleanup)
  setTimeout(() => { win.focus(); win.print(); cleanup() }, 50)
}

export async function downloadReportDocx(blocks, filename) {
  const { Document, Packer, Paragraph, TextRun, HeadingLevel, BorderStyle } = await import('docx')
  const children = blocks.map((b) => {
    if (b.type === 'h1') return new Paragraph({ text: b.text, heading: HeadingLevel.HEADING_1 })
    if (b.type === 'h2') return new Paragraph({ text: b.text, heading: HeadingLevel.HEADING_2 })
    if (b.type === 'li') return new Paragraph({ text: b.text, bullet: { level: 0 } })
    if (b.type === 'hr') {
      return new Paragraph({ border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: '999999', space: 1 } } })
    }
    return new Paragraph({ children: [new TextRun({ text: b.text, bold: Boolean(b.bold) })], spacing: { after: 120 } })
  })
  const doc = new Document({
    creator: 'ANTARIKSHA-RAKSHA',
    title: 'ANTARIKSHA-RAKSHA Incident Report',
    styles: { default: { document: { run: { font: 'Segoe UI', size: 22 } } } },
    sections: [{ children }],
  })
  const blob = await Packer.toBlob(doc)
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
