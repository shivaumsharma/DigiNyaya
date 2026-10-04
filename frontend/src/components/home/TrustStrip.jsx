import { useLanguage } from '../../i18n/LanguageContext.jsx'

export default function TrustStrip() {
  const { t } = useLanguage()
  const items = t('landing.trust')
  return (
    <div className="dn-trust">
      <div className="dn-trust-track">
        {[0, 1].map((copy) =>
          items.map((x, i) => (
            <div key={`${copy}-${i}`} className="dn-trust-item" aria-hidden={copy === 1 ? true : undefined}>
              <span className="dn-diamond" />
              {x}
            </div>
          )),
        )}
      </div>
    </div>
  )
}
