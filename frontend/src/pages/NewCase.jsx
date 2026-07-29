import { useState } from 'react'
import { useNavigate, useParams, Link } from 'react-router-dom'
import { api } from '../api.js'
import { useAuth } from '../auth/AuthContext.jsx'
import { useLanguage } from '../i18n/LanguageContext.jsx'
import { ArrowLeft, ArrowRight, FileText, Receipt } from '../icons.jsx'
import Stepper from '../components/Stepper.jsx'

const EV_KINDS = ['invoice', 'receipt', 'screenshot', 'contract', 'photo', 'other']

export default function NewCase() {
  const { type } = useParams()
  const { user } = useAuth()
  const { t } = useLanguage()
  const navigate = useNavigate()

  const [form, setForm] = useState({
    claimant_name: user?.full_name || '',
    respondent_name: '',
    claim_amount: '',
    description: '',
  })
  const [evidence, setEvidence] = useState([])
  const [evName, setEvName] = useState('')
  const [evKind, setEvKind] = useState('invoice')
  const [submitting, setSubmitting] = useState(false)
  const [err, setErr] = useState('')

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
    setEvidence(claim.evidence)
  }

  function addEvidence() {
    if (!evName.trim()) return
    setEvidence((e) => [...e, { filename: evName.trim(), kind: evKind, note: null }])
    setEvName('')
  }

  async function submit(e) {
    e.preventDefault()
    setErr('')
    setSubmitting(true)
    try {
      const payload = {
        claimant_name: form.claimant_name,
        respondent_name: form.respondent_name,
        dispute_type: type,
        claim_amount: parseFloat(form.claim_amount) || 0,
        description: form.description,
        evidence,
      }
      const res = await api.createCase(payload)
      navigate(`/case/${res.case_id}/respond`)
    } catch (ex) {
      setErr(ex.message)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <section className="page fade-in container" style={{ maxWidth: 760, margin: '0 auto' }}>
      <Stepper current={1} />
      <Link to="/disputes" className="back-link">
        <ArrowLeft width={14} height={14} /> {t('newCase.backLink')}
      </Link>
      <div className="page-head between flex">
        <div>
          <h1 style={{ fontWeight: 400, marginBottom: 8 }}>{t('newCase.title')}</h1>
          <p style={{ fontSize: '0.95rem', margin: 0 }}>{t('newCase.subtitle')}</p>
        </div>
        <button className="btn btn-ghost" onClick={loadSample} type="button">
          <Receipt width={16} height={16} /> {t('newCase.loadDemo')}
        </button>
      </div>

      <form onSubmit={submit} className="card elev-sm card-pad">
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

        <div className="field">
          <label htmlFor="nc-evidence-name">{t('newCase.fieldEvidence')}</label>
          <div className="flex gap">
            <input
              id="nc-evidence-name"
              className="input"
              value={evName}
              onChange={(e) => setEvName(e.target.value)}
              placeholder={t('newCase.placeholderEvidence')}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault()
                  addEvidence()
                }
              }}
            />
            <select className="select" style={{ width: 150 }} value={evKind} onChange={(e) => setEvKind(e.target.value)}>
              {EV_KINDS.map((k) => (
                <option key={k} value={k}>{t(`newCase.kind.${k}`)}</option>
              ))}
            </select>
            <button type="button" className="btn" onClick={addEvidence}>{t('newCase.addEvidence')}</button>
          </div>
          {evidence.length > 0 && (
            <div className="ev-list">
              {evidence.map((ev, i) => (
                <div className="ev-row" key={i}>
                  <FileText width={16} height={16} />
                  {ev.filename}
                  <span className="ev-kind">{t(`newCase.kind.${ev.kind}`)}</span>
                  <span className="x" onClick={() => setEvidence((e) => e.filter((_, j) => j !== i))}>✕</span>
                </div>
              ))}
            </div>
          )}
        </div>

        {err && <p style={{ color: 'var(--red)', fontSize: '0.85rem', marginBottom: 12 }}>{err}</p>}

        <button className="btn btn-primary btn-lg" disabled={submitting}>
          {submitting ? t('newCase.filing') : t('newCase.fileClaim')} <ArrowRight />
        </button>
      </form>
    </section>
  )
}
