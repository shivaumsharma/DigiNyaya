import { useRef, useState } from 'react'
import { useLanguage } from '../../i18n/LanguageContext.jsx'
import { ImagePlate, Reveal } from './shared.jsx'
import { useCycle, useInView, usePrefersReducedMotion } from './hooks.js'

export default function Lifecycle() {
  const { t } = useLanguage()
  const reduced = usePrefersReducedMotion()
  const stages = t('landing.lifecycle')
  const ref = useRef(null)
  const inView = useInView(ref, { threshold: 0.2, once: false })
  const [hover, setHover] = useState(false)
  const [stage, setStage] = useCycle(stages.length, { active: !reduced && inView && !hover })

  return (
    <section id="lifecycle" className="dn-sec" ref={ref}>
      <Reveal className="dn-life-hero">
        <ImagePlate />
        <div className="dn-life-over">
          <div className="dn-eyebrow" style={{ marginBottom: 12 }}>
            {t('landing.lifecycleEyebrow')}
          </div>
          <div className="dn-life-title">{t('landing.lifecycleTitle')}</div>
        </div>
      </Reveal>
      <div className="dn-stages" onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}>
        {stages.map((s, i) => (
          <div
            key={s.title}
            className={`dn-stage ${i === stage ? 'on' : ''}`}
            tabIndex={0}
            onMouseEnter={() => setStage(i)}
            onFocus={() => setStage(i)}
          >
            <div className="dn-stage-day tnum">{s.day}</div>
            <div className="dn-stage-title">{s.title}</div>
            <div className="dn-stage-desc">{s.desc}</div>
          </div>
        ))}
      </div>
    </section>
  )
}
