import { useEffect, useState } from 'react'
import { Outlet, Link, useNavigate } from 'react-router-dom'
import { useSession } from './session.jsx'
import { api } from './api.js'
import { Scales, Cpu } from './icons.jsx'
import { useLanguage, SUPPORTED_UI_LANGUAGES } from './i18n/LanguageContext.jsx'

export default function App() {
  const { user, logout } = useSession()
  const { lang, setLang, t } = useLanguage()
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
              <span className="pill">{t('app.pill')}</span>
            </Link>
            {ai && (
              <span className={`engine-badge ${ai.available ? 'live' : 'scripted'}`} title={ai.engine}>
                <Cpu width={14} height={14} />
                {ai.available ? t('app.engineLive', { model: ai.model }) : t('app.engineScripted')}
              </span>
            )}
          </div>
          <div className="flex gap" style={{ alignItems: 'center' }}>
            <select
              className="lang-select"
              aria-label="Language"
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
                  {t('app.signOut')}
                </button>
              </div>
            ) : (
              <span className="user-chip">{t('app.aadhaarSecured')}</span>
            )}
          </div>
        </div>
      </header>

      <main className="container" style={{ flex: 1 }}>
        <Outlet />
      </main>

      <footer className="footer">
        <div className="container between flex">
          <span>{t('app.footerLeft')}</span>
          <span>{t('app.footerRight')}</span>
        </div>
      </footer>
    </div>
  )
}
