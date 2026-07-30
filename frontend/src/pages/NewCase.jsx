import { useState } from 'react'
import { useNavigate, useParams, Link } from 'react-router-dom'
import { api } from '../api.js'
import { useAuth } from '../auth/AuthContext.jsx'
import { useLanguage } from '../i18n/LanguageContext.jsx'
import { ArrowLeft, ArrowRight, Receipt, Clock } from '../icons.jsx'
import Stepper from '../components/Stepper.jsx'
import EvidenceDropzone from '../components/EvidenceDropzone.jsx'
import CaseStrengthPanel from '../components/CaseStrengthPanel.jsx'

export default function NewCase() {
  const { type } = useParams()
  const { user } = useAuth()
  const { t } = useLanguage()
  const navigate = useNavigate()

  // 'details' -> 'evidence' -> 'review', matching the backend's real
  // draft -> (upload/preliminary-review, repeatable) -> submit lifecycle.
  const [step, setStep] = useState('details')
  const [caseId, setCaseId] = useState(null)

  const [form, setForm] = useState({
    claimant_name: user?.full_name || '',
    respondent_name: '',
    claim_amount: '',
    description: '',
  })
  const [demoEvidence, setDemoEvidence] = useState([])
  const [documents, setDocuments] = useState([])
  const [creating, setCreating] = useState(false)
  const [err, setErr] = useState('')

  const [review, setReview] = useState(null)
  const [reviewLoading, setReviewLoading] = useState(false)
  const [reviewErr, setReviewErr] = useState('')
  const [filing, setFiling] = useState(false)

  function set(k, v) {
    setForm((f) => ({ ...f, [k]: v }))
  }

  async function loadSample() {
    const { claim } = await api.sampleClaim()
    setForm({
      claimant_name: claim.claimant_name,
      respondent_name: claim.respondent_name,
      claim_amount: String(claim.claim_amount),
      description: claim.description,
    })
    setDemoEvidence(claim.evidence)
  }

  async function createDraft(e) {
    e.preventDefault()
    setErr('')
    setCreating(true)
    try {
      const payload = {
        claimant_name: form.claimant_name,
        respondent_name: form.respondent_name,
        dispute_type: type,
        claim_amount: parseFloat(form.claim_amount) || 0,
        description: form.description,
        evidence: demoEvidence,
      }
      const res = await api.createCase(payload)
      setCaseId(res.case_id)
      setStep('evidence')
    } catch (ex) {
      setErr(ex.message)
    } finally {
      setCreating(false)
    }
  }

  const anyPending = documents.some((d) => d.extraction_status === 'pending')

  async function runReview() {
    setReviewLoading(true)
    setReviewErr('')
    try {
      const res = await api.preliminaryReview(caseId)
      setReview(res)
    } catch (ex) {
      setReviewErr(ex.message)
    } finally {
      setReviewLoading(false)
    }
  }

  function goToReview() {
    setStep('review')
    runReview()
  }

  async function fileClaim() {
    setFiling(true)
    setErr('')
    try {
      await api.submitCase(caseId)
      navigate(`/case/${caseId}/respond`)
    } catch (ex) {
      setErr(ex.message)
      setFiling(false)
    }
  }

  return (
    <section className="page fade-in container" style={{ maxWidth: 760, margin: '0 auto' }}>
      <Stepper current={1} />
      <Link to="/disputes" className="back-link">
        <ArrowLeft width={14} height={14} /> {t('newCase.backLink')}
      </Link>

      {step === 'details' && (
        <>
          <div className="page-head between flex">
            <div>
              <h1 style={{ fontWeight: 400, marginBottom: 8 }}>{t('newCase.title')}</h1>
              <p style={{ fontSize: '0.95rem', margin: 0 }}>{t('newCase.subtitle')}</p>
            </div>
            <button className="btn btn-ghost" onClick={loadSample} type="button">
              <Receipt width={16} height={16} /> {t('newCase.loadDemo')}
            </button>
          </div>

          <form onSubmit={createDraft} className="card elev-sm card-pad">
            <div className="field-row">
              <div className="field">
                <label htmlFor="nc-claimant-name">{t('newCase.fieldClaimant')}</label>
                <input id="nc-claimant-name" className="input" value={form.claimant_name} onChange={(e) => set('claimant_name', e.target.value)} required />
              </div>
              <div className="field">
                <label htmlFor="nc-respondent-name">{t('newCase.fieldRespondent')}</label>
                <input
                  id="nc-respondent-name"
                  className="input"
                  value={form.respondent_name}
                  onChange={(e) => set('respondent_name', e.target.value)}
                  placeholder={t('newCase.placeholderRespondent')}
                  required
                />
              </div>
            </div>

            <div className="field" style={{ maxWidth: 260 }}>
              <label htmlFor="nc-claim-amount">{t('newCase.fieldAmount')}</label>
              <input
                id="nc-claim-amount"
                className="input"
                type="number"
                value={form.claim_amount}
                onChange={(e) => set('claim_amount', e.target.value)}
                placeholder="42999"
                required
              />
            </div>

            <div className="field">
              <label htmlFor="nc-description">{t('newCase.fieldDescription')}</label>
              <textarea
                id="nc-description"
                className="textarea"
                value={form.description}
                onChange={(e) => set('description', e.target.value)}
                placeholder={t('newCase.placeholderDescription')}
                required
              />
            </div>

            {demoEvidence.length > 0 && (
              <div className="field">
                <label>{t('newCase.fieldEvidence')}</label>
                <div className="ev-list">
                  {demoEvidence.map((ev, i) => (
                    <div className="ev-row" key={i}>
                      {ev.filename}
                      <span className="ev-kind">{t(`newCase.kind.${ev.kind}`)}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {err && <p style={{ color: 'var(--red)', fontSize: '0.85rem', marginBottom: 12 }}>{err}</p>}

            <button className="btn btn-primary btn-lg" disabled={creating}>
              {creating ? t('newCase.filing') : t('newCase.continueToEvidence')} <ArrowRight />
            </button>
          </form>
        </>
      )}

      {step === 'evidence' && (
        <div className="card elev-sm card-pad">
          <h2 style={{ fontWeight: 500, marginBottom: 4 }}>{t('evidence.title')}</h2>
          <p className="sub" style={{ marginBottom: 18 }}>{t('evidence.subtitle')}</p>

          <EvidenceDropzone caseId={caseId} onDocumentsChange={setDocuments} />

          <div className="flex gap" style={{ marginTop: 22, justifyContent: 'flex-end' }}>
            <button type="button" className="btn btn-primary btn-lg" onClick={goToReview} disabled={anyPending}>
              {anyPending ? t('evidence.waiting') : t('evidence.continueToReview')} <ArrowRight />
            </button>
          </div>
        </div>
      )}

      {step === 'review' && (
        <div className="card elev-sm card-pad">
          <h2 style={{ fontWeight: 500, marginBottom: 4 }}>{t('review.title')}</h2>
          <p className="sub" style={{ marginBottom: 18 }}>{t('review.subtitle')}</p>

          {reviewLoading && (
            <p className="flex gap" style={{ alignItems: 'center', color: 'var(--text-dim)' }}>
              <Clock width={16} height={16} /> {t('review.loading')}
            </p>
          )}

          {reviewErr && <p style={{ color: 'var(--red)', fontSize: '0.85rem' }}>{reviewErr}</p>}

          {review && !reviewLoading && (
            <CaseStrengthPanel
              review={review}
              title={t('caseStrength.claimantAgentTitle')}
              subtitle={t('caseStrength.claimantAgentDetail')}
              t={t}
            />
          )}

          {err && <p style={{ color: 'var(--red)', fontSize: '0.85rem', marginTop: 14 }}>{err}</p>}

          <div className="flex gap" style={{ marginTop: 22, justifyContent: 'space-between' }}>
            <button type="button" className="btn btn-ghost" onClick={() => setStep('evidence')}>
              {t('review.addMoreEvidence')}
            </button>
            <button type="button" className="btn btn-primary btn-lg" onClick={fileClaim} disabled={filing || reviewLoading}>
              {filing ? t('newCase.filing') : t('newCase.fileClaim')} <ArrowRight />
            </button>
          </div>
        </div>
      )}
    </section>
  )
}
