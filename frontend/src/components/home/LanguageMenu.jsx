import { useEffect, useRef, useState } from 'react'
import { SUPPORTED_UI_LANGUAGES, useLanguage } from '../../i18n/LanguageContext.jsx'
import { Chevron, GlobeIcon } from './shared.jsx'

export default function LanguageMenu() {
  const { lang, setLang, t } = useLanguage()
  const [open, setOpen] = useState(false)
  const wrapRef = useRef(null)
  const current = SUPPORTED_UI_LANGUAGES.find((l) => l.code === lang) || SUPPORTED_UI_LANGUAGES[0]

  useEffect(() => {
    if (!open) return undefined
    const onDown = (e) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false)
    }
    const onKey = (e) => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  return (
    <div className="dn-lang" ref={wrapRef}>
      <button
        type="button"
        className="dn-lang-btn"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={t('landing.languageLabel')}
        onClick={() => setOpen((o) => !o)}
      >
        <GlobeIcon />
        {current.label}
        <Chevron dir="down" size={12} />
      </button>
      {open && (
        <div className="dn-lang-menu" role="menu">
          {SUPPORTED_UI_LANGUAGES.map((l) => (
            <button
              key={l.code}
              type="button"
              role="menuitemradio"
              aria-checked={l.code === lang}
              lang={l.code}
              onClick={() => {
                setLang(l.code)
                setOpen(false)
              }}
            >
              {l.label}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
