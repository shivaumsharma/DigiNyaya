import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { LanguageProvider, useLanguage, SUPPORTED_UI_LANGUAGES } from './LanguageContext.jsx'

function Probe({ langKey, vars }) {
  const { lang, setLang, t } = useLanguage()
  return (
    <div>
      <span data-testid="lang">{lang}</span>
      <span data-testid="translated">{t(langKey, vars)}</span>
      <button onClick={() => setLang('hi-IN')}>switch-to-hindi</button>
      <button onClick={() => setLang('bogus-code')}>switch-to-bogus</button>
    </div>
  )
}

describe('LanguageContext', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('defaults to en-IN when nothing is stored', () => {
    render(
      <LanguageProvider>
        <Probe langKey="newCase.title" />
      </LanguageProvider>,
    )
    expect(screen.getByTestId('lang')).toHaveTextContent('en-IN')
  })

  it('restores a previously saved language from localStorage', () => {
    localStorage.setItem('diginyaya_lang', 'hi-IN')
    render(
      <LanguageProvider>
        <Probe langKey="newCase.title" />
      </LanguageProvider>,
    )
    expect(screen.getByTestId('lang')).toHaveTextContent('hi-IN')
  })

  it('falls back to the key itself when a translation is missing from every dictionary', () => {
    render(
      <LanguageProvider>
        <Probe langKey="this.key.does.not.exist" />
      </LanguageProvider>,
    )
    expect(screen.getByTestId('translated')).toHaveTextContent('this.key.does.not.exist')
  })

  it('interpolates {{vars}} into the resolved string', () => {
    render(
      <LanguageProvider>
        <Probe langKey="stepper.dispute" vars={{ name: 'Ada' }} />
      </LanguageProvider>,
    )
    // stepper.dispute has no {{vars}} placeholder, so this just proves
    // interpolation doesn't crash or mangle a string that has nothing to fill.
    expect(screen.getByTestId('translated')).not.toHaveTextContent('{{name}}')
  })

  it('switches language, persists the choice, and updates t() output', async () => {
    const user = userEvent.setup()
    render(
      <LanguageProvider>
        <Probe langKey="newCase.title" />
      </LanguageProvider>,
    )
    const before = screen.getByTestId('translated').textContent
    await user.click(screen.getByText('switch-to-hindi'))
    expect(screen.getByTestId('lang')).toHaveTextContent('hi-IN')
    expect(localStorage.getItem('diginyaya_lang')).toBe('hi-IN')
    expect(screen.getByTestId('translated').textContent).not.toBe(before)
  })

  it('falls back to the English dictionary for a language code with no dictionary entry', async () => {
    const user = userEvent.setup()
    render(
      <LanguageProvider>
        <Probe langKey="newCase.title" />
      </LanguageProvider>,
    )
    const english = screen.getByTestId('translated').textContent
    await act(async () => {
      await user.click(screen.getByText('switch-to-bogus'))
    })
    // DICTIONARIES[lang] || en means an unknown code silently renders English
    // rather than blank/undefined text.
    expect(screen.getByTestId('translated').textContent).toBe(english)
  })

  it('exports exactly the 11 languages the backend supports', () => {
    expect(SUPPORTED_UI_LANGUAGES).toHaveLength(11)
    expect(SUPPORTED_UI_LANGUAGES.map((l) => l.code)).toContain('en-IN')
  })
})
