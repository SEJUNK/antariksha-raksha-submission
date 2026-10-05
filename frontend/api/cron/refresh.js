// Vercel Cron target: GET /api/cron/refresh (schedule in vercel.json).
//
// Vercel invokes this path with `Authorization: Bearer <CRON_SECRET>` when
// CRON_SECRET is set in the project environment. This function verifies that
// header with a constant-time comparison, then asks the persistent backend to
// start a scheduled catalog refresh:
//
//   GET {BACKEND_URL}/api/internal/scheduled-refresh
//   Authorization: Bearer <SCHEDULER_SECRET>
//
// The backend answers quickly (202 accepted / 200 skipped) and runs the
// multi-second refresh in its own process, so this function never has to wait
// for CelesTrak download + SGP4 screening. Secrets are never logged or echoed.
//
// The vercel.json /api/* rewrite to api/proxy.js excludes /api/cron/*, so this
// function is reached directly. Inert unless a Vercel Cron job is configured.

import { createHash, timingSafeEqual } from 'node:crypto'

export const CRON_TIMEOUT_MS = 25_000

function json(status, body) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json', 'cache-control': 'no-store' },
  })
}

/**
 * Constant-time check of an Authorization header against `Bearer <secret>`.
 * Both sides are SHA-256 hashed first so the comparison length is fixed and
 * does not leak the secret's length.
 */
export function bearerMatches(authHeader, secret) {
  if (!secret || typeof authHeader !== 'string') return false
  const a = createHash('sha256').update(authHeader, 'utf8').digest()
  const b = createHash('sha256').update(`Bearer ${secret}`, 'utf8').digest()
  return timingSafeEqual(a, b)
}

export async function handleCron(request, { env = process.env, fetchImpl = globalThis.fetch, timeoutMs = CRON_TIMEOUT_MS } = {}) {
  const method = request.method.toUpperCase()
  if (method !== 'GET' && method !== 'POST') {
    return new Response(JSON.stringify({ error: 'method_not_allowed' }), {
      status: 405,
      headers: { 'content-type': 'application/json', allow: 'GET, POST' },
    })
  }

  const cronSecret = String(env.CRON_SECRET || '')
  if (!cronSecret) {
    // Fail closed: without CRON_SECRET anyone could trigger refreshes.
    return json(500, { error: 'cron_secret_not_configured' })
  }
  if (!bearerMatches(request.headers.get('authorization'), cronSecret)) {
    return json(401, { error: 'unauthorized' })
  }

  const base = String(env.BACKEND_URL || '').trim().replace(/\/+$/, '')
  if (!/^https?:\/\//i.test(base)) {
    return json(503, { error: 'backend_not_configured' })
  }
  const schedulerSecret = String(env.SCHEDULER_SECRET || '')
  if (!schedulerSecret) {
    return json(500, { error: 'scheduler_secret_not_configured' })
  }

  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  let upstream
  let text
  try {
    upstream = await fetchImpl(`${base}/api/internal/scheduled-refresh`, {
      method: 'GET',
      headers: {
        authorization: `Bearer ${schedulerSecret}`,
        accept: 'application/json',
        'user-agent': 'antariksha-vercel-cron',
      },
      redirect: 'manual',
      signal: controller.signal,
    })
    text = await upstream.text()
  } catch (err) {
    if (err?.name === 'AbortError') return json(504, { error: 'backend_timeout' })
    return json(502, { error: 'backend_unreachable' })
  } finally {
    clearTimeout(timer)
  }

  let result
  try {
    result = text ? JSON.parse(text) : {}
  } catch {
    return json(502, { error: 'backend_invalid_response', backend_status: upstream.status })
  }

  // 401/403 from the backend means SCHEDULER_SECRET does not match the
  // backend's value: a deployment misconfiguration, not a caller error.
  if (upstream.status === 401 || upstream.status === 403) {
    return json(502, { error: 'backend_rejected_scheduler_secret', backend_status: upstream.status })
  }
  // 202 accepted, 200 skipped, 503 backend has no secret configured, etc.
  console.log(`[cron/refresh] backend status ${upstream.status} ${result?.status ?? ''}`.trim())
  return json(upstream.status, result)
}

export default {
  fetch(request) {
    return handleCron(request)
  },
}
