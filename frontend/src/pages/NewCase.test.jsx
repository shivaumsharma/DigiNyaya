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
    submitCase: vi.fn(),
    uploadDocuments: vi.fn(),
    listDocuments: vi.fn(),
    preliminaryReview: vi.fn(),
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

async function fillDetailsAndContinue(user) {
  await user.type(screen.getByLabelText(/opposing party/i), 'Acme Corp')
  await user.type(screen.getByLabelText(/claim amount/i), '15000')
  await user.type(screen.getByLabelText(/describe your dispute/i), 'They never delivered the goods.')
  await user.click(screen.getByRole('button', { name: /continue to evidence/i }))
}

describe('NewCase', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('pre-fills the claimant name from the logged-in user', () => {
    renderNewCase()
    expect(screen.getByDisplayValue('Ada Lovelace')).toBeInTheDocument()
  })

  it('loads a sample claim and populates the form + a read-only evidence preview', async () => {
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

  it('creates a draft case with a parsed numeric amount and advances to the evidence step', async () => {
    const user = userEvent.setup()
    api.createCase.mockResolvedValue({ case_id: 'case-42', status: 'draft' })
    renderNewCase()

    await fillDetailsAndContinue(user)

    await waitFor(() => expect(api.createCase).toHaveBeenCalled())
    const payload = api.createCase.mock.calls[0][0]
    expect(payload).toMatchObject({
      claimant_name: 'Ada Lovelace',
      respondent_name: 'Acme Corp',
      dispute_type: 'money_recovery',
      claim_amount: 15000,
    })
    expect(await screen.findByText(/add your evidence/i)).toBeInTheDocument()
  })

  it('submits a claim amount of "0" as 0, not NaN (parseFloat(...) || 0 fallback)', async () => {
    const user = userEvent.setup()
    api.createCase.mockResolvedValue({ case_id: 'case-1', status: 'draft' })
    renderNewCase()

    await user.type(screen.getByLabelText(/opposing party/i), 'Acme Corp')
    await user.type(screen.getByLabelText(/claim amount/i), '0')
    await user.type(screen.getByLabelText(/describe your dispute/i), 'Some description text.')
    await user.click(screen.getByRole('button', { name: /continue to evidence/i }))

    await waitFor(() => expect(api.createCase).toHaveBeenCalled())
    expect(api.createCase.mock.calls[0][0].claim_amount).toBe(0)
  })

  it('shows the rejected error message and stays on the details step on failure', async () => {
    const user = userEvent.setup()
    api.createCase.mockRejectedValue(new Error('Server exploded'))
    renderNewCase()

    await fillDetailsAndContinue(user)

    expect(await screen.findByText('Server exploded')).toBeInTheDocument()
    expect(navigateSpy).not.toHaveBeenCalled()
  })

  it('runs the preliminary review after evidence, then files the claim and navigates to the respond page', async () => {
    const user = userEvent.setup()
    api.createCase.mockResolvedValue({ case_id: 'case-42', status: 'draft' })
    api.preliminaryReview.mockResolvedValue({
      documents: [
        { document_id: 'DOC-1', filename: 'resume.pdf', relevant: false, looks_like: 'a resume', note: 'This does not look like proof of a loan.' },
      ],
      case_strength_note: "What you've uploaded doesn't look like it supports this claim.",
    })
    api.submitCase.mockResolvedValue({ case_id: 'case-42', status: 'awaiting_response' })
    renderNewCase()

    await fillDetailsAndContinue(user)
    await screen.findByText(/add your evidence/i)

    await user.click(screen.getByRole('button', { name: /continue to review/i }))

    await waitFor(() => expect(api.preliminaryReview).toHaveBeenCalledWith('case-42'))
    expect(await screen.findByText(/doesn't look like it supports this claim/i)).toBeInTheDocument()
    expect(screen.getByText('resume.pdf')).toBeInTheDocument()
    expect(screen.getByText('a resume')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /file claim/i }))

    await waitFor(() => expect(api.submitCase).toHaveBeenCalledWith('case-42'))
    await waitFor(() => expect(navigateSpy).toHaveBeenCalledWith('/case/case-42/respond'))
  })
})
