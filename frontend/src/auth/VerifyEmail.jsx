import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { authApi } from './authApi.js'

// Landing page for the link the backend's mail stub prints:
// {FRONTEND_URL}/verify-email?token=...
export default function VerifyEmail() {
  const [params] = useSearchParams()
  const token = params.get('token') || ''
  // No token in the URL is knowable synchronously from render (not an
  // external-system result), so it seeds initial state directly rather than
  // being set from inside the effect below.
  const [status, setStatus] = useState(token ? 'checking' : 'error') // 'checking' | 'ok' | 'error'
  const [message, setMessage] = useState(token ? '' : 'This link is missing its verification token.')

  useEffect(() => {
    if (!token) return
    authApi
      .verifyEmail(token)
      .then((r) => {
        setStatus('ok')
        setMessage(r.message)
      })
      .catch((ex) => {
        setStatus('error')
        setMessage(ex.message)
      })
  }, [token])

  return (
    <section className="fade-in" style={{ maxWidth: 440, margin: '48px auto' }}>
      <div className="card card-pad" style={{ textAlign: 'center' }}>
        <h3>Email verification</h3>
        <p className="sub" style={{ marginTop: 10, color: status === 'error' ? 'var(--red)' : undefined }}>
          {status === 'checking' ? 'Verifying…' : message}
        </p>
        {status !== 'checking' && (
          <Link to="/disputes" className="btn btn-primary btn-block btn-lg" style={{ marginTop: 14 }}>
            Continue
          </Link>
        )}
      </div>
    </section>
  )
}
