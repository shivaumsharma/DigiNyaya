import { useState } from 'react'
import { Eye, EyeOff } from '../icons.jsx'

// Shared by EmailPasswordForm and ResetPassword -- a password field with a
// show/hide toggle, since a bare type="password" input gives the user no way
// to check what they typed before submitting.
export default function PasswordInput({ value, onChange, placeholder, minLength, autoComplete }) {
  const [visible, setVisible] = useState(false)

  return (
    <div className="password-field">
      <input
        className="input"
        type={visible ? 'text' : 'password'}
        value={value}
        onChange={onChange}
        placeholder={placeholder}
        minLength={minLength}
        autoComplete={autoComplete}
        required
      />
      <button
        type="button"
        className="password-toggle"
        onClick={() => setVisible((v) => !v)}
        aria-label={visible ? 'Hide password' : 'Show password'}
        tabIndex={-1}
      >
        {visible ? <EyeOff width={17} height={17} /> : <Eye width={17} height={17} />}
      </button>
    </div>
  )
}
