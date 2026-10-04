import { useRef } from 'react'
import { useLanguage } from '../../i18n/LanguageContext.jsx'
import { Reveal } from './shared.jsx'
import { countStr, useCountUp, useInView, usePrefersReducedMotion } from './hooks.js'

export default function Problem() {
  const { t } = useLanguage()
  const reduced = usePrefersReducedMotion()
  const rowRef = useRef(null)
  const seen = useInView(rowRef, { threshold: 0.3 })
  const p = useCountUp(seen, { disabled: reduced })
  const stats = t('landing.problem.stats')

  return (
    <section id="problem" className="dn-sec dn-problem">
      <Reveal className="dn-two dn-problem-top">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
          <div className="dn-eyebrow">{t('landing.problem.eyebrow')}</div>
          <h2 className="dn-h2">{t('landing.problem.title')}</h2>
        </div>
        <p className="dn-lede justify">{t('landing.problem.body')}</p>
      </Reveal>
      <div className="dn-stats3" ref={rowRef}>
        {stats.map((s) => (
          <div className="dn-stat3" key={s.label}>
            <div className="dn-bignum tnum">{countStr(s.value, p)}</div>
            <div className="dn-stat3-l">{s.label}</div>
          </div>
        ))}
      </div>
      <div className="dn-source">{t('landing.problem.source')}</div>
    </section>
  )
}
