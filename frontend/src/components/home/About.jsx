import { useLanguage } from '../../i18n/LanguageContext.jsx'
import { ImagePlate, Reveal } from './shared.jsx'

export default function About() {
  const { t } = useLanguage()
  const stats = [1, 2, 3].map((n) => [t(`landing.stat${n}Value`), t(`landing.stat${n}Label`)])
  return (
    <section id="about" className="dn-sec dn-about">
      <Reveal className="dn-about-copy">
        <div className="dn-eyebrow">{t('landing.eyebrow')}</div>
        <p className="dn-about-lede">{t('landing.heroLede')}</p>
        <div className="dn-stats-sm">
          {stats.map(([v, l]) => (
            <div key={l}>
              <div className="dn-stat-sm-v tnum">{v}</div>
              <div className="dn-stat-sm-l">{l}</div>
            </div>
          ))}
        </div>
      </Reveal>
      <Reveal className="dn-collage" aria-hidden="true">
        <div className="c1">
          <ImagePlate />
        </div>
        <div className="c2">
          <ImagePlate />
        </div>
        <div className="c3">
          <ImagePlate />
        </div>
      </Reveal>
    </section>
  )
}
