import { useLanguage } from '../../i18n/LanguageContext.jsx'
import { Reveal } from './shared.jsx'

export function Close({ onFile }) {
  const { t } = useLanguage()
  return (
    <section id="close" className="dn-sec dn-close">
      <Reveal as="h2">{t('landing.ctaTitle')}</Reveal>
      <p>{t('landing.ctaLede')}</p>
      <button type="button" className="dn-btn lg" onClick={onFile}>
        {t('landing.ctaFile')}
      </button>
    </section>
  )
}

export function HomeFooter() {
  const { t } = useLanguage()
  return (
    <footer className="dn-footer">
      <span>{t('landing.footerAddr')}</span>
      <span>{t('landing.footerGrievance')}</span>
      <span>{t('landing.footerCopyright')}</span>
    </footer>
  )
}
