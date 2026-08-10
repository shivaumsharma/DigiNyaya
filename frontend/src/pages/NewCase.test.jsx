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
    disputeTypes: vi.fn(),
    classifyDisputeType: vi.fn(),
  },
}))

const DISPUTE_TYPES = [
  { id: 'consumer_dispute', label: 'Consumer Dispute', examples: ['Received a defective or counterfeit item'] },
  { id: 'money_recovery', label: 'Money Recovery / Loan Dispute', examples: ['Lent money to someone who is not repaying it'] },
  { id: 'contract_breach', label: 'Simple Contract Breach', examples: ["The other party didn't fulfil their side of a written agreement"] },
  { id: 'cheque_bounce', label: 'Cheque Bounce', examples: ['A cheque issued to settle a debt bounced due to insufficient funds'] },
]

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
    api.disputeTypes.mockResolvedValue(DISPUTE_TYPES)
    // Default: no suggestion. Individual tests override this when they
    // specifically want to exercise the category-mismatch banner.
    api.classifyDisputeType.mockResolvedValue(null)
  })

  it('pre-fills the claimant name from the logged-in user', () => {
    renderNewCase()
    expect(screen.getByDisplayValue('Ada Lovelace')).toBeInTheDocument()
  })

  it('shows the actual selected category in the title, not always "consumer dispute"', async () => {
    // Regression test: the title used to be a hardcoded "File your consumer
    // dispute" string regardless of which category was clicked on
    // /disputes, since :type (correctly used for the case payload) was
    // never used to drive the displayed copy. renderNewCase() renders at
    // /new/money_recovery, so this must show "Money Recovery" wording.
    renderNewCase()
    expect(await screen.findByRole('heading', { name: /money recovery/i })).toBeInTheDocument()
  })

  it('uses a category-specific example in the description placeholder', async () => {
    renderNewCase()
    await waitFor(() =>
      expect(screen.getByLabelText(/describe your dispute/i).placeholder).toMatch(/lent money/i),
    )
  })

  it('requests the sample claim for the current category, not always the default', async () => {
    const user = userEvent.setup()
    api.sampleClaim.mockResolvedValue({
      claim: {
        claimant_name: 'Rohan Verma', respondent_name: 'Karan Mehta',
        claim_amount: 150000, description: 'Loan not repaid.', evidence: [],
      },
    })
    renderNewCase()

    await user.click(screen.getByRole('button', { name: /load demo/i }))

    await waitFor(() => expect(api.sampleClaim).toHaveBeenCalledWith('money_recovery'))
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
        {
          document_id: 'DOC-1', filename: 'resume.pdf', relevant: false, looks_like: 'a resume',
          note: 'This does not look like proof of a loan.',
          authenticity_flag: true, authenticity_note: 'Date 45/25/2024 is not a valid calendar date',
        },
      ],
      case_strength_note: "What you've uploaded doesn't look like it supports this claim.",
      description_review: { detailed_enough: true, note: '' },
      winnability: { score: 20, label: 'weak', reasons: ['No relevant evidence on file'] },
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
    expect(screen.getByText(/Date 45\/25\/2024 is not a valid calendar date/)).toBeInTheDocument()

    const fileClaimButton = screen.getByRole('button', { name: /file claim/i })
    expect(fileClaimButton).toBeDisabled()

    await user.click(screen.getByRole('checkbox'))
    expect(fileClaimButton).toBeEnabled()

    await user.click(fileClaimButton)

    await waitFor(() => expect(api.submitCase).toHaveBeenCalledWith('case-42', true))
    await waitFor(() => expect(navigateSpy).toHaveBeenCalledWith('/case/case-42/respond'))
  })

  describe('category-mismatch suggestion', () => {
    const LONG_CHEQUE_DESCRIPTION =
      'Suresh Traders issued a cheque for Rs 2,50,000 to settle a debt and it bounced due to insufficient funds.'

    it('does not call the classifier for a short, still-being-typed description', async () => {
      const user = userEvent.setup()
      renderNewCase()
      await user.type(screen.getByLabelText(/describe your dispute/i), 'too short')
      await new Promise((r) => setTimeout(r, 1200))
      expect(api.classifyDisputeType).not.toHaveBeenCalled()
    })

    it('shows a suggestion banner after the debounce when the description sounds like another category', async () => {
      const user = userEvent.setup()
      api.classifyDisputeType.mockResolvedValue({
        suggested_type_id: 'cheque_bounce',
        suggested_type_label: 'Cheque Bounce',
        reason: 'Describes a bounced cheque and Section 138 notice.',
      })
      renderNewCase()

      await user.type(screen.getByLabelText(/describe your dispute/i), LONG_CHEQUE_DESCRIPTION)

      await waitFor(
        () => expect(api.classifyDisputeType).toHaveBeenCalledWith(LONG_CHEQUE_DESCRIPTION, 'money_recovery'),
        { timeout: 2000 },
      )
      expect(await screen.findByText(/cheque bounce case/i)).toBeInTheDocument()
      expect(screen.getByText(/section 138 notice/i)).toBeInTheDocument()
    })

    it('switching category navigates to the suggested type and carries the typed form over', async () => {
      const user = userEvent.setup()
      api.classifyDisputeType.mockResolvedValue({
        suggested_type_id: 'cheque_bounce',
        suggested_type_label: 'Cheque Bounce',
        reason: 'Describes a bounced cheque.',
      })
      renderNewCase()

      await user.type(screen.getByLabelText(/opposing party/i), 'Suresh Traders')
      await user.type(screen.getByLabelText(/describe your dispute/i), LONG_CHEQUE_DESCRIPTION)
      await screen.findByText(/cheque bounce case/i, {}, { timeout: 2000 })

      await user.click(screen.getByRole('button', { name: /switch to cheque bounce/i }))

      expect(navigateSpy).toHaveBeenCalledWith(
        '/file/cheque_bounce',
        { state: { carriedForm: expect.objectContaining({ respondent_name: 'Suresh Traders', description: LONG_CHEQUE_DESCRIPTION }) } },
      )
    })

    it('dismissing the suggestion hides the banner', async () => {
      const user = userEvent.setup()
      api.classifyDisputeType.mockResolvedValue({
        suggested_type_id: 'cheque_bounce',
        suggested_type_label: 'Cheque Bounce',
        reason: 'Describes a bounced cheque.',
      })
      renderNewCase()

      await user.type(screen.getByLabelText(/describe your dispute/i), LONG_CHEQUE_DESCRIPTION)
      await screen.findByText(/cheque bounce case/i, {}, { timeout: 2000 })

      await user.click(screen.getByLabelText(/dismiss suggestion/i))

      expect(screen.queryByText(/cheque bounce case/i)).not.toBeInTheDocument()
    })
  })
})
