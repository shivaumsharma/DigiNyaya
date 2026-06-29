import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api.js'
import { useSession } from '../session.jsx'
import { Shield, ArrowRight, Scales } from '../icons.jsx'

export default function Landing() {
  const { user, login } = useSession()
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
            <Scales width={15} height={15} /> Resolving India's 50 million pending cases
          </span>
          <h1>
            Justice in <span className="grad">30 minutes</span>,
            <br /> not <span className="grad">7 years</span>.
          </h1>
          <p className="lede">
            DigiNyaya is an AI-native digital court. Five specialised agents parse your
            claim, research precedent, mediate and issue a binding resolution — end to end,
            with a human only where one is truly needed.
          </p>

          <div className="stat-row">
            <div className="stat">
              <div className="num">5 agents</div>
              <div className="lbl">Coordinated by an orchestrator</div>
            </div>
            <div className="stat">
              <div className="num">~4 min</div>
              <div className="lbl">vs 18 months in Consumer Forum</div>
            </div>
            <div className="stat">
              <div className="num">Tier 1</div>
              <div className="lbl">Fully autonomous resolution</div>
            </div>
          </div>
        </div>

        <div className="card login-card fade-in">
          <h3>Log in to file a dispute</h3>
          <p className="sub">Secure citizen access via Aadhaar (simulated for demo).</p>
          {user ? (
            <button className="btn btn-primary btn-block btn-lg" onClick={() => navigate('/disputes')}>
              Continue as {user.name} <ArrowRight />
            </button>
          ) : (
            <form onSubmit={handleLogin}>
              <div className="field">
                <label>Full name</label>
                <input
                  className="input"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="As per Aadhaar"
                  required
                />
              </div>
              <div className="field">
                <label>Aadhaar — last 4 digits</label>
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
                {loading ? 'Verifying…' : 'Verify & continue'} <ArrowRight />
              </button>
              <div className="aadhaar-badge">
                <Shield width={16} height={16} /> OTP-verified identity · zero documents to carry
              </div>
            </form>
          )}
        </div>
      </div>
    </section>
  )
}
