import { useEffect, useState } from 'react'
import { Outlet, Link, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from './auth/AuthContext.jsx'
import { SUPPORTED_UI_LANGUAGES, useLanguage } from './i18n/LanguageContext.jsx'
import { api } from './api'
import { Logo, Cpu } from './icons.jsx'

export default function App() {
  const { user, logout } = useAuth()
  const { lang, setLang } = useLanguage()
  const navigate = useNavigate()
  const location = useLocation()
  // The homepage brings its own navigation (hero nav + sticky nav).
  const isHome = location.pathname === '/'
  const [ai, setAi] = useState(null)

  useEffect(() => {
    api.aiStatus().then(setAi).catch(() => {})
  }, [])

  async function handleSignOut() {
    await logout()
    navigate('/')
  }

  const initial = user?.full_name?.[0]?.toUpperCase() || '?'

  return (
    <div className="app-shell">
      {!isHome && (
        <header className="topbar">
          <div className="container topbar-inner">
            <Link to="/" className="brand">
              <span className="logo">
                <Logo />
              </span>
              <div>
                <div className="wordmark">
                  Digi<span className="nya">Nyaya</span>
                </div>
                <div className="tagline">Online Dispute Resolution</div>
              </div>
            </Link>

            <div className="topbar-actions">
              {ai && (
                <span className={`engine-badge ${ai.available ? 'live' : 'scripted'}`} title={ai.engine}>
                  <Cpu width={14} height={14} />
                  {ai.available ? `Live LLM · ${ai.model}` : 'Scripted engine'}
                </span>
              )}
              <select
                className="select"
                style={{ width: 'auto', padding: '7px 10px' }}
                value={lang}
                onChange={(e) => setLang(e.target.value)}
                aria-label="Language"
              >
                {SUPPORTED_UI_LANGUAGES.map((l) => (
                  <option key={l.code} value={l.code}>
                    {l.label}
                  </option>
                ))}
              </select>
              {user ? (
                <div className="topbar-user">
                  <Link to="/my-cases" className="btn btn-ghost">
                    My cases
                  </Link>
                  {user.is_reviewer && (
                    <Link to="/reviewer" className="btn btn-ghost">
                      Review queue
                    </Link>
                  )}
                  <div className="user-chip topbar-chip">
                    <span className="avatar">{initial}</span>
                    <span className="user-name">{user.full_name}</span>
                  </div>
                  <button className="btn" onClick={handleSignOut}>
                    Sign out
                  </button>
                </div>
              ) : (
                <Link to="/login" className="btn btn-primary">
                  Sign in
                </Link>
              )}
            </div>
          </div>
        </header>
      )}

      <main style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
        <Outlet />
      </main>
    </div>
  )
}
