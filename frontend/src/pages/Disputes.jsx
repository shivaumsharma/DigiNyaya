import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api.js'
import { useLanguage } from '../i18n/LanguageContext.jsx'
import Stepper from '../components/Stepper.jsx'

export default function Disputes() {
  const navigate = useNavigate()
  const { t } = useLanguage()
  const [types, setTypes] = useState([])

  useEffect(() => {
    api.disputeTypes().then(setTypes).catch(() => {})
  }, [])

  return (
    <section className="page fade-in container" style={{ width: '100%' }}>
      <Stepper current={0} />
      <div className="page-head">
        <h1 style={{ fontWeight: 400, marginBottom: 10 }}>{t('disputes.title')}</h1>
        <p style={{ fontSize: '0.95rem', maxWidth: '70ch', lineHeight: 1.6 }}>{t('disputes.subtitle')}</p>
      </div>

      <div className="dispute-grid">
        {types.map((dt) => (
          <div
            key={dt.id}
            className={`card elev-sm dispute-card ${dt.active ? '' : 'disabled'}`}
            onClick={() => dt.active && navigate(`/file/${dt.id}`)}
          >
            {!dt.active && <span className="soon">{t('disputes.roadmap')}</span>}
            <span className={`tag ${dt.tier === 1 ? 'tag-accent' : 'tag-outline'}`}>
              {t('disputes.tier', { n: dt.tier })} · {dt.tier === 1 ? t('disputes.tier1Desc') : t('disputes.tier2Desc')}
            </span>
            <div className="card-title">{dt.label}</div>
            <p className="card-body">{dt.description}</p>
          </div>
        ))}
      </div>
    </section>
  )
}
