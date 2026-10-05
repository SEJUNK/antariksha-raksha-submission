// React wiring for the session (pure logic in ./auth.js). The session lives
// in an HttpOnly cookie set by the backend; the browser keeps nothing else.
// /api/auth/me on load decides between the login screen and the console.
import { createContext, useCallback, useContext, useEffect, useMemo, useReducer } from 'react'
import { api, setUnauthorizedHandler } from './api'
import { INITIAL_AUTH, authReducer, canDo, capabilities, deniedVars } from './auth'

const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const [state, dispatch] = useReducer(authReducer, INITIAL_AUTH)

  // Any 401 from an API call (except the login attempt) ends the session.
  useEffect(() => {
    setUnauthorizedHandler(() => dispatch({ type: 'unauthorized' }))
    return () => setUnauthorizedHandler(null)
  }, [])

  const check = useCallback(() => (
    api.me()
      .then((resp) => dispatch({ type: 'session', payload: resp }))
      .catch((err) => {
        if (err?.status === 401) dispatch({ type: 'unauthorized' })
        else dispatch({ type: 'checkFailed', error: 'unreachable' })
      })
  ), [])
  useEffect(() => { check() }, [check])

  // Throws ApiError on failure (LoginScreen shows it); 401 here never
  // triggers the session-expiry handler.
  const login = useCallback(async (username, password) => {
    const resp = await api.login(username, password)
    dispatch({ type: 'session', payload: resp })
    return resp
  }, [])

  const logout = useCallback(async () => {
    try {
      await api.logout()
    } catch {
      /* already logged out / backend down -- the local state still clears */
    }
    dispatch({ type: 'logout' })
  }, [])

  const value = useMemo(() => {
    const session = state.session
    return {
      ...state,
      caps: capabilities(session),
      can: (p) => canDo(session?.permissions, p),
      deniedVars: (p) => deniedVars(session, p),
      login,
      logout,
      recheck: check,
    }
  }, [state, login, logout, check])

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

// Outside a provider (tests / stray render): signed out, nothing permitted.
const FALLBACK = {
  ...INITIAL_AUTH,
  status: 'anonymous',
  caps: capabilities(null),
  can: () => false,
  deniedVars: (p) => deniedVars(null, p),
  login: async () => { throw new Error('no auth provider') },
  logout: async () => {},
  recheck: () => {},
}

export function useAuth() {
  return useContext(AuthContext) || FALLBACK
}
