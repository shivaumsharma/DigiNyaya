import { useId, useState } from 'react'
import { useLanguage } from '../../i18n/LanguageContext.jsx'
import { Reveal } from './shared.jsx'
import { localizeDigits } from './hooks.js'

// Real composite-confidence weights (backend/app/core/confidence.py) and the
// real hard escalation floor (backend/app/core/safety_gate.py: 0.4). The slider
// starting values are illustrative, which the panel says on its face.
const WEIGHTS = [0.35, 0.35, 0.2, 0.1]
const FLOOR = 40
const START = [80, 70, 100, 90]

const Check = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M20 6 9 17l-5-5" />
  </svg>
)
const Person = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <circle cx="9" cy="7" r="4" />
    <path d="M3 21v-2a4 4 0 0 1 4-4h4a4 4 0 0 1 4 4v2" />
    <path d="M16 11h6" />
    <path d="m19 8 3 3-3 3" />
  </svg>
)

export default function ConfidenceGate() {
  const { t, lang } = useLanguage()
  const uid = useId()
  const d = (v) => localizeDigits(v, lang)
  const [vals, setVals] = useState(START)
  const signals = t('landing.gate.signals')
  const score = Math.round(vals.reduce((sum, v, i) => sum + v * WEIGHTS[i], 0))
  const pass = score >= FLOOR

  return (
    <section id="gate" className="dn-sec dn-gate">
      <div className="dn-two">
        <Reveal className="dn-gate-copy">
          <div className="dn-eyebrow">{t('landing.gate.eyebrow')}</div>
          <h2 className="dn-h2">
            {t('landing.gate.titlePre')} <em>{t('landing.gate.titleEm')}</em> {t('landing.gate.titlePost')}
          </h2>
          <p className="dn-lede justify">{t('landing.gate.body')}</p>
        </Reveal>

        <Reveal className="dn-panel">
          <div className="dn-panel-top">
            <span>{t('landing.gate.prompt')}</span>
            <span className="dn-tag">{t('landing.gate.tag')}</span>
          </div>

          {signals.map((s, i) => (
            <div className="dn-signal" key={s.label}>
              <label className="dn-signal-row" htmlFor={`${uid}-${i}`}>
                <span>
                  {s.label} <small className="tnum">· {s.weight}</small>
                </span>
                <span className="tnum" style={{ color: 'var(--dn-ink2)' }}>
                  {d(vals[i])}%
                </span>
              </label>
              <input
                id={`${uid}-${i}`}
                className="dn-range"
                type="range"
                min="0"
                max="100"
                step="5"
                value={vals[i]}
                onChange={(e) => setVals((v) => v.map((x, j) => (j === i ? Number(e.target.value) : x)))}
              />
              <div className="dn-signal-hint">{s.hint}</div>
            </div>
          ))}

          <div className="dn-score-row">
            <span className="dn-score tnum">
              {d(score)}
              <small>%</small>
            </span>
            <span className="dn-score-l">{t('landing.gate.scoreLabel')}</span>
          </div>

          <div className="dn-meter" aria-hidden="true">
            <div className="dn-meter-bg" />
            <div className="dn-meter-fill" style={{ width: `${score}%`, background: pass ? 'var(--dn-acc)' : 'var(--dn-ink3)' }} />
            <div className="dn-meter-floor" style={{ left: `${FLOOR}%` }} />
            <div className="dn-meter-floor-l" style={{ left: `${FLOOR}%` }}>
              {t('landing.gate.floorLabel')} · {t('landing.gate.floorValue')}
            </div>
          </div>

          <div className="dn-result" aria-live="polite">
            <span className="dn-result-ico">{pass ? <Check /> : <Person />}</span>
            <div>
              <div className="dn-result-t">{pass ? t('landing.gate.passTitle') : t('landing.gate.failTitle')}</div>
              <div className="dn-result-s">{pass ? t('landing.gate.passSub') : t('landing.gate.failSub')}</div>
            </div>
          </div>
        </Reveal>
      </div>

      <Reveal className="dn-principles">
        {t('landing.gate.principles').map((p) => (
          <div className="dn-principle" key={p.title}>
            <div className="dn-principle-t">{p.title}</div>
            <div className="dn-principle-b">{p.body}</div>
          </div>
        ))}
      </Reveal>
    </section>
  )
}
