import { useState } from 'react'
import { ArrowRight } from '../icons.jsx'

// Two-step phone+OTP flow shared by signup, login and account-linking.
// `needsProfile` shows the full_name/preferred_language fields required
// only on signup. `onStart(phone)` and `onVerify(phone, otp, extra)` are
// injected so this component doesn't know which of the six
// signup/login/link endpoints it's driving.
export default function PhoneOtpForm({ needsProfile, languages, onStart, onVerify }) {
  const [step, setStep] = useState('phone') // 'phone' | 'otp'
  const [phone, setPhone] = useState('')
  const [otp, setOtp] = useState('')
  const [fullName, setFullName] = useState('')
  const [language, setLanguage] = useState(languages?.[0]?.code || 'en-IN')
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')
  const [info, setInfo] = useState('')
  const [devOtp, setDevOtp] = useState('')

  async function handleStart(e) {
    e.preventDefault()
    setErr('')
    setLoading(true)
    try {
      const res = await onStart(phone)
      setInfo('OTP sent. Check your phone.')
      // No real SMS provider is wired up yet (see backend/app/auth/sms.py's
      // console stub) -- the backend includes the code directly in dev so
      // testing doesn't require reading server logs. Never present outside
      // DIGINYAYA_ENV=production.
      setDevOtp(res?.dev_otp || '')
      setStep('otp')
    } catch (ex) {
      setErr(ex.message)
    } finally {
      setLoading(false)
    }
  }

  async function handleVerify(e) {
    e.preventDefault()
    setErr('')
    setLoading(true)
    try {
      const extra = needsProfile ? { full_name: fullName, preferred_language: language } : undefined
      await onVerify(phone, otp, extra)
    } catch (ex) {
      setErr(ex.message)
    } finally {
      setLoading(false)
    }
  }

  if (step === 'phone') {
    return (
      <form onSubmit={handleStart}>
        <div className="field">
          <label>Phone number</label>
          <input
            className="input"
            type="tel"
            value={phone}
            onChange={(e) => setPhone(e.target.value)}
            placeholder="+91 98765 43210"
            required
          />
        </div>
        {err && <p style={{ color: 'var(--red)', fontSize: '0.85rem', marginBottom: 12 }}>{err}</p>}
        <button className="btn btn-primary btn-block btn-lg" disabled={loading || !phone}>
          {loading ? 'Sending…' : 'Send OTP'} <ArrowRight />
        </button>
      </form>
    )
  }

  return (
    <form onSubmit={handleVerify}>
      {info && <p style={{ color: 'var(--green)', fontSize: '0.85rem', marginBottom: 12 }}>{info}</p>}
      {devOtp && (
        <p
          className="tag tag-outline"
          style={{ display: 'block', marginBottom: 12, textTransform: 'none', letterSpacing: 0 }}
        >
          Dev mode — no SMS provider configured. Your code is <strong>{devOtp}</strong>.
        </p>
      )}
      {needsProfile && (
        <>
          <div className="field">
            <label>Full name</label>
            <input
              className="input"
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              placeholder="Your full name"
              required
            />
          </div>
          {languages?.length > 0 && (
            <div className="field">
              <label>Preferred language</label>
              <select className="select" value={language} onChange={(e) => setLanguage(e.target.value)}>
                {languages.map((l) => (
                  <option key={l.code} value={l.code}>
                    {l.native} ({l.label})
                  </option>
                ))}
              </select>
            </div>
          )}
        </>
      )}
      <div className="field">
        <label>Enter the 6-digit code</label>
        <input
          className="input"
          value={otp}
          onChange={(e) => setOtp(e.target.value.replace(/\D/g, '').slice(0, 6))}
          placeholder="123456"
          maxLength={6}
          required
        />
      </div>
      {err && <p style={{ color: 'var(--red)', fontSize: '0.85rem', marginBottom: 12 }}>{err}</p>}
      <button className="btn btn-primary btn-block btn-lg" disabled={loading || otp.length !== 6}>
        {loading ? 'Verifying…' : 'Verify & continue'} <ArrowRight />
      </button>
      <button
        type="button"
        className="btn btn-ghost btn-block"
        style={{ marginTop: 10 }}
        onClick={() => {
          setStep('phone')
          setOtp('')
          setErr('')
        }}
      >
        Use a different number
      </button>
    </form>
  )
}
