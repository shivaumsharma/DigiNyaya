import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { LanguageProvider } from '../i18n/LanguageContext.jsx'
import ListenButton from './ListenButton.jsx'

// jsdom implements neither real audio playback nor object URLs -- both are
// mocked directly rather than pulled in as a dependency, since the
// component only needs `play`/`pause`/`onended` and a revocable URL string.
class MockAudio {
  constructor(url) {
    this.url = url
    this.onended = null
    this.play = vi.fn().mockResolvedValue(undefined)
    this.pause = vi.fn()
  }
}

function renderListenButton(props = {}) {
  return render(
    <LanguageProvider>
      <ListenButton fetchAudio={vi.fn().mockResolvedValue(new Blob(['fake-audio']))} {...props} />
    </LanguageProvider>,
  )
}

describe('ListenButton', () => {
  beforeEach(() => {
    vi.stubGlobal('Audio', MockAudio)
    vi.stubGlobal('URL', { ...URL, createObjectURL: vi.fn(() => 'blob:fake-url'), revokeObjectURL: vi.fn() })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('fetches audio only on the first click, not before', () => {
    const fetchAudio = vi.fn().mockResolvedValue(new Blob(['fake-audio']))
    renderListenButton({ fetchAudio })
    expect(fetchAudio).not.toHaveBeenCalled()
  })

  it('fetches, plays, and shows the listening label on click', async () => {
    const user = userEvent.setup()
    const fetchAudio = vi.fn().mockResolvedValue(new Blob(['fake-audio']))
    renderListenButton({ fetchAudio, label: 'Listen to proposal' })

    await user.click(screen.getByRole('button', { name: /listen to proposal/i }))

    expect(fetchAudio).toHaveBeenCalledTimes(1)
    await waitFor(() => expect(screen.getByRole('button', { name: /playing/i })).toBeInTheDocument())
  })

  it('toggles to paused on a second click without re-fetching', async () => {
    const user = userEvent.setup()
    const fetchAudio = vi.fn().mockResolvedValue(new Blob(['fake-audio']))
    renderListenButton({ fetchAudio, label: 'Listen to proposal' })

    const button = screen.getByRole('button', { name: /listen to proposal/i })
    await user.click(button)
    await waitFor(() => expect(screen.getByRole('button', { name: /playing/i })).toBeInTheDocument())

    await user.click(screen.getByRole('button', { name: /playing/i }))

    expect(fetchAudio).toHaveBeenCalledTimes(1) // still just the one real Sarvam-backed call
  })

  it('resumes from paused on a third click', async () => {
    const user = userEvent.setup()
    renderListenButton({ label: 'Listen to proposal' })

    await user.click(screen.getByRole('button', { name: /listen to proposal/i }))
    await waitFor(() => screen.getByRole('button', { name: /playing/i }))
    await user.click(screen.getByRole('button', { name: /playing/i })) // -> paused

    await user.click(screen.getByRole('button')) // -> resume
    await waitFor(() => expect(screen.getByRole('button', { name: /playing/i })).toBeInTheDocument())
  })

  it('shows the unavailable message when fetching audio fails', async () => {
    const user = userEvent.setup()
    const fetchAudio = vi.fn().mockRejectedValue(new Error('Audio narration is unavailable right now'))
    renderListenButton({ fetchAudio, label: 'Listen to proposal' })

    await user.click(screen.getByRole('button', { name: /listen to proposal/i }))

    expect(await screen.findByText(/unavailable/i)).toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('revokes the object URL on unmount', async () => {
    const user = userEvent.setup()
    const { unmount } = renderListenButton({ label: 'Listen to proposal' })

    await user.click(screen.getByRole('button', { name: /listen to proposal/i }))
    await waitFor(() => screen.getByRole('button', { name: /playing/i }))

    unmount()
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:fake-url')
  })
})
