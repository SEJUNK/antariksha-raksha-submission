export const DEFAULT_API_BASE = 'http://localhost:8000'

/**
 * API base URL (pure, unit-tested). VITE_API_BASE_URL wins when it is
 * defined: an empty string means same-origin relative requests ('/api/...',
 * used when a reverse proxy such as the Vercel rewrite serves /api);
 * otherwise the configured origin without a trailing slash. Undefined ->
 * the local backend.
 */
export function resolveApiBase(value) {
  if (value === undefined || value === null) return DEFAULT_API_BASE
  return String(value).trim().replace(/\/+$/, '')
}

export const BASE_URL = resolveApiBase(import.meta.env?.VITE_API_BASE_URL)

// Optional deployment key (backend ANTARIKSHA_API_KEY). Unset in local jury
// mode. It is embedded in the bundle, so it is a shared key, not user identity.
const API_KEY = import.meta.env?.VITE_API_KEY

// Header every state-changing request carries (backend CSRF guard: a plain
// cross-site form post cannot set a custom header).
export const CLIENT_HEADER = 'X-Antariksha-Client'
export const CLIENT_HEADER_VALUE = 'console'
export const LOGIN_PATH = '/api/auth/login'

/**
 * Pure fetch-option builder (unit-tested): every request sends the session
 * cookie (credentials: 'include'); every non-GET request adds the CSRF guard
 * header and, if configured, the optional deployment key.
 */
export function buildRequestOptions(options = {}, { apiKey = API_KEY } = {}) {
  const method = String(options.method || 'GET').toUpperCase()
  const headers = { ...(options.headers || {}) }
  if (method !== 'GET' && method !== 'HEAD') {
    headers[CLIENT_HEADER] = CLIENT_HEADER_VALUE
    if (apiKey) headers['X-API-Key'] = apiKey
  }
  return { ...options, method, headers, credentials: 'include' }
}

/** A 401 means "session ended" for every call except the login attempt itself. */
export function isSessionExpiry(path, status) {
  return status === 401 && !String(path || '').startsWith(LOGIN_PATH)
}

/** Error carrying the HTTP status and the backend's machine-readable code. */
export class ApiError extends Error {
  constructor(message, { status = 0, code = null, path = null } = {}) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.path = path
  }
}

/** Human-readable detail from a FastAPI error body (pure). */
export function errorDetail(body, fallback) {
  if (typeof body?.detail === 'string' && body.detail) return body.detail
  // 422 validation errors carry a list of {msg, loc} objects.
  if (Array.isArray(body?.detail) && body.detail[0]?.msg) return body.detail[0].msg
  if (typeof body?.message === 'string' && body.message) return body.message
  return fallback
}

// Called on any session-expiry 401 (AuthProvider sends the app back to the
// login screen). One handler; replaced, never stacked.
let unauthorizedHandler = null
export function setUnauthorizedHandler(fn) {
  unauthorizedHandler = typeof fn === 'function' ? fn : null
}

async function request(path, options = {}) {
  const res = await fetch(`${BASE_URL}${path}`, buildRequestOptions(options))
  if (!res.ok) {
    // FastAPI error responses carry a human-readable `detail` string (e.g.
    // DemoScenarioError messages, 403 "forbidden" explanations) -- surface
    // that instead of a bare status code.
    let body = null
    try {
      body = await res.json()
    } catch {
      /* non-JSON error body -- fall back to the status line */
    }
    if (isSessionExpiry(path, res.status) && unauthorizedHandler) {
      try { unauthorizedHandler() } catch { /* never break the caller */ }
    }
    throw new ApiError(errorDetail(body, `${path} -> HTTP ${res.status}`), {
      status: res.status, code: typeof body?.error === 'string' ? body.error : null, path,
    })
  }
  return res.json()
}

const jsonBody = (method, payload) => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(payload ?? {}),
})

export const api = {
  // Session (HttpOnly cookie set by the backend; nothing stored client-side).
  login: (username, password) => request(LOGIN_PATH, jsonBody('POST', { username, password })),
  logout: () => request('/api/auth/logout', { method: 'POST' }),
  me: () => request('/api/auth/me'),
  getObjects: () => request('/api/objects'),
  getEvents: () => request('/api/events'),
  getEvent: (id) => request(`/api/events/${id}`),
  approveEvent: (id) => request(`/api/events/${id}/approve`, { method: 'POST' }),
  dismissEvent: (id, reason) => request(`/api/events/${id}/dismiss`, jsonBody('POST', { reason: reason || null })),
  getDecisionStats: () => request('/api/decisions/stats'),
  getEventProfile: (id) => request(`/api/events/${id}/profile`),
  getEventProvenance: (id) => request(`/api/events/${id}/provenance`),
  getEventDecisions: (id) => request(`/api/events/${id}/decisions`),
  // Stored observations of the same tracked encounter across screening runs.
  getEventEvolution: (id) => request(`/api/events/${id}/evolution`),
  // Latest vs previous screening run of the same domain ('real' | 'demo').
  getScreeningDelta: (domain) => request(`/api/screening/delta${domain ? `?domain=${encodeURIComponent(domain)}` : ''}`),
  decisionsCsvUrl: `${BASE_URL}/api/decisions/export`,
  // Operator-configured protected assets vs the public object catalog, and
  // objects new to the LOCAL catalog (review = audit acknowledgement only).
  getRegistry: () => request('/api/catalog/registry'),
  getNewObjects: () => request('/api/catalog/new-objects'),
  reviewNewObject: (noradId, note) => request(`/api/catalog/new-objects/${encodeURIComponent(noradId)}/review`, jsonBody('POST', { note: note || null })),
  // Protected-asset registry management (manage_assets). Changes take effect
  // at the next successful refresh (backend: effective = next_successful_refresh).
  createProtectedAsset: (payload) => request('/api/protected-assets', jsonBody('POST', payload)),
  updateProtectedAsset: (id, patch) => request(`/api/protected-assets/${encodeURIComponent(id)}`, jsonBody('PATCH', patch)),
  setProtectedAssetStatus: (id, status, reason) => request(
    `/api/protected-assets/${encodeURIComponent(id)}/status`,
    jsonBody('POST', reason ? { status, reason } : { status }),
  ),
  getProtectedAssetAudit: () => request('/api/protected-assets/audit'),
  // Local account administration (manage_users / view_admin_audit).
  getUsers: () => request('/api/admin/users'),
  createUser: (payload) => request('/api/admin/users', jsonBody('POST', payload)),
  updateUser: (id, patch) => request(`/api/admin/users/${encodeURIComponent(id)}`, jsonBody('PATCH', patch)),
  getAdminAudit: () => request('/api/admin/audit'),
  // Optional Telegram notifications (configure_system): non-secret status and a
  // server-built test message. Credentials never reach the browser.
  getTelegramStatus: () => request('/api/admin/notifications/telegram'),
  sendTelegramTest: () => request('/api/admin/notifications/telegram/test', { method: 'POST' }),
  // A 409 {error:'refresh_in_progress'} means another refresh is running.
  refresh: () => request('/api/refresh', { method: 'POST' }),
  // Future screening window used by the NEXT screening run (configure_system).
  getScreeningHorizon: () => request('/api/settings/screening-horizon'),
  setScreeningHorizon: (hours) => request('/api/settings/screening-horizon', jsonBody('PUT', { hours })),
  getHealth: () => request('/api/health'),
  getStatus: () => request('/api/status'),
  getTracks: () => request('/api/tracks'),
  // Disclosed demo scenario. historyStep 1 | 2 = controlled two-step DEMO
  // HISTORY of one demo track (step 2 without step 1 -> 409 with a detail).
  demoSeed: (mode = 'collision', { historyStep } = {}) => request(
    `/api/demo/seed?mode=${encodeURIComponent(mode)}${historyStep === 1 || historyStep === 2 ? `&history_step=${historyStep}` : ''}`,
    { method: 'POST' },
  ),
  // Optional LOCAL brief translation (backend IndicTrans2; never an external
  // service). Read-only: English stays the source of record.
  getTranslationStatus: () => request('/api/translation/status'),
  translateBrief: (eventId, targetLanguage) => request('/api/translation/brief', jsonBody('POST', { event_id: eventId, target_language: targetLanguage })),
}
