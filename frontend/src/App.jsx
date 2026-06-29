import { useEffect, useState } from 'react'
import { Outlet, Link, useNavigate } from 'react-router-dom'
import { useSession } from './session.jsx'
import { api } from './api.js'
import { Scales, Cpu } from './icons.jsx'

export default function App() {
  const { user, logout } = useSession()
  const navigate = useNavigate()
  const [ai, setAi] = useState(null)

  useEffect(() => {
    api.aiStatus().then(setAi).catch(() => {})
  }, [])

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="container topbar-inner">
          <div className="flex gap" style={{ alignItems: 'center' }}>
            <Link to="/" className="brand">
              <span className="logo">
                <Scales />
              </span>
              Digi<span className="nya">Nyaya</span>
              <span className="pill">AI-Native ODR</span>
            </Link>
            {ai && (
              <span className={`engine-badge ${ai.available ? 'live' : 'scripted'}`} title={ai.engine}>
                <Cpu width={14} height={14} />
                {ai.available ? `Live LLM · ${ai.model}` : 'Scripted engine'}
              </span>
            )}
          </div>
          {user ? (
            <div className="flex gap" style={{ alignItems: 'center' }}>
              <div className="user-chip">
                <span className="avatar">{user.name?.[0]?.toUpperCase() || 'C'}</span>
                <span>
                  {user.name}
                  <br />
                  <span style={{ fontSize: '0.72rem', color: 'var(--text-faint)' }}>
                    {user.masked_aadhaar}
                  </span>
                </span>
              </div>
              <button
                className="btn btn-ghost"
                style={{ padding: '8px 14px' }}
                onClick={() => {
                  logout()
                  navigate('/')
                }}
              >
                Sign out
              </button>
            </div>
          ) : (
            <span className="user-chip">Aadhaar-secured</span>
          )}
        </div>
      </header>

      <main className="container" style={{ flex: 1 }}>
        <Outlet />
      </main>

      <footer className="footer">
        <div className="container between flex">
          <span>DigiNyaya · Hackathon MVP · Tier 1 Autonomous Resolution</span>
          <span>Demo prototype — not a real court order</span>
        </div>
      </footer>
    </div>
  )
}
