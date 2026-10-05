// Deployment layer tests: Vercel API proxy, Vercel Cron relay, vercel.json and
// the .env.example templates. Pure node:test; global fetch is mocked.
import { test, afterEach } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

import proxy, { handleProxy, resolveBackendPath, PATH_PARAM } from '../api/proxy.js'
import cron, { handleCron, bearerMatches } from '../api/cron/refresh.js'

const here = path.dirname(fileURLToPath(import.meta.url))
const frontendDir = path.resolve(here, '..')
const repoRoot = path.resolve(frontendDir, '..')

// URLs are assembled from parts on purpose: src/i18n/i18n.test.js guards that
// frontend/src contains no literal external http(s) endpoints.
const PROTO = 'https:'
const APP = `${PROTO}//app.vercel.app`
const BACKEND = `${PROTO}//backend.example.com`
const X = `${PROTO}//x`

const realFetch = globalThis.fetch
const savedEnv = { ...process.env }
afterEach(() => {
  globalThis.fetch = realFetch
  for (const k of ['BACKEND_URL', 'CRON_SECRET', 'SCHEDULER_SECRET']) {
    if (k in savedEnv) process.env[k] = savedEnv[k]
    else delete process.env[k]
  }
})

/** Install a fetch mock that records calls and returns `respond(url, init)`. */
function mockFetch(respond) {
  const calls = []
  globalThis.fetch = async (url, init = {}) => {
    calls.push({ url: String(url), init })
    return respond(String(url), init)
  }
  return calls
}

// ---------------------------------------------------------------- proxy ----

test('proxy forwards method, path, query, body and allowlisted headers', async () => {
  process.env.BACKEND_URL = `${BACKEND}/`
  const calls = mockFetch(() => new Response(JSON.stringify({ ok: true }), {
    status: 201, headers: { 'content-type': 'application/json' },
  }))
  const req = new Request(`${APP}/api/assets/42?limit=5&__ar_path=injected`, {
    method: 'POST',
    headers: {
      cookie: 'antariksha_session=abc',
      'content-type': 'application/json',
      'x-antariksha-client': 'console',
      'x-api-key': 'k',
      origin: `${APP}`,
      'user-agent': 'test-agent',
      authorization: 'Bearer browser-token',
      'x-evil': '1',
    },
    body: JSON.stringify({ name: 'x' }),
  })
  const res = await proxy.fetch(req)
  assert.equal(res.status, 201)
  assert.deepEqual(await res.json(), { ok: true })
  assert.equal(calls.length, 1)
  const { url, init } = calls[0]
  assert.equal(url, `${BACKEND}/api/assets/42?limit=5`)
  assert.equal(init.method, 'POST')
  assert.equal(new TextDecoder().decode(init.body), '{"name":"x"}')
  const h = init.headers
  assert.equal(h.get('cookie'), 'antariksha_session=abc')
  assert.equal(h.get('content-type'), 'application/json')
  assert.equal(h.get('x-antariksha-client'), 'console')
  assert.equal(h.get('x-api-key'), 'k')
  assert.equal(h.get('origin'), `${APP}`)
  assert.equal(h.get('user-agent'), 'test-agent')
  assert.equal(h.get('authorization'), null, 'browser Authorization must never be forwarded')
  assert.equal(h.get('x-evil'), null)
  assert.equal(init.redirect, 'manual')
})

test('proxy returns every Set-Cookie header individually', async () => {
  process.env.BACKEND_URL = `${BACKEND}`
  mockFetch(() => {
    const headers = new Headers({ 'content-type': 'application/json' })
    headers.append('set-cookie', 'antariksha_session=new; HttpOnly; SameSite=Lax; Path=/')
    headers.append('set-cookie', 'other=1; Path=/')
    return new Response('{}', { status: 200, headers })
  })
  const res = await handleProxy(new Request(`${APP}/api/auth/login`, {
    method: 'POST', body: '{}', headers: { 'content-type': 'application/json' },
  }))
  assert.equal(res.status, 200)
  assert.deepEqual(res.headers.getSetCookie(), [
    'antariksha_session=new; HttpOnly; SameSite=Lax; Path=/',
    'other=1; Path=/',
  ])
})

test('proxy GET sends no body and relays backend error status', async () => {
  process.env.BACKEND_URL = `${BACKEND}`
  const calls = mockFetch(() => new Response('{"error":"unauthenticated"}', { status: 401 }))
  const res = await handleProxy(new Request(`${APP}/api/events`))
  assert.equal(res.status, 401)
  assert.equal(calls[0].init.method, 'GET')
  assert.equal(calls[0].init.body, undefined)
})

test('proxy blocks /api/internal/* (including encoded variants) with 404', async () => {
  process.env.BACKEND_URL = `${BACKEND}`
  const calls = mockFetch(() => new Response('{}'))
  for (const p of [
    '/api/internal/scheduled-refresh',
    '/api/INTERNAL/scheduled-refresh',
    '/api/%69nternal/scheduled-refresh',
    '/api//internal/scheduled-refresh',
    '/api/./internal',
    '/api/internal',
  ]) {
    const res = await handleProxy(new Request(`${APP}${p}`, {
      headers: { authorization: 'Bearer guess' },
    }))
    assert.equal(res.status, 404, p)
  }
  assert.equal(calls.length, 0, 'blocked paths must never reach the backend')
})

test('proxy resolves the vercel.json rewrite form /api/proxy?__ar_path=...', () => {
  assert.equal(PATH_PARAM, '__ar_path')
  assert.equal(resolveBackendPath(new URL(`${X}/api/proxy?__ar_path=health`)), '/api/health')
  assert.equal(resolveBackendPath(new URL(`${X}/api/proxy?__ar_path=events/7/decisions`)), '/api/events/7/decisions')
  assert.equal(resolveBackendPath(new URL(`${X}/api/proxy?__ar_path=internal/x`)), '/api/internal/x')
  assert.equal(resolveBackendPath(new URL(`${X}/api/proxy`)), null)
  // WHATWG URL already collapses dot segments, so the result is still blocked.
  assert.equal(resolveBackendPath(new URL(`${X}/api/a/%2E%2E/internal`)), '/api/internal')
  assert.equal(resolveBackendPath(new URL(`${X}/api/proxy?__ar_path=a/../internal`)), null)
  assert.equal(resolveBackendPath(new URL(`${X}/other`)), null)
})

test('proxy returns 503 backend_not_configured without BACKEND_URL', async () => {
  delete process.env.BACKEND_URL
  const calls = mockFetch(() => new Response('{}'))
  const res = await handleProxy(new Request(`${APP}/api/health`))
  assert.equal(res.status, 503)
  assert.deepEqual(await res.json(), { error: 'backend_not_configured' })
  assert.equal(calls.length, 0)
})

test('proxy returns 504 on upstream timeout and 502 when unreachable', async () => {
  process.env.BACKEND_URL = `${BACKEND}`
  globalThis.fetch = (url, init) => new Promise((_, reject) => {
    init.signal.addEventListener('abort', () => {
      const e = new Error('aborted'); e.name = 'AbortError'; reject(e)
    })
  })
  const slow = await handleProxy(new Request(`${APP}/api/health`), { timeoutMs: 10 })
  assert.equal(slow.status, 504)
  assert.deepEqual(await slow.json(), { error: 'backend_timeout' })

  globalThis.fetch = async () => { throw new TypeError('fetch failed') }
  const down = await handleProxy(new Request(`${APP}/api/health`))
  assert.equal(down.status, 502)
  assert.deepEqual(await down.json(), { error: 'backend_unreachable' })
})

// ----------------------------------------------------------------- cron ----

function cronEnv() {
  process.env.BACKEND_URL = `${BACKEND}`
  process.env.CRON_SECRET = 'cron-test-secret-value'
  process.env.SCHEDULER_SECRET = 'scheduler-test-secret-value'
}
const CRON_URL = `${APP}/api/cron/refresh`
const CRON_AUTH = { headers: { authorization: 'Bearer cron-test-secret-value' } }

test('cron bearerMatches is exact (constant-time compare of hashes)', () => {
  assert.equal(bearerMatches('Bearer s3cret', 's3cret'), true)
  assert.equal(bearerMatches('Bearer s3cre', 's3cret'), false)
  assert.equal(bearerMatches('s3cret', 's3cret'), false)
  assert.equal(bearerMatches(null, 's3cret'), false)
  assert.equal(bearerMatches('Bearer ', ''), false)
})

test('cron rejects missing or wrong CRON_SECRET with 401 and never calls backend', async () => {
  cronEnv()
  const calls = mockFetch(() => new Response('{}'))
  const missing = await cron.fetch(new Request(CRON_URL))
  assert.equal(missing.status, 401)
  const wrong = await cron.fetch(new Request(CRON_URL, { headers: { authorization: 'Bearer nope' } }))
  assert.equal(wrong.status, 401)
  assert.equal(calls.length, 0)
})

test('cron forwards with SCHEDULER_SECRET and relays 202 accepted', async () => {
  cronEnv()
  const calls = mockFetch(() => new Response(JSON.stringify({ status: 'accepted', attempt_id: 7 }), {
    status: 202, headers: { 'content-type': 'application/json' },
  }))
  const res = await cron.fetch(new Request(CRON_URL, CRON_AUTH))
  assert.equal(res.status, 202)
  assert.deepEqual(await res.json(), { status: 'accepted', attempt_id: 7 })
  assert.equal(calls.length, 1)
  assert.equal(calls[0].url, `${BACKEND}/api/internal/scheduled-refresh`)
  assert.equal(calls[0].init.headers.authorization, 'Bearer scheduler-test-secret-value')
})

test('cron relays 200 skipped and maps backend 401 to 502 without leaking secrets', async () => {
  cronEnv()
  mockFetch(() => new Response('{"status":"skipped","reason":"too_soon"}', { status: 200 }))
  const skipped = await handleCron(new Request(CRON_URL, CRON_AUTH))
  assert.equal(skipped.status, 200)
  assert.equal((await skipped.json()).status, 'skipped')

  mockFetch(() => new Response('{"error":"unauthorized"}', { status: 401 }))
  const bad = await handleCron(new Request(CRON_URL, CRON_AUTH))
  assert.equal(bad.status, 502)
  const body = await bad.json()
  assert.equal(body.error, 'backend_rejected_scheduler_secret')
  assert.ok(!JSON.stringify(body).includes('secret-value'))
})

test('cron fails closed when secrets or BACKEND_URL are unset', async () => {
  const calls = mockFetch(() => new Response('{}'))

  cronEnv(); delete process.env.CRON_SECRET
  let res = await handleCron(new Request(CRON_URL, { headers: { authorization: 'Bearer ' } }))
  assert.equal(res.status, 500)
  assert.equal((await res.json()).error, 'cron_secret_not_configured')

  cronEnv(); delete process.env.SCHEDULER_SECRET
  res = await handleCron(new Request(CRON_URL, CRON_AUTH))
  assert.equal(res.status, 500)
  assert.equal((await res.json()).error, 'scheduler_secret_not_configured')

  cronEnv(); delete process.env.BACKEND_URL
  res = await handleCron(new Request(CRON_URL, CRON_AUTH))
  assert.equal(res.status, 503)
  assert.equal((await res.json()).error, 'backend_not_configured')
  assert.equal(calls.length, 0)
})

// ---------------------------------------------------------- vercel.json ----

test('vercel.json: vite build, no cron (Hobby-safe), proxy + SPA rewrites, function duration, no secrets', () => {
  const raw = readFileSync(path.join(frontendDir, 'vercel.json'), 'utf8')
  const cfg = JSON.parse(raw)
  const allowed = new Set(['$schema', 'framework', 'buildCommand', 'outputDirectory', 'installCommand',
    'functions', 'rewrites', 'headers', 'crons', 'regions'])
  for (const k of Object.keys(cfg)) assert.ok(allowed.has(k), `unexpected vercel.json key ${k}`)
  assert.equal(cfg.framework, 'vite')
  assert.equal(cfg.buildCommand, 'npm run build')
  assert.equal(cfg.outputDirectory, 'dist')
  // No Vercel Cron: Hobby plans reject sub-daily schedules at deploy time; the
  // backend's internal scheduler provides the 2-hour cadence.
  assert.equal(cfg.crons, undefined)
  // /api/* goes to the named proxy function (Vercel has no [...catchAll] files
  // outside Next.js); the proxy itself and /api/cron/* are excluded.
  const px = cfg.rewrites.find((r) => r.destination.startsWith('/api/proxy'))
  assert.equal(px.destination, '/api/proxy?__ar_path=:path')
  const pxRe = new RegExp('^' + px.source.replace(':path', '') + '$')
  assert.ok(pxRe.test('/api/health') && pxRe.test('/api/events/7/decisions') && pxRe.test('/api/auth/login'))
  assert.ok(!pxRe.test('/api/proxy') && !pxRe.test('/api/cron/refresh'))
  assert.ok(cfg.functions['api/**/*.js'].maxDuration >= 30)
  const spa = cfg.rewrites.find((r) => r.destination === '/index.html')
  assert.ok(spa, 'SPA rewrite present')
  const re = new RegExp(`^${spa.source}$`)
  assert.ok(re.test('/dashboard/events'))
  assert.ok(re.test('/'))
  assert.ok(!re.test('/api/health'), 'SPA rewrite must not swallow /api/*')
  assert.ok(!/secret|token|password|bearer/i.test(raw), 'vercel.json must not contain secrets')
})

// --------------------------------------------------------- .env.example ----

test('.env.example templates contain placeholders only', () => {
  for (const file of [path.join(repoRoot, '.env.example'), path.join(frontendDir, '.env.example')]) {
    const text = readFileSync(file, 'utf8')
    for (const line of text.split(/\r?\n/)) {
      const m = /^\s*#?\s*([A-Z0-9_]+)=(\S*)/.exec(line)
      if (!m) continue
      const [, key, value] = m
      if (/SECRET|TOKEN|PASSWORD|API_KEY/.test(key)) {
        assert.ok(value === '' || /replace|example|placeholder/i.test(value),
          `${path.basename(file)}: ${key} must be empty or an obvious placeholder`)
      }
    }
    assert.ok(!/\b\d{8,10}:[A-Za-z0-9_-]{30,}\b/.test(text), 'Telegram-style bot token')
    assert.ok(!/sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{20,}/.test(text), 'API-key-like string')
  }
  const root = readFileSync(path.join(repoRoot, '.env.example'), 'utf8')
  for (const k of ['ANTARIKSHA_DB_PATH', 'ANTARIKSHA_SCHEDULER_MODE', 'SCHEDULER_SECRET',
    'ANTARIKSHA_CORS_ORIGINS', 'ANTARIKSHA_COOKIE_SECURE', 'ANTARIKSHA_AI_MODE']) {
    assert.ok(root.includes(`${k}=`), `root .env.example documents ${k}`)
  }
})
