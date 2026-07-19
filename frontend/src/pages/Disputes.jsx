import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api.js'
import { useSession } from '../session.jsx'
import { useLanguage } from '../i18n/LanguageContext.jsx'
import { DISPUTE_ICONS } from '../icons.jsx'
import Stepper from '../components/Stepper.jsx'

export default function Disputes() {
  const { user } = useSession()
  const { t } = useLanguage()
  const navigate = useNavigate()
  const [types, setTypes] = useState([])

  useEffect(() => {
    if (!user) navigate('/')
  }, [user, navigate])

  useEffect(() => {
    api.disputeTypes().then(setTypes).catch(() => {})
  }, [])

  return (
    <section className="page fade-in">
      <Stepper current={0} />
      <div className="page-head">
        <h2>{t('disputes.title')}</h2>
        <p>{t('disputes.subtitle')}</p>
      </div>

      <div className="dispute-grid">
        {types.map((t2) => {
          const Icon = DISPUTE_ICONS[t2.icon] || DISPUTE_ICONS['file-text']
          return (
            <div
              key={t2.id}
              className={`card dispute-card ${t2.active ? '' : 'disabled'}`}
              onClick={() => t2.active && navigate(`/file/${t2.id}`)}
            >
              {!t2.active && <span className="soon">{t('disputes.roadmap')}</span>}
              <span className="tier-tag">{t('disputes.tier', { n: t2.tier })}</span>
              <div className="ic">
                <Icon width={24} height={24} />
              </div>
              <h4>{t2.label}</h4>
              <p>{t2.description}</p>
            </div>
          )
        })}
      </div>
    </section>
  )
}
