// React wiring for the local UI i18n. Dictionaries are bundled JSON (no
// network). Pure logic lives in ./core.js.
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import en from './en.json'
import hi from './hi.json'
import ta from './ta.json'
import te from './te.json'
import mr from './mr.json'
import bn from './bn.json'
import gu from './gu.json'
import kn from './kn.json'
import ml from './ml.json'
import pa from './pa.json'
import or from './or.json'
import {
  acronymTooltip, acronymTooltipsFor, languageInfo, loadLanguage, normalizeLanguage, saveLanguage, translate,
} from './core'

export const DICTIONARIES = { en, hi, ta, te, mr, bn, gu, kn, ml, pa, or }

function safeStorage() {
  try {
    return typeof window !== 'undefined' ? window.localStorage : null
  } catch {
    return null
  }
}

const I18nContext = createContext(null)

export function I18nProvider({ children }) {
  const [lang, setLangState] = useState(() => loadLanguage(safeStorage()))

  const setLang = useCallback((next) => {
    const code = normalizeLanguage(next)
    setLangState(code)
    saveLanguage(safeStorage(), code)
  }, [])

  useEffect(() => {
    try { document.documentElement.lang = lang } catch { /* no DOM */ }
  }, [lang])

  const value = useMemo(() => ({
    lang,
    setLang,
    info: languageInfo(lang),
    t: (key, vars) => translate(DICTIONARIES, lang, key, vars),
    tip: (text) => acronymTooltipsFor(DICTIONARIES, lang, text),
    acro: (code) => acronymTooltip(DICTIONARIES, lang, code),
  }), [lang, setLang])

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>
}

// Outside a provider (shouldn't happen) fall back to English.
const FALLBACK = {
  lang: 'en',
  setLang: () => {},
  info: languageInfo('en'),
  t: (key, vars) => translate(DICTIONARIES, 'en', key, vars),
  tip: (text) => acronymTooltipsFor(DICTIONARIES, 'en', text),
  acro: (code) => acronymTooltip(DICTIONARIES, 'en', code),
}

export function useI18n() {
  return useContext(I18nContext) || FALLBACK
}
