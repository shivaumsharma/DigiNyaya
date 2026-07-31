import { useState } from 'react'
import PasswordInput from './PasswordInput.jsx'

// Shared by signup (needs full_name + preferred_language) and login (just
// email + password). `languages` is [{code, label, native}] from
// GET /api/languages -- passed in so this component doesn't hardcode a copy
// of the 11 Sarvam-supported codes that could drift from the backend.
export default function EmailPasswordForm({ mode, languages, onSubmit, onForgotPassword }) {
  const isSignup = mode === 'signup'
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [fullName, setFullName] = useState('')
  const [language, setLanguage] = useState(languages?.[0]?.code || 'en-IN')
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')

  async function handleSubmit(e) {
    e.preventDefault()
    setErr('')
    setLoading(true)
    try {
      const payload = isSignup
        ? { email, password, full_name: fullName, preferred_language: language }
        : { email, password }
      await onSubmit(payload)
    } catch (ex) {
      setErr(ex.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <form onSubmit={handleSubmit}>
      {isSignup && (
        <div className="field">
          <label htmlFor="ep-full-name">Full name</label>
          <input
            id="ep-full-name"
            className="input"
            value={fullName}
            onChange={(e) => setFullName(e.target.value)}
            placeholder="Your full name"
            required
          />
        </div>
      )}
      <div className="field">
        <label htmlFor="ep-email">Email</label>
        <input
          id="ep-email"
          className="input"
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="you@example.com"
          required
        />
      </div>
      <div className="field">
        <label htmlFor="ep-password">Password</label>
        <PasswordInput
          id="ep-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder={isSignup ? 'At least 8 characters' : 'Your password'}
          minLength={8}
          autoComplete={isSignup ? 'new-password' : 'current-password'}
        />
      </div>
      {isSignup && languages?.length > 0 && (
        <div className="field">
          <label htmlFor="ep-language">Preferred language</label>
          <select id="ep-language" className="select" value={language} onChange={(e) => setLanguage(e.target.value)}>
            {languages.map((l) => (
              <option key={l.code} value={l.code}>
                {l.native} ({l.label})
              </option>
            ))}
          </select>
        </div>
      )}
      {err && <p style={{ color: 'var(--red)', fontSize: '0.85rem', marginBottom: 12 }}>{err}</p>}
      <button className="btn btn-primary btn-block btn-lg" disabled={loading}>
        {loading ? 'Please wait…' : isSignup ? 'Create account' : 'Log in'}
      </button>
      {!isSignup && onForgotPassword && (
        <button
          type="button"
          className="btn btn-ghost btn-block"
          style={{ marginTop: 10 }}
          onClick={onForgotPassword}
        >
          Forgot password?
        </button>
      )}
    </form>
  )
}
