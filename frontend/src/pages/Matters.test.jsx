import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import Matters from './Matters.jsx'
import { groupMatters } from './matterGroups.js'
import MatterGuide from './MatterGuide.jsx'
import { LanguageProvider } from '../i18n/LanguageContext.jsx'
import { api, ApiError } from '../api'

vi.mock('../api', () => {
  class ApiError extends Error {
    constructor(message, status) {
      super(message)
      this.status = status
    }
  }
  return { ApiError, api: { matters: vi.fn(), matterGuide: vi.fn() } }
})

const MATTERS = [
  { id: 'consumer_dispute', label: 'Consumer Dispute', family: 'civil', level: 3, level_name: 'L3_DECIDE', status: 'live', description: 'Defective products.', can_resolve: true },
  { id: 'money_recovery', label: 'Money Recovery', family: 'civil', level: 2, level_name: 'L2_RESOLVE', status: 'live', description: 'Recovery of lent money.', can_resolve: true },
  { id: 'tenancy_dispute', label: 'Tenancy Dispute', family: 'civil', level: 2, level_name: 'L2_RESOLVE', status: 'preview', description: 'Unpaid rent.', can_resolve: true, has_guide: true },
  { id: 'criminal_complaint', label: 'Criminal complaint or FIR', family: 'criminal', level: 0, level_name: 'L0_NAVIGATE', status: 'navigator', description: 'How a complaint starts.', can_resolve: false },
  { id: 'tax_dispute', label: 'Tax notices', family: 'tax', level: 0, level_name: 'L0_NAVIGATE', status: 'navigator', description: 'Responding to a notice.', can_resolve: false },
]

const GUIDE = {
  id: 'criminal_complaint', label: 'Criminal complaint or FIR', level_name: 'L0_NAVIGATE', summary: 'How a complaint starts.',
  forum: 'Usually the police or a Magistrate.', steps: ['Write down what happened', 'Keep evidence safe'],
  documents: ['Government ID'], this_tool_cannot: ['Predict the outcome'], disclaimer: 'General information only, not legal advice.', reviewed: false,
}

function renderAt(path) {
  return render(
    <LanguageProvider>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/matters" element={<Matters />} />
          <Route path="/matters/:id" element={<MatterGuide />} />
        </Routes>
      </MemoryRouter>
    </LanguageProvider>,
  )
}

beforeEach(() => {
  try { localStorage.removeItem('diginyaya_lang') } catch { /* ignore */ }
  api.matters.mockReset()
  api.matterGuide.mockReset()
})

describe('groupMatters', () => {
  it('separates fileable, roadmap and guide matters and orders guide families', () => {
    const g = groupMatters(MATTERS)
    expect(g.resolve.map((m) => m.id)).toEqual(['consumer_dispute', 'money_recovery'])
    expect(g.soon.map((m) => m.id)).toEqual(['tenancy_dispute'])
    expect(g.guides.map((x) => x.family)).toEqual(['criminal', 'tax'])
  })

  it('filters by label or description, case-insensitively', () => {
    expect(groupMatters(MATTERS, 'FIR').guides[0].items[0].id).toBe('criminal_complaint')
    expect(groupMatters(MATTERS, 'lent money').resolve.map((m) => m.id)).toEqual(['money_recovery'])
    expect(groupMatters(MATTERS, 'zzz')).toEqual({ resolve: [], soon: [], guides: [] })
  })

  it('never offers a pipeline filing for a navigator matter', () => {
    const g = groupMatters(MATTERS)
    expect(g.resolve.some((m) => m.status === 'navigator')).toBe(false)
  })
})

describe('Matters page', () => {
  it('shows file buttons only for fileable matters and guide links for navigator matters', async () => {
    api.matters.mockResolvedValue(MATTERS)
    renderAt('/matters')
    expect(await screen.findByText('Money Recovery')).toBeInTheDocument()
    const fileLinks = screen.getAllByRole('link', { name: 'File a case' })
    expect(fileLinks.map((a) => a.getAttribute('href'))).toEqual(['/file/consumer_dispute', '/file/money_recovery'])
    expect(screen.getAllByRole('link', { name: 'Read the guide' }).map((a) => a.getAttribute('href'))).toEqual(['/matters/tenancy_dispute', '/matters/criminal_complaint', '/matters/tax_dispute'])
    expect(screen.getByText('Roadmap')).toBeInTheDocument()
  })

  it('links a roadmap matter to its guide but never offers to file it', async () => {
    api.matters.mockResolvedValue(MATTERS)
    renderAt('/matters')
    await screen.findByText('Tenancy Dispute')
    const hrefs = screen.getAllByRole('link', { name: 'Read the guide' }).map((a) => a.getAttribute('href'))
    expect(hrefs).toContain('/matters/tenancy_dispute')
    expect(screen.getAllByRole('link', { name: 'File a case' }).map((a) => a.getAttribute('href'))).not.toContain('/file/tenancy_dispute')
  })

  it('filters with the search box and shows an empty message', async () => {
    api.matters.mockResolvedValue(MATTERS)
    renderAt('/matters')
    await screen.findByText('Money Recovery')
    await userEvent.type(screen.getByRole('searchbox'), 'zzz')
    expect(screen.getByText('No matters match your search.')).toBeInTheDocument()
  })

  it('shows an error message when the list cannot load', async () => {
    api.matters.mockRejectedValue(new Error('down'))
    renderAt('/matters')
    expect(await screen.findByRole('alert')).toHaveTextContent("couldn't load")
  })
})

describe('MatterGuide page', () => {
  it('renders the guide with the unreviewed banner, disclaimer and the limits of the tool', async () => {
    api.matterGuide.mockResolvedValue(GUIDE)
    renderAt('/matters/criminal_complaint')
    expect(await screen.findByRole('heading', { name: 'Criminal complaint or FIR' })).toBeInTheDocument()
    expect(screen.getByRole('note')).toHaveTextContent('not been reviewed by a lawyer')
    expect(screen.getByRole('note')).toHaveTextContent('not legal advice')
    expect(screen.getByText('Write down what happened')).toBeInTheDocument()
    expect(screen.getByText('Predict the outcome')).toBeInTheDocument()
    expect(api.matterGuide).toHaveBeenCalledWith('criminal_complaint', 'en-IN')
  })

  it('shows not-found for an unknown guide and a generic error otherwise', async () => {
    api.matterGuide.mockRejectedValue(new ApiError('nope', 404))
    renderAt('/matters/unknown')
    expect(await screen.findByRole('alert')).toHaveTextContent("couldn't find")
  })

  it('shows a load error for a server failure', async () => {
    api.matterGuide.mockRejectedValue(new ApiError('boom', 500))
    renderAt('/matters/tax_dispute')
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent("couldn't load"))
  })
})

describe('language', () => {
  const setLang = (code) => localStorage.setItem('diginyaya_lang', code)

  it('requests the list and the guide in the chosen UI language', async () => {
    setLang('hi-IN')
    api.matters.mockResolvedValue(MATTERS)
    renderAt('/matters')
    await screen.findByText('Money Recovery')
    expect(api.matters).toHaveBeenCalledWith('hi-IN')
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('हर तरह का कानूनी मामला')
  })

  it('labels a machine translation as unchecked', async () => {
    setLang('ta-IN')
    api.matterGuide.mockResolvedValue({ ...GUIDE, language: 'ta', translation_reviewed: false })
    renderAt('/matters/criminal_complaint')
    await screen.findByRole('heading', { name: GUIDE.label })
    expect(api.matterGuide).toHaveBeenCalledWith('criminal_complaint', 'ta-IN')
    expect(screen.getByText(/தானாக உருவாக்கப்பட்டது/)).toBeInTheDocument()
  })

  it('says so when the guide falls back to English', async () => {
    setLang('hi-IN')
    api.matterGuide.mockResolvedValue({ ...GUIDE, language: 'en' })
    renderAt('/matters/criminal_complaint')
    await screen.findByRole('heading', { name: GUIDE.label })
    expect(screen.getByText(/अंग्रेज़ी में दिखाई गई है/)).toBeInTheDocument()
  })

  it('shows no language notice for English', async () => {
    api.matterGuide.mockResolvedValue({ ...GUIDE, language: 'en' })
    renderAt('/matters/criminal_complaint')
    await screen.findByRole('heading', { name: GUIDE.label })
    expect(screen.getAllByRole('note')).toHaveLength(1)
  })
})
