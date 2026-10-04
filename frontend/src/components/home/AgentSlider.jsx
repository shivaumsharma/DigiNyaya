import { useRef, useState } from 'react'
import { useLanguage } from '../../i18n/LanguageContext.jsx'
import { Chevron, Reveal } from './shared.jsx'
import { localizeDigits, useCycle, useInView, usePrefersReducedMotion } from './hooks.js'

export default function AgentSlider() {
  const { t, lang } = useLanguage()
  const reduced = usePrefersReducedMotion()
  const agents = t('landing.agents')
  const n = agents.length
  const ref = useRef(null)
  const inView = useInView(ref, { threshold: 0.2, once: false })
  const [paused, setPaused] = useState(false)
  const [slide, setSlide] = useCycle(n, { active: !reduced && inView && !paused })
  const go = (i) => setSlide(((i % n) + n) % n)
  const num = (i) => localizeDigits(String(i + 1).padStart(2, '0'), lang)

  return (
    <section
      id="agents"
      className="dn-agents dn-sec"
      style={{ paddingLeft: 0, paddingRight: 0 }}
      ref={ref}
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      onFocus={() => setPaused(true)}
      onBlur={() => setPaused(false)}
    >
      <Reveal className="dn-agents-head">
        <div>
          <div className="dn-eyebrow" style={{ marginBottom: 16 }}>
            {t('landing.agentsEyebrow')}
          </div>
          <h2 className="dn-h2">{t('landing.agentsTitle')}</h2>
        </div>
        <div className="dn-agents-ctl">
          <span className="dn-counter tnum" aria-live="off">
            {num(slide)} / {num(n - 1)}
          </span>
          <button type="button" className="dn-round" aria-label={t('landing.sliderPrev')} onClick={() => go(slide - 1)}>
            <Chevron dir="left" />
          </button>
          <button type="button" className="dn-round" aria-label={t('landing.sliderNext')} onClick={() => go(slide + 1)}>
            <Chevron dir="right" />
          </button>
        </div>
      </Reveal>

      <div className="dn-track" style={{ '--i': slide }}>
        {agents.map((a, i) => {
          const status = i === slide ? 'working' : i < slide ? 'done' : 'queued'
          return (
            <div
              key={a.title}
              className={`dn-agent ${i === slide ? 'on' : ''}`}
              role="button"
              tabIndex={0}
              aria-current={i === slide ? 'true' : undefined}
              onClick={() => go(i)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault()
                  go(i)
                }
              }}
            >
              <div className="dn-ghost-num tnum" aria-hidden="true">
                {num(i)}
              </div>
              <div className="dn-kicker">
                <span className="dn-dot" />
                {t(`landing.kickers.${status}`)}
              </div>
              <div className="dn-agent-body">
                <div className="dn-agent-n tnum">{num(i)}</div>
                <div className="dn-agent-title">{a.title}</div>
                <div className="dn-agent-desc">{a.desc}</div>
              </div>
            </div>
          )
        })}
      </div>
    </section>
  )
}
