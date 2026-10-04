import { useState } from 'react'
import { useLanguage } from '../../i18n/LanguageContext.jsx'
import { Reveal, ScalesIcon } from './shared.jsx'

// A keyword preview, not the real classifier: the Ingestion agent decides when
// a case is actually filed (the UI says so via router.routed / router.unknown).
const TIER2 = /cheque|bounc|138|loan|recover|repay|owe|unpaid|dues|contract|breach|agreement|rent|salary/
const TIER1 = /refund|defect|faulty|deliver|damag|broken|service|order|product|warrant|repair|consumer|stopped working|replace|fridge|phone|seller/

export default function TierRouter() {
  const { t } = useLanguage()
  const [query, setQuery] = useState('')
  const q = query.toLowerCase().trim()
  const tier = !q ? 0 : TIER2.test(q) ? 2 : TIER1.test(q) ? 1 : -1
  const cards = [1, 2].map((n) => ({
    tag: t(`landing.tier${n}Tag`),
    title: t(`landing.tier${n}Title`),
    desc: t(`landing.tier${n}Desc`),
    turn: t(`landing.tier${n}Turn`),
  }))
  const note = tier === 0 ? '' : tier === -1 ? t('landing.router.unknown') : t('landing.router.routed', { tier: cards[tier - 1].tag })

  return (
    <section id="tiers" className="dn-sec">
      <Reveal className="dn-router-head">
        <div className="dn-eyebrow">{t('landing.tiersEyebrow')}</div>
        <h2 className="dn-h2">{t('landing.tiersTitle')}</h2>
      </Reveal>

      <Reveal className="dn-router">
        <label htmlFor="dn-route-q">{t('landing.router.label')}</label>
        <div className="dn-router-input">
          <ScalesIcon size={22} stroke={1.5} />
          <input
            id="dn-route-q"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t('landing.router.placeholder')}
            autoComplete="off"
          />
        </div>
        <div className="dn-pills">
          {t('landing.router.examples').map((ex) => (
            <button key={ex} type="button" className="dn-pill" onClick={() => setQuery(ex)}>
              {ex}
            </button>
          ))}
        </div>
        <div className="dn-route-note" aria-live="polite">
          {note}
        </div>
      </Reveal>

      <div className="dn-tiers">
        {cards.map((c, i) => {
          const hit = tier === i + 1
          const dim = tier > 0 && !hit
          return (
            <div key={c.tag} className={`dn-tier ${hit ? 'hit' : ''} ${dim ? 'dim' : ''}`}>
              <div className="dn-tier-top">
                <span className="dn-tier-tag">{c.tag}</span>
                <span className="dn-badge">{t('landing.router.badge')}</span>
              </div>
              <div className="dn-tier-title">{c.title}</div>
              <div className="dn-tier-desc">{c.desc}</div>
              <div className="dn-tier-foot">
                <span>{t('landing.turnaroundLabel')}</span>
                <span className="dn-tier-turn tnum">{c.turn}</span>
              </div>
            </div>
          )
        })}
      </div>
    </section>
  )
}
