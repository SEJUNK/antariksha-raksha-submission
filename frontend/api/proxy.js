// Same-origin API proxy (Vercel Function, Node.js runtime, Web Handler).
//
// Browser -> https://<vercel-domain>/api/* -> this function -> BACKEND_URL/api/*
//
// Why: the backend issues an HttpOnly, SameSite=Lax session cookie. Served
// through this proxy, that cookie belongs to the Vercel domain (first-party),
// so the console works without third-party cookies or cross-site CORS.
//
// Security rules:
//  * /api/internal/* is never reachable through the proxy (404). The scheduled
//    refresh endpoint is only called server-to-server by api/cron/refresh.js.
//  * The browser's Authorization header is never forwarded; only an explicit
//    allowlist of request headers is.
//  * No secrets are read or logged here. BACKEND_URL is not a secret but is
//    never echoed back to the client.
//
// No npm dependencies: plain ESM + global fetch (Node 18+).

/** Upstream timeout. Keep below the function maxDuration (60 s). */
export const PROXY_TIMEOUT_MS = 55_000

/** Request headers copied from the browser to the backend (lower-case). */
export const FORWARDED_REQUEST_HEADERS = [
  'accept',
  'accept-language',
  'content-type',
  'cookie',
  'origin',
  'user-agent',
  'x-antariksha-client',
  'x-api-key',
]

// Response headers that must not be copied back (hop-by-hop, or invalid after
// fetch() has already decoded the body). set-cookie is handled separately.
const DROPPED_RESPONSE_HEADERS = new Set([
  'connection',
  'keep-alive',
  'transfer-encoding',
  'content-encoding',
  'content-length',
  'upgrade',
  'proxy-authenticate',
  'trailer',
  'te',
  'set-cookie',
])

function json(status, body) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json', 'cache-control': 'no-store' },
  })
}

/** Normalised BACKEND_URL (no trailing slash) or null if unset/invalid. */
export function backendBase(env) {
  const raw = String(env?.BACKEND_URL || '').trim()
  if (!raw) return null
  try {
    const u = new URL(raw)
    if (u.protocol !== 'http:' && u.protocol !== 'https:') return null
    return raw.replace(/\/+$/, '')
  } catch {
    return null
  }
}

/**
 * Resolve the backend path ("/api/...") for an incoming request URL, or null
 * if the path is not a valid /api/* path. Segments are decoded and normalised
 * so that encodings such as /api/%69nternal or /api//internal cannot bypass
 * the internal-route block. ".." segments are rejected.
 *
 * Vercel Functions outside Next.js do not support [...catchAll] filenames, so
 * vercel.json rewrites /api/<anything> to this function as
 * /api/proxy?__ar_path=<anything> (the original query string is kept). A
 * request that arrives with its original pathname (e.g. local tests) is
 * handled directly. The injected parameter is never forwarded upstream.
 */
export const PATH_PARAM = '__ar_path'

export function resolveBackendPath(url) {
  let pathname = url.pathname
  if (/\/api\/proxy\/?$/i.test(pathname)) {
    const p = url.searchParams.get(PATH_PARAM)
    if (!p) return null
    pathname = '/api/' + p.replace(/^\/+/, '')
  }
  const rawSegments = pathname.split('/')
  const segments = []
  for (const raw of rawSegments) {
    if (raw === '' || raw === '.') continue
    let seg
    try {
      seg = decodeURIComponent(raw)
    } catch {
      return null
    }
    if (seg === '..' || seg.includes('/') || seg.includes('\\')) return null
    segments.push(seg)
  }
  if (segments.length < 2 || segments[0] !== 'api') return null
  return '/' + segments.map((s) => encodeURIComponent(s)).join('/')
}

/** True for /api/internal and anything below it (case-insensitive). */
export function isInternalPath(backendPath) {
  const second = backendPath.split('/')[2] || ''
  return second.toLowerCase() === 'internal'
}

/** Build the upstream query string, dropping the rewrite's injected path parameter. */
function upstreamSearch(url) {
  const params = new URLSearchParams(url.search)
  params.delete(PATH_PARAM)
  const s = params.toString()
  return s ? `?${s}` : ''
}

export async function handleProxy(request, { env = process.env, fetchImpl = globalThis.fetch, timeoutMs = PROXY_TIMEOUT_MS } = {}) {
  const url = new URL(request.url)
  const backendPath = resolveBackendPath(url)
  if (!backendPath || isInternalPath(backendPath)) {
    return json(404, { error: 'not_found' })
  }

  const base = backendBase(env)
  if (!base) return json(503, { error: 'backend_not_configured' })

  const method = request.method.toUpperCase()
  const headers = new Headers()
  for (const name of FORWARDED_REQUEST_HEADERS) {
    const v = request.headers.get(name)
    if (v !== null) headers.set(name, v)
  }
  const clientIp = request.headers.get('x-forwarded-for')
  if (clientIp) headers.set('x-forwarded-for', clientIp)
  headers.set('x-forwarded-proto', url.protocol.replace(':', ''))
  headers.set('x-forwarded-host', url.host)

  let body
  if (method !== 'GET' && method !== 'HEAD') {
    const buf = await request.arrayBuffer()
    if (buf.byteLength > 0) body = buf
  }

  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  let upstream
  let payload
  try {
    upstream = await fetchImpl(`${base}${backendPath}${upstreamSearch(url)}`, {
      method,
      headers,
      body,
      redirect: 'manual',
      signal: controller.signal,
    })
    payload = method === 'HEAD' ? null : await upstream.arrayBuffer()
  } catch (err) {
    if (err?.name === 'AbortError') {
      return json(504, { error: 'backend_timeout' })
    }
    return json(502, { error: 'backend_unreachable' })
  } finally {
    clearTimeout(timer)
  }

  const out = new Headers()
  upstream.headers.forEach((value, name) => {
    if (!DROPPED_RESPONSE_HEADERS.has(name.toLowerCase())) out.set(name, value)
  })
  // Every Set-Cookie header must survive individually (login sets the session
  // cookie; logout clears it). getSetCookie() keeps them separate.
  const cookies = typeof upstream.headers.getSetCookie === 'function'
    ? upstream.headers.getSetCookie()
    : (upstream.headers.get('set-cookie') ? [upstream.headers.get('set-cookie')] : [])
  for (const c of cookies) out.append('set-cookie', c)

  const nullBody = method === 'HEAD' || [101, 204, 205, 304].includes(upstream.status)
  return new Response(nullBody ? null : payload, { status: upstream.status, headers: out })
}

export default {
  fetch(request) {
    return handleProxy(request)
  },
}
