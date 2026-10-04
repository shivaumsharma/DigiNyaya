import { useLanguage } from '../../i18n/LanguageContext.jsx'
import { ImagePlate, Reveal, ScalesIcon } from './shared.jsx'

export function Legal() {
  const { t } = useLanguage()
  return (
    <section id="legal" className="dn-sec">
      <Reveal className="dn-two dn-legal-top">
        <div>
          <div className="dn-eyebrow" style={{ marginBottom: 16 }}>
            {t('landing.legalEyebrow')}
          </div>
          <h2 className="dn-h2">{t('landing.legalTitle')}</h2>
        </div>
        <p className="dn-lede justify">
          {t('landing.legalLedePre')} <em>{t('landing.legalLedeBold')}</em>
          {t('landing.legalLedePost')}
        </p>
      </Reveal>
      <Reveal className="dn-legal-grid">
        {t('landing.legal').map((g) => (
          <div className="dn-legal-item" key={g.title}>
            <ScalesIcon size={26} />
            <div className="dn-legal-t">{g.title}</div>
            <div className="dn-legal-s">{g.sub}</div>
          </div>
        ))}
      </Reveal>
    </section>
  )
}

export function Founder() {
  const { t } = useLanguage()
  return (
    <section id="team" className="dn-sec">
      <Reveal className="dn-team">
        <div className="dn-team-copy">
          <div className="dn-eyebrow">{t('landing.advisoryEyebrow')}</div>
          <h2 className="dn-h2">{t('landing.advisoryTitle')}</h2>
          <p className="dn-lede">{t('landing.advisoryNote')}</p>
        </div>
        <div className="dn-founder">
          <div className="dn-founder-ring">
            <ImagePlate shape="circle" mono="SS" />
          </div>
          <div className="dn-founder-name">Shivaum Sharma</div>
          <div className="dn-founder-role">{t('landing.advisoryRole')}</div>
        </div>
      </Reveal>
    </section>
  )
}
