// React wiring for the UI text size (pure logic in ./uiScale.js).
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import { DEFAULT_SCALE, effectiveScale, loadScale, normalizeScale, overlayLayout, saveScale, stepScale } from './uiScale'

function safeStorage() {
  try {
    return typeof window !== 'undefined' ? window.localStorage : null
  } catch {
    return null
  }
}

function viewport() {
  if (typeof window === 'undefined' || !window.innerWidth) return { w: 1920, h: 1080 }
  return { w: window.innerWidth, h: window.innerHeight }
}

const UiScaleContext = createContext(null)

export function UiScaleProvider({ children }) {
  const [selected, setSelected] = useState(() => loadScale(safeStorage()))
  const [vp, setVp] = useState(viewport)

  useEffect(() => {
    const onResize = () => setVp(viewport())
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  const setScale = useCallback((s) => {
    const v = normalizeScale(s)
    setSelected(v)
    saveScale(safeStorage(), v)
  }, [])

  const value = useMemo(() => {
    const scale = effectiveScale(selected, vp.w, vp.h)
    return {
      selected,
      scale,
      layout: overlayLayout(scale, vp.w, vp.h),
      setScale,
      increase: () => setScale(stepScale(selected, +1)),
      decrease: () => setScale(stepScale(selected, -1)),
      reset: () => setScale(DEFAULT_SCALE),
    }
  }, [selected, vp, setScale])

  return <UiScaleContext.Provider value={value}>{children}</UiScaleContext.Provider>
}

const FALLBACK = {
  selected: DEFAULT_SCALE,
  scale: DEFAULT_SCALE,
  layout: overlayLayout(DEFAULT_SCALE, 1920, 1080),
  setScale: () => {},
  increase: () => {},
  decrease: () => {},
  reset: () => {},
}

export function useUiScale() {
  return useContext(UiScaleContext) || FALLBACK
}
