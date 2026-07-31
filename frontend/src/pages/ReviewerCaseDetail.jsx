import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api } from '../api.js'
import { ArrowLeft, Check, AlertTriangle } from '../icons.jsx'

export default function ReviewerCaseDetail() {
  const { id } = useParams()
  const navigate = useNavigate()
  const [caseData, setCaseData] = useState(null)
  const [error, setError] = useState('')
  const [approve, setApprove] = useState(true)
  const [note, setNote] = useState('')
  const [reliefAmount, setReliefAmount] = useState('')
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(false)

  useEffect(() => {
    api.reviewDetail(id).then(setCaseData).catch((e) => setError(e.message))
  }, [id])

  async function submit(e) {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      await api.submitReviewDecision(id, {
        approve,
        note,
        relief_amount: reliefAmount ? parseFloat(reliefAmount) : null,
      })
      setDone(true)
    } catch (ex) {
      setError(ex.message)
    } finally {
      setBusy(false)
    }
  }

  if (error && !caseData) {
    return (
      <section className="page fade-in container">
        <p style={{ color: 'var(--red)' }}>{error}</p>
      </section>
    )
  }
  if (!caseData) {
    return (
      <section className="page fade-in container">
        <p className="sub">Loading case…</p>
      </section>
    )
  }

  const already = Boolean(caseData.reviewer_decision)

  return (
    <section className="page fade-in container" style={{ maxWidth: 800 }}>
      <button className="back-link" onClick={() => navigate('/reviewer')} style={{ background: 'none', border: 'none', cursor: 'pointer' }}>
        <ArrowLeft width={14} height={14} /> Back to queue
      </button>

      <div className="page-head">
        <h1 style={{ fontWeight: 400, marginBottom: 6 }}>{caseData.case_id}</h1>
        <p className="sub">
          {caseData.dispute_type?.replace('_', ' ')} · ₹{Number(caseData.claim_amount).toLocaleString('en-IN')} · {caseData.tier_label}
        </p>
      </div>

      <div className="card elev-sm card-pad" style={{ marginBottom: 18 }}>
        <div className="field" style={{ marginBottom: 14 }}>
          <label>{caseData.claimant?.name} alleges</label>
          <p style={{ margin: 0, lineHeight: 1.6 }}>{caseData.description}</p>
        </div>
        {caseData.respondent_submission ? (
          <div className="field" style={{ marginBottom: 0 }}>
            <label>{caseData.respondent?.name} responds</label>
            <p style={{ margin: 0, lineHeight: 1.6 }}>{caseData.respondent_submission.statement}</p>
          </div>
        ) : (
          <p className="sub" style={{ marginBottom: 0 }}>No response was filed -- proceeded uncontested.</p>
        )}
      </div>

      {caseData.escalation && (
        <div className="review-note warn" style={{ marginBottom: 18 }}>
          <AlertTriangle width={18} height={18} style={{ flexShrink: 0, color: 'var(--color-accent-700)' }} />
          <div>
            <strong>Safety-gate escalation ({caseData.escalation.checkpoint})</strong>
            <ul style={{ margin: '6px 0 0', paddingLeft: 18 }}>
              {Object.values(caseData.escalation.details || {}).map((d, i) => <li key={i}>{d}</li>)}
            </ul>
          </div>
        </div>
      )}

      {caseData.mediation && (
        <div className="card elev-sm card-pad" style={{ marginBottom: 18 }}>
          <label>AI mediation proposal</label>
          <p style={{ margin: 0, lineHeight: 1.6 }}>{caseData.mediation.headline}</p>
        </div>
      )}

      {caseData.resolution && (
        <div className="card elev-sm card-pad" style={{ marginBottom: 18 }}>
          <label>AI-drafted resolution {caseData.resolution.requires_human_signoff && '(awaiting your counter-signature)'}</label>
          <p style={{ margin: '4px 0 0', fontWeight: 600 }}>{caseData.resolution.relief_amount_display}</p>
          {caseData.resolution.order?.map((line, i) => <p key={i} className="sub" style={{ margin: '4px 0 0' }}>{line}</p>)}
        </div>
      )}

      <div className="card elev-sm card-pad">
        <label style={{ display: 'block', marginBottom: 10, fontWeight: 600 }}>Your decision</label>

        {already ? (
          <div>
            <p style={{ display: 'flex', alignItems: 'center', gap: 8, color: caseData.reviewer_decision.approved ? 'var(--green)' : 'var(--red)' }}>
              {caseData.reviewer_decision.approved ? <Check width={18} height={18} /> : <AlertTriangle width={18} height={18} />}
              {caseData.reviewer_decision.approved ? 'Approved' : 'Rejected / overridden'} by {caseData.reviewer_decision.reviewer_name}
            </p>
            {caseData.reviewer_decision.note && <p className="sub">{caseData.reviewer_decision.note}</p>}
          </div>
        ) : done ? (
          <p style={{ color: 'var(--green)' }}><Check width={16} height={16} /> Decision recorded.</p>
        ) : (
          <form onSubmit={submit}>
            <div className="field" style={{ marginBottom: 12 }}>
              <label style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                <input type="radio" checked={approve} onChange={() => setApprove(true)} /> Approve / counter-sign as-is
              </label>
              <label style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <input type="radio" checked={!approve} onChange={() => setApprove(false)} /> Reject / issue my own ruling
              </label>
            </div>
            {!approve && (
              <div className="field">
                <label htmlFor="rv-relief">Relief amount (₹, optional -- 0 for no relief)</label>
                <input id="rv-relief" className="input" type="number" value={reliefAmount} onChange={(e) => setReliefAmount(e.target.value)} placeholder="e.g. 0" />
              </div>
            )}
            <div className="field">
              <label htmlFor="rv-note">Note (visible in the case record)</label>
              <textarea id="rv-note" className="textarea" value={note} onChange={(e) => setNote(e.target.value)} required />
            </div>
            {error && <p style={{ color: 'var(--red)', fontSize: '0.85rem' }}>{error}</p>}
            <button className="btn btn-primary" disabled={busy}>{busy ? 'Submitting…' : 'Submit decision'}</button>
          </form>
        )}
      </div>
    </section>
  )
}
