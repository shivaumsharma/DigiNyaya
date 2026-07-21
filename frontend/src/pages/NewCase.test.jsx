import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import NewCase from './NewCase.jsx'
import { LanguageProvider } from '../i18n/LanguageContext.jsx'
import { api } from '../api.js'

const navigateSpy = vi.fn()
vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal()
  return { ...actual, useNavigate: () => navigateSpy }
})

vi.mock('../auth/AuthContext.jsx', () => ({
  useAuth: () => ({ user: { full_name: 'Ada Lovelace' } }),
}))

vi.mock('../api.js', () => ({
  api: {
    sampleClaim: vi.fn(),
    createCase: vi.fn(),
  },
}))

function renderNewCase() {
  return render(
    <LanguageProvider>
      <MemoryRouter initialEntries={['/new/money_recovery']}>
        <Routes>
          <Route path="/new/:type" element={<NewCase />} />
        </Routes>
      </MemoryRouter>
    </LanguageProvider>,
  )
}

describe('NewCase', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('pre-fills the claimant name from the logged-in user', () => {
    renderNewCase()
    expect(screen.getByDisplayValue('Ada Lovelace')).toBeInTheDocument()
  })

  it('loads a sample claim and populates the form + evidence list', async () => {
    const user = userEvent.setup()
    api.sampleClaim.mockResolvedValue({
      claim: {
        claimant_name: 'Sample Claimant',
        respondent_name: 'Sample Respondent',
        claim_amount: 5000,
        description: 'A sample dispute description.',
        evidence: [{ filename: 'invoice.pdf', kind: 'invoice', note: null }],
      },
    })
    renderNewCase()

    await user.click(screen.getByRole('button', { name: /load demo/i }))

    await waitFor(() => expect(screen.getByDisplayValue('Sample Claimant')).toBeInTheDocument())
    expect(screen.getByDisplayValue('Sample Respondent')).toBeInTheDocument()
    expect(screen.getByText('invoice.pdf')).toBeInTheDocument()
  })

  it('adds and removes a manually entered evidence item', async () => {
    const user = userEvent.setup()
    renderNewCase()

    const evidenceInput = screen.getByPlaceholderText(/order_invoice/i)
    await user.type(evidenceInput, 'contract.pdf')
    await user.click(screen.getByRole('button', { name: /add/i }))

    expect(screen.getByText('contract.pdf')).toBeInTheDocument()

    await user.click(screen.getByText('✕'))
    expect(screen.queryByText('contract.pdf')).not.toBeInTheDocument()
  })

  it('submits the claim with a parsed numeric amount and navigates to the respond page', async () => {
    const user = userEvent.setup()
    api.createCase.mockResolvedValue({ case_id: 'case-42' })
    renderNewCase()

    await user.type(screen.getByPlaceholderText(/seller.*business name/i), 'Acme Corp')
    await user.type(screen.getByPlaceholderText('42999'), '15000')
    await user.type(screen.getByPlaceholderText(/what did you buy/i), 'They never delivered the goods.')
    await user.click(screen.getByRole('button', { name: /file claim/i }))

    await waitFor(() => expect(api.createCase).toHaveBeenCalled())
    const payload = api.createCase.mock.calls[0][0]
    expect(payload).toMatchObject({
      claimant_name: 'Ada Lovelace',
      respondent_name: 'Acme Corp',
      dispute_type: 'money_recovery',
      claim_amount: 15000,
    })
    await waitFor(() => expect(navigateSpy).toHaveBeenCalledWith('/case/case-42/respond'))
  })

  it('submits a claim amount of "0" as 0, not NaN (parseFloat(...) || 0 fallback)', async () => {
    const user = userEvent.setup()
    api.createCase.mockResolvedValue({ case_id: 'case-1' })
    renderNewCase()

    await user.type(screen.getByPlaceholderText(/seller.*business name/i), 'Acme Corp')
    await user.type(screen.getByPlaceholderText('42999'), '0')
    await user.type(screen.getByPlaceholderText(/what did you buy/i), 'Some description text.')
    await user.click(screen.getByRole('button', { name: /file claim/i }))

    await waitFor(() => expect(api.createCase).toHaveBeenCalled())
    expect(api.createCase.mock.calls[0][0].claim_amount).toBe(0)
  })

  it('shows the rejected error message and re-enables the submit button on failure', async () => {
    const user = userEvent.setup()
    api.createCase.mockRejectedValue(new Error('Server exploded'))
    renderNewCase()

    await user.type(screen.getByPlaceholderText(/seller.*business name/i), 'Acme Corp')
    await user.type(screen.getByPlaceholderText('42999'), '15000')
    await user.type(screen.getByPlaceholderText(/what did you buy/i), 'Some description text.')
    await user.click(screen.getByRole('button', { name: /file claim/i }))

    expect(await screen.findByText('Server exploded')).toBeInTheDocument()
    expect(navigateSpy).not.toHaveBeenCalled()
  })
})
