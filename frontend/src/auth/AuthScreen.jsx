import { useEffect, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { api } from '../api.js'
import { useAuth } from './AuthContext.jsx'
import { authApi } from './authApi.js'
import EmailPasswordForm from './EmailPasswordForm.jsx'
import PhoneOtpForm from './PhoneOtpForm.jsx'

export default function AuthScreen() {
  const { login } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [mode, setMode] = useState('login') // 'login' | 'signup'
  const [method, setMethod] = useState('email') // 'email' | 'phone'
  const [languages, setLanguages] = useState([])
  const [showForgot, setShowForgot] = useState(false)

  useEffect(() => {
    api
      .languages()
      .then((r) => setLanguages(r.languages))
      .catch(() => setLanguages([]))
  }, [])

  const redirectTo = location.state?.from?.pathname || '/disputes'

  async function handleEmailSubmit(payload) {
    const tokens =
      mode === 'signup' ? await authApi.signupEmail(payload) : await authApi.loginEmail(payload.email, payload.password)
    await login(tokens)
    navigate(redirectTo, { replace: true })
  }

  async function handlePhoneVerify(phone, otp, extra) {
    const tokens =
      mode === 'signup'
        ? await authApi.signupPhoneVerify({ phone, otp, ...extra })
        : await authApi.loginPhoneVerify(phone, otp)
    await login(tokens)
    navigate(redirectTo, { replace: true })
  }

  if (showForgot) {
    return <ForgotPasswordCard onBack={() => setShowForgot(false)} />
  }

  return (
    <section className="fade-in" style={{ maxWidth: 440, margin: '48px auto' }}>
      <div className="card card-pad">
        <div className="flex gap" style={{ marginBottom: 18 }}>
          <button
            type="button"
            className={`btn ${mode === 'login' ? 'btn-primary' : 'btn-ghost'}`}
            style={{ flex: 1 }}
            onClick={() => setMode('login')}
          >
            Sign in
          </button>
          <button
            type="button"
            className={`btn ${mode === 'signup' ? 'btn-primary' : 'btn-ghost'}`}
            style={{ flex: 1 }}
            onClick={() => setMode('signup')}
          >
            Create account
          </button>
        </div>

        <div className="flex gap" style={{ marginBottom: 18 }}>
          <button
            type="button"
            className={`btn btn-small ${method === 'email' ? 'btn-primary' : 'btn-ghost'}`}
            style={{ flex: 1, marginLeft: 0 }}
            onClick={() => setMethod('email')}
          >
            Email
          </button>
          <button
            type="button"
            className={`btn btn-small ${method === 'phone' ? 'btn-primary' : 'btn-ghost'}`}
            style={{ flex: 1, marginLeft: 0 }}
            onClick={() => setMethod('phone')}
          >
            Phone
          </button>
        </div>

        {method === 'email' ? (
          <EmailPasswordForm
            mode={mode}
            languages={languages}
            onSubmit={handleEmailSubmit}
            onForgotPassword={mode === 'login' ? () => setShowForgot(true) : undefined}
          />
        ) : (
          <PhoneOtpForm
            needsProfile={mode === 'signup'}
            languages={languages}
            onStart={mode === 'signup' ? authApi.signupPhoneStart : authApi.loginPhoneStart}
            onVerify={handlePhoneVerify}
          />
        )}
      </div>
    </section>
  )
}

function ForgotPasswordCard({ onBack }) {
  const [email, setEmail] = useState('')
  const [sent, setSent] = useState(false)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')

  async function handleSubmit(e) {
    e.preventDefault()
    setErr('')
    setLoading(true)
    try {
      await authApi.passwordResetRequest(email)
      setSent(true) // Same UI regardless of whether the account exists -- matches the backend's enumeration-safe response.
    } catch (ex) {
      setErr(ex.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <section className="fade-in" style={{ maxWidth: 440, margin: '48px auto' }}>
      <div className="card card-pad">
        <h3>Reset your password</h3>
        {sent ? (
          <p className="sub" style={{ marginTop: 10 }}>
            If an account exists for that email, a reset link has been sent.
          </p>
        ) : (
          <form onSubmit={handleSubmit} style={{ marginTop: 14 }}>
            <div className="field">
              <label htmlFor="fp-email">Email</label>
              <input
                id="fp-email"
                className="input"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
              />
            </div>
            {err && <p style={{ color: 'var(--red)', fontSize: '0.85rem', marginBottom: 12 }}>{err}</p>}
            <button className="btn btn-primary btn-block btn-lg" disabled={loading}>
              {loading ? 'Sending…' : 'Send reset link'}
            </button>
          </form>
        )}
        <button type="button" className="btn btn-ghost btn-block" style={{ marginTop: 10 }} onClick={onBack}>
          Back to sign in
        </button>
      </div>
    </section>
  )
}
