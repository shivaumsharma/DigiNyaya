import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { authApi } from './authApi.js'
import { getAccessToken, setAccessToken } from './tokenStore.js'

const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null)
  const [loading, setLoading] = useState(true)
  // Refresh tokens rotate on every use, and reusing an already-rotated-out
  // one revokes the whole session (theft detection, see backend/auth
  // router.py). React 19 StrictMode intentionally double-invokes mount
  // effects in dev (mount -> cleanup -> mount again); without this cache,
  // that fires two concurrent /auth/refresh calls sharing the same cookie --
  // the second presents a token the first has already rotated out, gets
  // flagged as replay, and revokes the session the first call just
  // legitimately established. Caching the in-flight promise across both
  // invocations means only one network call ever happens; each invocation's
  // own `cancelled` flag (below) still decides whether ITS handler applies
  // the result, so a genuine unmount still can't set state on a dead
  // instance.
  const refreshPromiseRef = useRef(null)

  const fetchProfile = useCallback(async (token) => {
    const profile = await authApi.me(token)
    setUser(profile)
    return profile
  }, [])

  const clearSession = useCallback(() => {
    setAccessToken(null)
    setUser(null)
  }, [])

  // On mount, try a silent refresh via the httpOnly cookie so a page reload
  // doesn't force a fresh login as long as the refresh token is still valid.
  useEffect(() => {
    let cancelled = false
    if (!refreshPromiseRef.current) {
      refreshPromiseRef.current = authApi.refresh()
    }

    refreshPromiseRef.current
      .then(async ({ access_token }) => {
        if (cancelled) return
        setAccessToken(access_token)
        await fetchProfile(access_token)
      })
      .catch(() => {
        if (!cancelled) clearSession()
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [fetchProfile, clearSession])

  const login = useCallback(
    async (tokenResponse) => {
      setAccessToken(tokenResponse.access_token)
      await fetchProfile(tokenResponse.access_token)
    },
    [fetchProfile],
  )

  const logout = useCallback(async () => {
    try {
      await authApi.logout(getAccessToken())
    } catch {
      // Best-effort: even if the network call fails, drop the local session.
    }
    clearSession()
  }, [clearSession])

  const refreshProfile = useCallback(async () => {
    const token = getAccessToken()
    if (!token) return null
    return fetchProfile(token)
  }, [fetchProfile])

  return (
    <AuthContext.Provider value={{ user, loading, login, logout, getAccessToken, refreshProfile }}>
      {children}
    </AuthContext.Provider>
  )
}

export const useAuth = () => useContext(AuthContext)
