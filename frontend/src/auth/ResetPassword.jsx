import { useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { authApi } from './authApi.js'
import PasswordInput from './PasswordInput.jsx'

// Landing page for the link the backend's mail stub prints:
// {FRONTEND_URL}/reset-password?token=...
export default function ResetPassword() {
  const [params] = useSearchParams()
  const token = params.get('token') || ''
  const navigate = useNavigate()
  const [password, setPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')
  const [done, setDone] = useState(false)

  async function handleSubmit(e) {
    e.preventDefault()
    setErr('')
    setLoading(true)
    try {
      await authApi.passwordResetConfirm(token, password)
      setDone(true)
    } catch (ex) {
      setErr(ex.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <section className="fade-in" style={{ maxWidth: 440, margin: '48px auto' }}>
      <div className="card card-pad">
        <h3>Set a new password</h3>
        {!token ? (
          <p style={{ color: 'var(--red)', fontSize: '0.85rem', marginTop: 12 }}>
            This link is missing its reset token.
          </p>
        ) : done ? (
          <>
            <p className="sub" style={{ marginTop: 10 }}>Your password has been updated.</p>
            <button className="btn btn-primary btn-block btn-lg" style={{ marginTop: 14 }} onClick={() => navigate('/login')}>
              Sign in
            </button>
          </>
        ) : (
          <form onSubmit={handleSubmit} style={{ marginTop: 14 }}>
            <div className="field">
              <label htmlFor="rp-new-password">New password</label>
              <PasswordInput
                id="rp-new-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="At least 8 characters"
                minLength={8}
                autoComplete="new-password"
              />
            </div>
            {err && <p style={{ color: 'var(--red)', fontSize: '0.85rem', marginBottom: 12 }}>{err}</p>}
            <button className="btn btn-primary btn-block btn-lg" disabled={loading}>
              {loading ? 'Updating…' : 'Update password'}
            </button>
          </form>
        )}
      </div>
    </section>
  )
}
