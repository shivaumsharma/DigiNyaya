import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, ApiError } from '../api'
import { useLanguage } from '../i18n/LanguageContext.jsx'

export default function MatterGuide() {
  const { id } = useParams()
  const { t, lang } = useLanguage()
  // The result is stored together with the id it was fetched for, so a stale result from a previous guide is never
  // shown and no state has to be reset synchronously inside the effect.
  const [result, setResult] = useState({ id: null, guide: null, error: null }) // error: 'notFound' | 'loadError'

  useEffect(() => {
    let cancelled = false
    api
      .matterGuide(id, lang)
      .then((guide) => !cancelled && setResult({ id, guide, error: null }))
      .catch((err) => !cancelled && setResult({ id, guide: null, error: err instanceof ApiError && err.status === 404 ? 'notFound' : 'loadError' }))
    return () => {
      cancelled = true
    }
  }, [id, lang])

  const current = result.id === id
  const guide = current ? result.guide : null
  const error = current ? result.error : null

  return (
    <section className="page fade-in container" style={{ width: '100%', maxWidth: 860 }}>
      <Link to="/matters" className="btn btn-ghost">← {t('matters.guide.back')}</Link>

      {error && <p role="alert" style={{ marginTop: 20 }}>{t(`matters.guide.${error}`)}</p>}
      {!guide && !error && <p style={{ marginTop: 20 }}>{t('matters.loading')}</p>}

      {guide && (
        <article style={{ marginTop: 20 }}>
          <span className="tag tag-neutral">{t(`matters.level.${guide.level_name}`)}</span>
          <h1 style={{ fontWeight: 400, margin: '10px 0' }}>{guide.label}</h1>
          <p style={{ lineHeight: 1.6 }}>{guide.summary}</p>

          <div className="matter-notice" role="note">
            {!guide.reviewed && <strong>{t('matters.guide.unreviewed')} </strong>}
            {guide.disclaimer}
          </div>
          {lang !== 'en-IN' && guide.language === 'en' && <p className="matter-notice" role="note">{t('matters.guide.englishOnly')}</p>}
          {guide.language && guide.language !== 'en' && guide.translation_reviewed === false && (
            <p className="matter-notice" role="note">{t('matters.guide.machineTranslated')}</p>
          )}

          <h2 className="matter-heading">{t('matters.guide.forum')}</h2>
          <p style={{ lineHeight: 1.6 }}>{guide.forum}</p>

          <h2 className="matter-heading">{t('matters.guide.steps')}</h2>
          <ol className="matter-list">
            {guide.steps.map((s) => <li key={s}>{s}</li>)}
          </ol>

          <h2 className="matter-heading">{t('matters.guide.documents')}</h2>
          <ul className="matter-list">
            {guide.documents.map((d) => <li key={d}>{d}</li>)}
          </ul>

          <h2 className="matter-heading">{t('matters.guide.cannot')}</h2>
          <ul className="matter-list">
            {guide.this_tool_cannot.map((d) => <li key={d}>{d}</li>)}
          </ul>

          <p style={{ marginTop: 24, color: 'var(--text-dim)' }}>{t('matters.guide.legalAid')}</p>
        </article>
      )}
    </section>
  )
}
