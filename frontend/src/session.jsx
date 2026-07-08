import { createContext, useContext, useState } from 'react'

const SessionContext = createContext(null)

const KEY = 'diginyaya_session'

export function SessionProvider({ children }) {
  const [user, setUser] = useState(() => {
    try {
      const raw = localStorage.getItem(KEY)
      return raw ? JSON.parse(raw) : null
    } catch {
      return null
    }
  })

  const login = (u) => {
    setUser(u)
    localStorage.setItem(KEY, JSON.stringify(u))
  }
  const logout = () => {
    setUser(null)
    localStorage.removeItem(KEY)
  }

  return (
    <SessionContext.Provider value={{ user, login, logout }}>
      {children}
    </SessionContext.Provider>
  )
}

export const useSession = () => useContext(SessionContext)
