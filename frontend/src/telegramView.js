// Telegram notification status (ADMINISTRATOR panel). Presentation only: the
// backend owns credentials and sending; the browser only ever sees non-secret
// flags. Unknown fields in the response are ignored on purpose.

export const TELEGRAM_TIERS = ['Medium', 'High', 'Critical']

/** telegramViewModel(resp) -> { state, minRisk, notifyDemo } or null. */
export function telegramViewModel(resp) {
  if (!resp || typeof resp !== 'object') return null
  const enabled = resp.enabled === true
  const configured = resp.configured === true
  const state = !enabled ? 'disabled' : configured ? 'active' : 'unconfigured'
  const minRisk = TELEGRAM_TIERS.includes(resp.min_risk) ? resp.min_risk : 'High'
  return { state, minRisk, notifyDemo: resp.notify_demo === true }
}

/** Message key + vars for a test-send outcome (result body or ApiError). */
export function telegramTestMessage(result, err) {
  if (err) {
    if (err.status === 429) return { tone: 'error', key: 'tg.cooldown' }
    return null // caller shows the generic admin error text
  }
  if (result && result.sent === true) return { tone: 'ok', key: 'tg.testSent' }
  const error = (result && (result.error || result.status)) || 'unknown'
  return { tone: 'error', key: 'tg.testFailed', vars: { error: String(error).slice(0, 60) } }
}
