import { useRef } from 'react'
import { useLanguage } from '../../i18n/LanguageContext.jsx'
import { Reveal } from './shared.jsx'
import { countStr, useCountUp, useInView, usePrefersReducedMotion } from './hooks.js'

export default function Outcomes() {
  const { t } = useLanguage()
  const reduced = usePrefersReducedMotion()
  const ref = useRef(null)
  const seen = useInView(ref, { threshold: 0.2 })
  const p = useCountUp(seen, { disabled: reduced })

  return (
    <section id="outcomes" className="dn-sec">
      <Reveal className="dn-outcomes-head">
        <div className="dn-eyebrow">{t('landing.metricsEyebrow')}</div>
        <h2 className="dn-h2">{t('landing.metricsTitle')}</h2>
      </Reveal>
      <div className="dn-outcomes" ref={ref}>
        <div className="dn-bars">
          <div className="dn-bars-l">{t('landing.barsLabel')}</div>
          {t('landing.metricBars').map((b) => (
            <div key={b.label} className={`dn-bar-row ${b.muted ? 'muted' : ''}`}>
              <div className="dn-bar-top">
                <span>{b.label}</span>
                <span className="dn-bar-v tnum">{countStr(b.value, p)}</span>
              </div>
              <div className="dn-bar-track">
                <div className="dn-bar-fill" style={{ width: `${b.pct * p}%` }} />
              </div>
            </div>
          ))}
        </div>
        <div className="dn-tiles">
          {t('landing.statTiles').map((x) => (
            <div key={x.label} className="dn-tile">
              <div className="dn-tile-v tnum">{countStr(x.value, p)}</div>
              <div className="dn-tile-l">{x.label}</div>
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}
