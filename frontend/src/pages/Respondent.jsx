import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api } from '../api.js'
import { useLanguage } from '../i18n/LanguageContext.jsx'
import { Clock, ArrowRight, Bot } from '../icons.jsx'
import Stepper from '../components/Stepper.jsx'
import CaseStrengthPanel from '../components/CaseStrengthPanel.jsx'

export default function Respondent() {
  const { id } = useParams()
  const { t } = useLanguage()
  const navigate = useNavigate()
  const [caseData, setCaseData] = useState(null)
  const [statement, setStatement] = useState('')
  const [counter, setCounter] = useState('')
  const [accepts, setAccepts] = useState(false)
  const [busy, setBusy] = useState(false)
  const [claimantReview, setClaimantReview] = useState(null)

  useEffect(() => {
    api.getCase(id).then(setCaseData).catch(() => {})
    // Re-runs the same advisory check shown to the claimant before filing --
    // lets the respondent see the claimant's evidence and how strong their
    // case looks, rather than replying blind. Best-effort: if it fails,
    // the reply form still works without it.
    api.preliminaryReview(id).then(setClaimantReview).catch(() => {})
  }, [id])

  async function loadSampleResponse() {
    const { response } = await api.sampleClaim(caseData?.dispute_type)
    setStatement(response.statement)
    setCounter(response.counter_offer != null ? String(response.counter_offer) : '')
    setAccepts(response.accepts_liability)
  }

  async function submitResponse() {
    setBusy(true)
    try {
      await api.respond(id, {
        statement,
        accepts_liability: accepts,
        counter_offer: counter ? parseFloat(counter) : null,
      })
      navigate(`/case/${id}`)
    } finally {
      setBusy(false)
    }
  }

  async function skip() {
    setBusy(true)
    try {
      await api.skipResponse(id)
      navigate(`/case/${id}`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="page fade-in container">
      <Stepper current={2} />
      <div className="page-head">
        <h1 style={{ fontWeight: 400, marginBottom: 10 }}>{t('respondent.title')}</h1>
        <p style={{ fontSize: '0.95rem', maxWidth: '75ch', lineHeight: 1.6 }}>
          {caseData ? t('respondent.notice', { name: caseData.respondent?.name, id }) : t('respondent.loading')}
        </p>
      </div>

      {claimantReview && (
        <div className="card elev-sm card-pad" style={{ marginBottom: 22 }}>
          <CaseStrengthPanel
            review={claimantReview}
            title={t('caseStrength.respondentAgentTitle')}
            subtitle={t('caseStrength.respondentAgentDetail')}
            t={t}
            showStrength={false}
          />
        </div>
      )}

      <div className="flex gap" style={{ alignItems: 'flex-start', flexWrap: 'wrap' }}>
        <div className="card elev-sm card-pad" style={{ flex: 1, minWidth: 340, maxWidth: 560 }}>
          <div className="flex between" style={{ marginBottom: 16 }}>
            <h3 style={{ fontSize: '1.1rem', fontWeight: 600 }}>{t('respondent.replyTitle')}</h3>
            <button className="btn btn-ghost" style={{ padding: '7px 13px' }} onClick={loadSampleResponse} type="button">
              {t('respondent.loadDemoReply')}
            </button>
          </div>
          <div className="field">
            <label htmlFor="resp-statement">{t('respondent.fieldStatement')}</label>
            <textarea
              id="resp-statement"
              className="textarea"
              style={{ minHeight: 110 }}
              value={statement}
              onChange={(e) => setStatement(e.target.value)}
              placeholder={t('respondent.placeholderStatement')}
            />
          </div>
          <div className="field-row">
            <div className="field">
              <label htmlFor="resp-counter">{t('respondent.fieldCounter')}</label>
              <input id="resp-counter" className="input" type="number" value={counter} onChange={(e) => setCounter(e.target.value)} placeholder={t('respondent.placeholderCounter')} />
            </div>
            <div className="field">
              <label>{t('respondent.fieldLiability')}</label>
              <label style={{ display: 'flex', alignItems: 'center', gap: 9, fontSize: '0.9rem', color: 'var(--text)', marginTop: 10 }}>
                <input type="checkbox" checked={accepts} onChange={(e) => setAccepts(e.target.checked)} />
                {t('respondent.acceptsLiability')}
              </label>
            </div>
          </div>
          <button className="btn btn-primary btn-block" disabled={busy} onClick={submitResponse}>
            {t('respondent.submit')} <ArrowRight />
          </button>
        </div>

        <div className="card elev-sm card-pad" style={{ width: 320 }}>
          <div className="flex gap" style={{ alignItems: 'center', marginBottom: 8 }}>
            <Clock width={20} height={20} />
            <h3 style={{ fontSize: '1.05rem', fontWeight: 600 }}>{t('respondent.noResponseTitle')}</h3>
          </div>
          <p className="muted" style={{ fontSize: '0.88rem', marginBottom: 18 }}>
            {t('respondent.noResponseText')}
          </p>
          <button className="btn btn-block" disabled={busy} onClick={skip}>
            <Bot width={16} height={16} /> {t('respondent.skip')}
          </button>
        </div>
      </div>
    </section>
  )
}
