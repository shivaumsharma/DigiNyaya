import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, within, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import Home from './Home.jsx'
import { LanguageProvider } from '../i18n/LanguageContext.jsx'

const navigateSpy = vi.fn()
vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal()
  return { ...actual, useNavigate: () => navigateSpy }
})

let mockUser = null
vi.mock('../auth/AuthContext.jsx', () => ({
  useAuth: () => ({ user: mockUser }),
}))

function renderHome() {
  return render(
    <LanguageProvider>
      <MemoryRouter>
        <Home />
      </MemoryRouter>
    </LanguageProvider>,
  )
}

// jsdom has no canvas; the hero degrades to no background animation.
HTMLCanvasElement.prototype.getContext = () => null

beforeEach(() => {
  localStorage.clear()
  navigateSpy.mockClear()
  mockUser = null
})

describe('Home', () => {
  it('shows the measured outcomes and the honesty disclaimer, not the old fabricated figures', () => {
    renderHome()
    expect(screen.getByText(/23,841 archived court judgments/i)).toBeInTheDocument()
    expect(screen.getByText(/no live users yet/i)).toBeInTheDocument()
    expect(screen.getByText(/Small-claims debt recovery \(weakest\)/)).toBeInTheDocument()
    expect(screen.queryByText('78%')).not.toBeInTheDocument()
    expect(screen.queryByText(/2\.3 min/i)).not.toBeInTheDocument()
  })

  it('sends a logged-out visitor to sign in when they file a dispute', async () => {
    renderHome()
    await userEvent.click(screen.getAllByRole('button', { name: /file a dispute — free to start/i })[0])
    expect(navigateSpy).toHaveBeenCalledWith('/login', { state: { from: { pathname: '/disputes' } } })
  })

  it('sends a signed-in user straight to the dispute picker', async () => {
    mockUser = { full_name: 'Ada Lovelace' }
    renderHome()
    await userEvent.click(screen.getAllByRole('button', { name: /file a dispute — free to start/i })[0])
    expect(navigateSpy).toHaveBeenCalledWith('/disputes')
  })

  it('previews which tier a described dispute routes to, and says it is only a preview', async () => {
    renderHome()
    await userEvent.type(screen.getByLabelText(/describe your dispute in one line/i), 'A client cheque bounced')
    expect(screen.getByText(/Preview: routed to Tier 2/)).toBeInTheDocument()
  })

  it('confidence gate uses the real 40% floor and escalates below it', () => {
    renderHome()
    expect(screen.getByText('Above the floor — the checks continue')).toBeInTheDocument()
    const sliders = screen.getAllByRole('slider')
    expect(sliders).toHaveLength(4)
    sliders.forEach((s) => fireEvent.change(s, { target: { value: '0' } }))
    expect(screen.getByText('Escalates to a human reviewer')).toBeInTheDocument()
    expect(screen.getByText('Escalation floor · 40%')).toBeInTheDocument()
  })

  it('switches the whole page to Hindi and sets the document language', async () => {
    renderHome()
    await userEvent.click(screen.getByRole('button', { name: 'Language' }))
    await userEvent.click(screen.getByRole('menuitemradio', { name: 'हिन्दी' }))
    expect(screen.getByText('समस्या')).toBeInTheDocument()
    expect(document.documentElement.lang).toBe('hi-IN')
  })

  it('lists only the founder, with a neutral placeholder instead of a photo', () => {
    renderHome()
    const team = document.getElementById('team')
    expect(within(team).getByText('Shivaum Sharma')).toBeInTheDocument()
    expect(within(team).queryByText('Harshita Shahi')).not.toBeInTheDocument()
    expect(within(team).queryByRole('img', { name: /photo/i })).not.toBeInTheDocument()
  })
})
