import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { useLanguage } from '../i18n/LanguageContext.jsx'
import { groupMatters } from './matterGroups.js'

function LevelTag({ matter, t }) {
  return <span className="tag tag-neutral">{t(`matters.level.${matter.level_name}`)}</span>
}

export default function Matters() {
  const { t } = useLanguage()
  const [matters, setMatters] = useState(null)
  const [error, setError] = useState(false)
  const [query, setQuery] = useState('')

  useEffect(() => {
    api.matters().then(setMatters).catch(() => setError(true))
  }, [])

  const groups = useMemo(() => groupMatters(matters || [], query), [matters, query])
  const empty = matters && !groups.resolve.length && !groups.soon.length && !groups.guides.length

  return (
    <section className="page fade-in container" style={{ width: '100%' }}>
      <div className="page-head">
        <h1 style={{ fontWeight: 400, marginBottom: 10 }}>{t('matters.title')}</h1>
        <p style={{ fontSize: '0.95rem', maxWidth: '70ch', lineHeight: 1.6 }}>{t('matters.subtitle')}</p>
        <input
          className="input"
          type="search"
          aria-label={t('matters.search')}
          placeholder={t('matters.search')}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          style={{ maxWidth: 360, marginTop: 14 }}
        />
      </div>

      {error && <p role="alert">{t('matters.loadError')}</p>}
      {!matters && !error && <p>{t('matters.loading')}</p>}
      {empty && <p>{t('matters.noResults')}</p>}

      {groups.resolve.length > 0 && (
        <div className="matter-section">
          <h2 className="matter-heading">{t('matters.sections.resolve')}</h2>
          <div className="dispute-grid">
            {groups.resolve.map((m) => (
              <div key={m.id} className="card elev-sm dispute-card matter-card">
                <LevelTag matter={m} t={t} />
                <div className="card-title">{m.label}</div>
                <p className="card-body">{m.description}</p>
                <Link className="btn btn-primary" to={`/file/${m.id}`}>{t('matters.fileCta')}</Link>
              </div>
            ))}
          </div>
        </div>
      )}

      {groups.soon.length > 0 && (
        <div className="matter-section">
          <h2 className="matter-heading">{t('matters.sections.soon')}</h2>
          <div className="dispute-grid">
            {groups.soon.map((m) => (
              <div key={m.id} className="card elev-sm dispute-card matter-card disabled">
                <span className="soon">{t('matters.roadmap')}</span>
                <LevelTag matter={m} t={t} />
                <div className="card-title">{m.label}</div>
                <p className="card-body">{m.description}</p>
              </div>
            ))}
          </div>
        </div>
      )}

      {groups.guides.length > 0 && (
        <div className="matter-section">
          <h2 className="matter-heading">{t('matters.sections.guides')}</h2>
          {groups.guides.map((g) => (
            <div key={g.family} style={{ marginBottom: 'var(--space-6)' }}>
              <h3 className="matter-family">{t(`matters.families.${g.family}`)}</h3>
              <div className="dispute-grid">
                {g.items.map((m) => (
                  <div key={m.id} className="card elev-sm dispute-card matter-card">
                    <LevelTag matter={m} t={t} />
                    <div className="card-title">{m.label}</div>
                    <p className="card-body">{m.description}</p>
                    <Link className="btn" to={`/matters/${m.id}`}>{t('matters.readGuide')}</Link>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  )
}
