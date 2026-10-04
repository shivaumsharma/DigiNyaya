import { useEffect, useState } from 'react'
import { useLanguage } from '../../i18n/LanguageContext.jsx'
import NavLinks from './NavLinks.jsx'

// Slides in once the hero has scrolled out of view.
export default function StickyNav({ onFile }) {
  const { t } = useLanguage()
  const [show, setShow] = useState(false)

  useEffect(() => {
    const hero = document.querySelector('[data-hero]')
    if (!hero || typeof IntersectionObserver === 'undefined') return undefined
    const io = new IntersectionObserver(([e]) => setShow(!e.isIntersecting), { rootMargin: '-60px 0px 0px 0px' })
    io.observe(hero)
    return () => io.disconnect()
  }, [])

  return (
    <div className={`dn-sticky ${show ? 'show' : ''}`}>
      <div className="dn-logo">
        Digi<em>Nyaya</em>
      </div>
      <NavLinks />
      <button type="button" className="dn-btn sm" onClick={onFile}>
        {t('landing.fileDispute')}
      </button>
    </div>
  )
}
