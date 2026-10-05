// Pure decision logic for the dynamic (local, backend) brief translation.
// English is the source of record; a translation is shown only when the
// backend reports status === 'translated' for the SAME English text we have.

/** Should the UI ask the backend to translate this brief at all? */
export function shouldRequestTranslation(lang, event, isPending) {
  if (!lang || lang === 'en') return false
  if (isPending) return false
  return Boolean(event && ((typeof event.brief_text === 'string' && event.brief_text.trim())
    || (typeof event.maneuver_text === 'string' && event.maneuver_text.trim())))
}

/**
 * Decide what to display.
 *   lang          current UI language
 *   event         event detail ({brief_text, maneuver_text})
 *   translation   null | { loading: true } | backend /api/translation/brief response | { error }
 *   showOriginal  operator toggled "Show English original"
 * Returns { briefText, maneuverText, mode, noticeKey }
 *   mode: 'english' | 'translated' | 'loading' | 'fallback'
 */
export function briefDisplay({ lang, event, translation, showOriginal = false }) {
  const english = {
    briefText: event?.brief_text ?? null,
    maneuverText: event?.maneuver_text ?? null,
  }
  if (!lang || lang === 'en' || translation == null) {
    return { ...english, mode: 'english', noticeKey: null }
  }
  if (translation.loading) {
    return { ...english, mode: 'loading', noticeKey: 'brief.translating' }
  }
  const ok = translation.status === 'translated'
    && translation.translated === true
    && translation.target_language === lang
    && (translation.english?.brief_text ?? null) === english.briefText
    && (translation.english?.maneuver_text ?? null) === english.maneuverText
  if (!ok) {
    return { ...english, mode: 'fallback', noticeKey: 'brief.translationUnavailable' }
  }
  if (showOriginal) {
    return { ...english, mode: 'english', noticeKey: 'brief.mtLabel' }
  }
  return {
    briefText: translation.brief_text ?? english.briefText,
    maneuverText: translation.maneuver_text ?? english.maneuverText,
    mode: 'translated',
    noticeKey: 'brief.mtLabel',
  }
}
