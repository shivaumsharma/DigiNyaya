import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import PhoneOtpForm from './PhoneOtpForm.jsx'

const LANGUAGES = [{ code: 'en-IN', label: 'English', native: 'English' }]

describe('PhoneOtpForm', () => {
  it('sends the phone number, advances to the OTP step, and shows the dev OTP', async () => {
    const user = userEvent.setup()
    const onStart = vi.fn().mockResolvedValue({ dev_otp: '482913' })
    const onVerify = vi.fn().mockResolvedValue(undefined)
    render(<PhoneOtpForm onStart={onStart} onVerify={onVerify} />)

    await user.type(screen.getByLabelText(/phone number/i), '+919876543210')
    await user.click(screen.getByRole('button', { name: /send otp/i }))

    expect(onStart).toHaveBeenCalledWith('+919876543210')
    expect(await screen.findByText(/482913/)).toBeInTheDocument()
    expect(screen.getByText(/otp sent/i)).toBeInTheDocument()
  })

  it('strips non-digit characters from the OTP field and caps it at 6 digits', async () => {
    const user = userEvent.setup()
    const onStart = vi.fn().mockResolvedValue({})
    render(<PhoneOtpForm onStart={onStart} onVerify={vi.fn()} />)

    await user.type(screen.getByLabelText(/phone number/i), '9876543210')
    await user.click(screen.getByRole('button', { name: /send otp/i }))

    const otpInput = await screen.findByLabelText(/enter the 6-digit code/i)
    await user.type(otpInput, 'ab12cd34ef56')
    expect(otpInput).toHaveValue('123456')
  })

  it('verify button stays disabled until exactly 6 digits are entered', async () => {
    const user = userEvent.setup()
    const onStart = vi.fn().mockResolvedValue({})
    render(<PhoneOtpForm onStart={onStart} onVerify={vi.fn()} />)

    await user.type(screen.getByLabelText(/phone number/i), '9876543210')
    await user.click(screen.getByRole('button', { name: /send otp/i }))

    const otpInput = await screen.findByLabelText(/enter the 6-digit code/i)
    const verifyBtn = screen.getByRole('button', { name: /verify & continue/i })
    expect(verifyBtn).toBeDisabled()

    await user.type(otpInput, '1234')
    expect(verifyBtn).toBeDisabled()

    await user.type(otpInput, '56')
    expect(verifyBtn).not.toBeDisabled()
  })

  it('verifies with phone + otp, and includes profile fields only when needsProfile is set', async () => {
    const user = userEvent.setup()
    const onStart = vi.fn().mockResolvedValue({})
    const onVerify = vi.fn().mockResolvedValue(undefined)
    render(<PhoneOtpForm needsProfile languages={LANGUAGES} onStart={onStart} onVerify={onVerify} />)

    await user.type(screen.getByLabelText(/phone number/i), '9876543210')
    await user.click(screen.getByRole('button', { name: /send otp/i }))

    await user.type(await screen.findByLabelText(/full name/i), 'Ada Lovelace')
    await user.type(screen.getByLabelText(/enter the 6-digit code/i), '123456')
    await user.click(screen.getByRole('button', { name: /verify & continue/i }))

    expect(onVerify).toHaveBeenCalledWith('9876543210', '123456', {
      full_name: 'Ada Lovelace',
      preferred_language: 'en-IN',
    })
  })

  it('"use a different number" returns to the phone step and clears the OTP', async () => {
    const user = userEvent.setup()
    const onStart = vi.fn().mockResolvedValue({})
    render(<PhoneOtpForm onStart={onStart} onVerify={vi.fn()} />)

    await user.type(screen.getByLabelText(/phone number/i), '9876543210')
    await user.click(screen.getByRole('button', { name: /send otp/i }))
    await screen.findByLabelText(/enter the 6-digit code/i)

    await user.click(screen.getByRole('button', { name: /use a different number/i }))

    expect(screen.getByLabelText(/phone number/i)).toBeInTheDocument()
    expect(screen.queryByLabelText(/enter the 6-digit code/i)).not.toBeInTheDocument()
  })

  it('surfaces the rejected error message when starting OTP fails', async () => {
    const user = userEvent.setup()
    const onStart = vi.fn().mockRejectedValue(new Error('Too many requests'))
    render(<PhoneOtpForm onStart={onStart} onVerify={vi.fn()} />)

    await user.type(screen.getByLabelText(/phone number/i), '9876543210')
    await user.click(screen.getByRole('button', { name: /send otp/i }))

    expect(await screen.findByText('Too many requests')).toBeInTheDocument()
  })
})
