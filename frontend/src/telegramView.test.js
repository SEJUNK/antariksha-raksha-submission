import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { telegramTestMessage, telegramViewModel } from './telegramView.js'

const here = dirname(fileURLToPath(import.meta.url))

test('telegram view model: disabled / unconfigured / active; ignores secret-looking fields', () => {
  assert.equal(telegramViewModel(null), null)
  assert.deepEqual(telegramViewModel({ enabled: false, configured: true }), { state: 'disabled', minRisk: 'High', notifyDemo: false })
  assert.equal(telegramViewModel({ enabled: true, configured: false }).state, 'unconfigured')
  const vm = telegramViewModel({ enabled: true, configured: true, min_risk: 'Critical', notify_demo: true, bot_token: '********' })
  assert.deepEqual(vm, { state: 'active', minRisk: 'Critical', notifyDemo: true })
  assert.equal(telegramViewModel({ enabled: true, configured: true, min_risk: 'Low' }).minRisk, 'High')
})

test('telegram test-send outcome messages', () => {
  assert.deepEqual(telegramTestMessage({ sent: true }), { tone: 'ok', key: 'tg.testSent' })
  assert.deepEqual(telegramTestMessage({ sent: false, status: 'failed', error: 'HTTPError HTTP 401' }),
    { tone: 'error', key: 'tg.testFailed', vars: { error: 'HTTPError HTTP 401' } })
  assert.deepEqual(telegramTestMessage(null, { status: 429 }), { tone: 'error', key: 'tg.cooldown' })
  assert.equal(telegramTestMessage(null, { status: 403 }), null)
})

test('telegram: api calls the protected backend routes only (POST carries no client text)', () => {
  const src = readFileSync(join(here, 'api.js'), 'utf8')
  assert.match(src, /getTelegramStatus: \(\) => request\('\/api\/admin\/notifications\/telegram'\)/)
  assert.match(src, /sendTelegramTest: \(\) => request\('\/api\/admin\/notifications\/telegram\/test', \{ method: 'POST' \}\)/)
  assert.doesNotMatch(src, /api\.telegram\.org/)
})

test('telegram: no credentials or Telegram env vars in browser code or Vite env', () => {
  const files = []
  const walk = (dir) => {
    for (const name of readdirSync(dir)) {
      const p = join(dir, name)
      if (statSync(p).isDirectory()) walk(p)
      else if (/\.(js|jsx)$/.test(name) && !/\.test\.js$/.test(name)) files.push(p)
    }
  }
  walk(here)
  for (const f of files) {
    const src = readFileSync(f, 'utf8')
    assert.doesNotMatch(src, /TELEGRAM_BOT_TOKEN|TELEGRAM_CHAT_ID|VITE_TELEGRAM|api\.telegram\.org/, f)
  }
  const envExample = readFileSync(join(here, '..', '.env.example'), 'utf8')
  assert.doesNotMatch(envExample, /^\s*VITE_TELEGRAM/m)
})
