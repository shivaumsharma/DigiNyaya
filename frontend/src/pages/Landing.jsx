import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api.js'
import { useSession } from '../session.jsx'
import { useLanguage } from '../i18n/LanguageContext.jsx'
import { Shield, ArrowRight, Scales } from '../icons.jsx'

export default function Landing() {
  const { user, login } = useSession()
  const { t } = useLanguage()
  const navigate = useNavigate()
  const [name, setName] = useState('Ananya Sharma')
  const [aadhaar, setAadhaar] = useState('4821')
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')

  async function handleLogin(e) {
    e.preventDefault()
    setErr('')
    setLoading(true)
    try {
      const u = await api.login(name, aadhaar)
      login(u)
      navigate('/disputes')
    } catch (ex) {
      setErr(ex.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <section className="hero fade-in">
      <div className="hero-grid">
        <div>
          <span className="eyebrow">
            <Scales width={15} height={15} /> {t('landing.eyebrow')}
          </span>
          <h1>
            {t('landing.heroPre')} <span className="grad">{t('landing.heroHighlight1')}</span>,
            <br /> {t('landing.heroMid')} <span className="grad">{t('landing.heroHighlight2')}</span>.
          </h1>
          <p className="lede">{t('landing.lede')}</p>

          <div className="stat-row">
            <div className="stat">
              <div className="num">{t('landing.stat1Num')}</div>
              <div className="lbl">{t('landing.stat1Lbl')}</div>
            </div>
            <div className="stat">
              <div className="num">{t('landing.stat2Num')}</div>
              <div className="lbl">{t('landing.stat2Lbl')}</div>
            </div>
            <div className="stat">
              <div className="num">{t('landing.stat3Num')}</div>
              <div className="lbl">{t('landing.stat3Lbl')}</div>
            </div>
          </div>
        </div>

        <div className="card login-card fade-in">
          <h3>{t('landing.loginTitle')}</h3>
          <p className="sub">{t('landing.loginSub')}</p>
          {user ? (
            <button className="btn btn-primary btn-block btn-lg" onClick={() => navigate('/disputes')}>
              {t('landing.continueAs', { name: user.name })} <ArrowRight />
            </button>
          ) : (
            <form onSubmit={handleLogin}>
              <div className="field">
                <label>{t('landing.fieldName')}</label>
                <input
                  className="input"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder={t('landing.placeholderName')}
                  required
                />
              </div>
              <div className="field">
                <label>{t('landing.fieldAadhaar')}</label>
                <input
                  className="input"
                  value={aadhaar}
                  maxLength={4}
                  onChange={(e) => setAadhaar(e.target.value.replace(/\D/g, ''))}
                  placeholder="4821"
                  required
                />
              </div>
              {err && <p style={{ color: 'var(--red)', fontSize: '0.85rem', marginBottom: 12 }}>{err}</p>}
              <button className="btn btn-primary btn-block btn-lg" disabled={loading || aadhaar.length !== 4}>
                {loading ? t('landing.verifying') : t('landing.verifyContinue')} <ArrowRight />
              </button>
              <div className="aadhaar-badge">
                <Shield width={16} height={16} /> {t('landing.otpBadge')}
              </div>
            </form>
          )}
        </div>
      </div>
    </section>
  )
}
