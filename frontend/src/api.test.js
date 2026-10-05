import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  ApiError, CLIENT_HEADER, CLIENT_HEADER_VALUE, api, buildRequestOptions, errorDetail, isSessionExpiry, setUnauthorizedHandler,
} from './api.js'

test('api: every request includes credentials; CSRF header only on non-GET', () => {
  const get = buildRequestOptions({}, { apiKey: null })
  assert.equal(get.credentials, 'include')
  assert.equal(get.method, 'GET')
  assert.equal(get.headers[CLIENT_HEADER], undefined)

  const post = buildRequestOptions({ method: 'post', headers: { 'Content-Type': 'application/json' }, body: '{}' }, { apiKey: null })
  assert.equal(post.credentials, 'include')
  assert.equal(post.method, 'POST')
  assert.equal(post.headers[CLIENT_HEADER], CLIENT_HEADER_VALUE)
  assert.equal(CLIENT_HEADER, 'X-Antariksha-Client')
  assert.equal(CLIENT_HEADER_VALUE, 'console')
  assert.equal(post.headers['Content-Type'], 'application/json')
  assert.equal(post.body, '{}')
  assert.equal(post.headers['X-API-Key'], undefined)

  for (const m of ['PATCH', 'DELETE', 'PUT']) assert.equal(buildRequestOptions({ method: m }, { apiKey: null }).headers[CLIENT_HEADER], 'console')
})

test('api: optional deployment key is kept for non-GET only', () => {
  assert.equal(buildRequestOptions({ method: 'POST' }, { apiKey: 'k1' }).headers['X-API-Key'], 'k1')
  assert.equal(buildRequestOptions({}, { apiKey: 'k1' }).headers['X-API-Key'], undefined)
  // Caller headers are not mutated.
  const h = { A: '1' }
  buildRequestOptions({ method: 'POST', headers: h }, { apiKey: 'k' })
  assert.deepEqual(h, { A: '1' })
})

test('api: 401 means session expiry except for the login call itself', () => {
  assert.equal(isSessionExpiry('/api/events', 401), true)
  assert.equal(isSessionExpiry('/api/auth/me', 401), true)
  assert.equal(isSessionExpiry('/api/auth/login', 401), false)
  assert.equal(isSessionExpiry('/api/events', 403), false)
  assert.equal(errorDetail({ detail: 'Invalid username or password.' }, 'x'), 'Invalid username or password.')
  assert.equal(errorDetail({ detail: [{ msg: 'too short' }] }, 'x'), 'too short')
  assert.equal(errorDetail(null, 'fallback'), 'fallback')
})

function mockFetch(status, body) {
  const calls = []
  globalThis.fetch = async (url, opts) => {
    calls.push({ url, opts })
    return { ok: status < 400, status, json: async () => body }
  }
  return calls
}

test('api: a 401 from any call triggers the logged-out handler; failed login does not', async () => {
  const orig = globalThis.fetch
  let fired = 0
  setUnauthorizedHandler(() => { fired += 1 })
  try {
    mockFetch(401, { error: 'not_authenticated' })
    await assert.rejects(api.getEvents(), (e) => e instanceof ApiError && e.status === 401 && e.code === 'not_authenticated')
    assert.equal(fired, 1)

    mockFetch(401, { detail: 'Invalid username or password.', error: 'invalid_credentials' })
    await assert.rejects(api.login('u', 'p'), (e) => e.status === 401 && e.message === 'Invalid username or password.')
    assert.equal(fired, 1)

    // 403 shows the backend detail and does not log out.
    mockFetch(403, { error: 'forbidden', detail: 'Your role (VIEWER) cannot record decisions.' })
    await assert.rejects(api.approveEvent(1), (e) => e.status === 403 && e.code === 'forbidden' && /VIEWER/.test(e.message))
    assert.equal(fired, 1)

    // Real requests carry the cookie and (for POST) the CSRF header.
    const calls = mockFetch(200, { ok: true })
    await api.logout()
    await api.getStatus()
    assert.equal(calls[0].opts.credentials, 'include')
    assert.equal(calls[0].opts.headers[CLIENT_HEADER], 'console')
    assert.equal(calls[1].opts.credentials, 'include')
    assert.equal(calls[1].opts.headers[CLIENT_HEADER], undefined)

    // Login sends JSON credentials to /api/auth/login.
    const c2 = mockFetch(200, { user: { username: 'u' } })
    await api.login('u', 'secret-pass')
    assert.match(c2[0].url, /\/api\/auth\/login$/)
    assert.deepEqual(JSON.parse(c2[0].opts.body), { username: 'u', password: 'secret-pass' })
  } finally {
    setUnauthorizedHandler(null)
    globalThis.fetch = orig
  }
})

test('api: protected-asset and admin endpoints use the documented paths and methods', async () => {
  const orig = globalThis.fetch
  try {
    const calls = mockFetch(200, { ok: true, effective: 'next_successful_refresh' })
    await api.createProtectedAsset({ name_query: 'CARTOSAT', criticality: 'Tier1' })
    await api.updateProtectedAsset(7, { note: 'n' })
    await api.setProtectedAssetStatus(7, 'suspended')
    await api.getProtectedAssetAudit()
    await api.getUsers()
    await api.createUser({ username: 'a', role: 'VIEWER', password: 'x'.repeat(10) })
    await api.updateUser(5, { active: false })
    await api.getAdminAudit()
    const seen = calls.map((c) => `${c.opts.method} ${c.url.replace(/^https?:\/\/[^/]+/, '')}`)
    assert.deepEqual(seen, [
      'POST /api/protected-assets',
      'PATCH /api/protected-assets/7',
      'POST /api/protected-assets/7/status',
      'GET /api/protected-assets/audit',
      'GET /api/admin/users',
      'POST /api/admin/users',
      'PATCH /api/admin/users/5',
      'GET /api/admin/audit',
    ])
    assert.deepEqual(JSON.parse(calls[2].opts.body), { status: 'suspended' })
    assert.ok(calls.filter((c) => c.opts.method !== 'GET').every((c) => c.opts.headers[CLIENT_HEADER] === 'console'))
  } finally {
    globalThis.fetch = orig
  }
})
