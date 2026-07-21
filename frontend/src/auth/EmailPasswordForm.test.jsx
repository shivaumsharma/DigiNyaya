import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import EmailPasswordForm from './EmailPasswordForm.jsx'

const LANGUAGES = [
  { code: 'en-IN', label: 'English', native: 'English' },
  { code: 'hi-IN', label: 'Hindi', native: 'हिन्दी' },
]

describe('EmailPasswordForm', () => {
  it('login mode submits only email + password, no name/language fields shown', async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn().mockResolvedValue(undefined)
    render(<EmailPasswordForm mode="login" languages={LANGUAGES} onSubmit={onSubmit} />)

    expect(screen.queryByPlaceholderText(/your full name/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/preferred language/i)).not.toBeInTheDocument()

    await user.type(screen.getByPlaceholderText(/you@example.com/i), 'user@example.com')
    await user.type(screen.getByPlaceholderText(/your password/i), 'hunter22')
    await user.click(screen.getByRole('button', { name: /log in/i }))

    expect(onSubmit).toHaveBeenCalledWith({ email: 'user@example.com', password: 'hunter22' })
  })

  it('signup mode includes full_name and preferred_language, defaulting to the first language', async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn().mockResolvedValue(undefined)
    render(<EmailPasswordForm mode="signup" languages={LANGUAGES} onSubmit={onSubmit} />)

    await user.type(screen.getByPlaceholderText(/your full name/i), 'Ada Lovelace')
    await user.type(screen.getByPlaceholderText(/you@example.com/i), 'ada@example.com')
    await user.type(screen.getByPlaceholderText(/at least 8 characters/i), 'analytical1')
    await user.click(screen.getByRole('button', { name: /create account/i }))

    expect(onSubmit).toHaveBeenCalledWith({
      email: 'ada@example.com',
      password: 'analytical1',
      full_name: 'Ada Lovelace',
      preferred_language: 'en-IN',
    })
  })

  it('shows the rejected promise message and re-enables the button on failure', async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn().mockRejectedValue(new Error('Invalid credentials'))
    render(<EmailPasswordForm mode="login" languages={LANGUAGES} onSubmit={onSubmit} />)

    await user.type(screen.getByPlaceholderText(/you@example.com/i), 'user@example.com')
    await user.type(screen.getByPlaceholderText(/your password/i), 'wrongpass')
    await user.click(screen.getByRole('button', { name: /log in/i }))

    expect(await screen.findByText('Invalid credentials')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /log in/i })).not.toBeDisabled()
  })

  it('calls onForgotPassword when the link is clicked, only in login mode', async () => {
    const user = userEvent.setup()
    const onForgotPassword = vi.fn()
    render(
      <EmailPasswordForm
        mode="login"
        languages={LANGUAGES}
        onSubmit={vi.fn()}
        onForgotPassword={onForgotPassword}
      />,
    )
    await user.click(screen.getByText(/forgot password/i))
    expect(onForgotPassword).toHaveBeenCalledTimes(1)
  })
})
