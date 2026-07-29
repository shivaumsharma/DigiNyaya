import { useEffect, useState } from 'react'
import { Outlet, Link, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from './auth/AuthContext.jsx'
import { SUPPORTED_UI_LANGUAGES, useLanguage } from './i18n/LanguageContext.jsx'
import { api } from './api.js'
import { Logo, Cpu } from './icons.jsx'

const HOME_NAV = [
  { href: '#agents', label: 'Under the hood' },
  { href: '#lifecycle', label: 'Case lifecycle' },
  { href: '#metrics', label: 'Outcomes' },
  { href: '#legal', label: 'Legal framework' },
]

export default function App() {
  const { user, logout } = useAuth()
  const { lang, setLang } = useLanguage()
  const navigate = useNavigate()
  const location = useLocation()
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

          {isHome && (
            <nav style={{ display: 'flex', gap: 26, fontSize: '0.88rem' }}>
              {HOME_NAV.map((n) => (
                <a key={n.href} href={n.href}>
                  {n.label}
                </a>
              ))}
            </nav>
          )}

          <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
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
            >
              {SUPPORTED_UI_LANGUAGES.map((l) => (
                <option key={l.code} value={l.code}>
                  {l.label}
                </option>
              ))}
            </select>
            {user ? (
              <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
                <div className="user-chip" style={{ borderLeft: '1px solid var(--color-divider)', paddingLeft: 16 }}>
                  <span className="avatar">{initial}</span>
                  <span>{user.full_name}</span>
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

      <main style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
        <Outlet />
      </main>
    </div>
  )
}
