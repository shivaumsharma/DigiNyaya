import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api.js'
import { useSession } from '../session.jsx'
import { DISPUTE_ICONS } from '../icons.jsx'
import Stepper from '../components/Stepper.jsx'

export default function Disputes() {
  const { user } = useSession()
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
        <h2>What's your dispute about?</h2>
        <p>
          Select a category. Tier 1 cases are resolved end-to-end by AI agents. More
          categories roll out across the phased roadmap.
        </p>
      </div>

      <div className="dispute-grid">
        {types.map((t) => {
          const Icon = DISPUTE_ICONS[t.icon] || DISPUTE_ICONS['file-text']
          return (
            <div
              key={t.id}
              className={`card dispute-card ${t.active ? '' : 'disabled'}`}
              onClick={() => t.active && navigate(`/file/${t.id}`)}
            >
              {!t.active && <span className="soon">Roadmap</span>}
              <span className="tier-tag">Tier {t.tier}</span>
              <div className="ic">
                <Icon width={24} height={24} />
              </div>
              <h4>{t.label}</h4>
              <p>{t.description}</p>
            </div>
          )
        })}
      </div>
    </section>
  )
}
