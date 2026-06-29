import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api } from '../api.js'
import { Clock, ArrowRight, Bot } from '../icons.jsx'
import Stepper from '../components/Stepper.jsx'

export default function Respondent() {
  const { id } = useParams()
  const navigate = useNavigate()
  const [caseData, setCaseData] = useState(null)
  const [statement, setStatement] = useState('')
  const [counter, setCounter] = useState('')
  const [accepts, setAccepts] = useState(false)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api.getCase(id).then(setCaseData).catch(() => {})
  }, [id])

  async function loadSampleResponse() {
    const { response } = await api.sampleClaim()
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
    <section className="page fade-in">
      <Stepper current={2} />
      <div className="page-head">
        <h2>Respondent notified</h2>
        <p>
          {caseData ? (
            <>
              <strong>{caseData.respondent?.name}</strong> has been served digitally for case{' '}
              <strong>{id}</strong> and has <strong>72 hours</strong> to respond. For the demo, act
              as the respondent below — or let the window lapse.
            </>
          ) : (
            'Loading case…'
          )}
        </p>
      </div>

      <div className="flex gap" style={{ alignItems: 'flex-start', flexWrap: 'wrap' }}>
        <div className="card card-pad" style={{ flex: 1, minWidth: 340, maxWidth: 560 }}>
          <div className="flex between" style={{ marginBottom: 16 }}>
            <h3 style={{ fontSize: '1.1rem' }}>Respondent's reply</h3>
            <button className="btn btn-ghost" style={{ padding: '7px 13px' }} onClick={loadSampleResponse} type="button">
              Load demo reply
            </button>
          </div>
          <div className="field">
            <label>Statement</label>
            <textarea
              className="textarea"
              style={{ minHeight: 110 }}
              value={statement}
              onChange={(e) => setStatement(e.target.value)}
              placeholder="The respondent's side of the story…"
            />
          </div>
          <div className="field-row">
            <div className="field">
              <label>Counter-offer (₹, optional)</label>
              <input className="input" type="number" value={counter} onChange={(e) => setCounter(e.target.value)} placeholder="e.g. 20000" />
            </div>
            <div className="field">
              <label>Liability</label>
              <label style={{ display: 'flex', alignItems: 'center', gap: 9, fontSize: '0.9rem', color: 'var(--text)', marginTop: 10 }}>
                <input type="checkbox" checked={accepts} onChange={(e) => setAccepts(e.target.checked)} />
                Respondent accepts liability
              </label>
            </div>
          </div>
          <button className="btn btn-primary btn-block" disabled={busy} onClick={submitResponse}>
            Submit response & start AI resolution <ArrowRight />
          </button>
        </div>

        <div className="card card-pad" style={{ width: 320 }}>
          <div className="flex gap" style={{ alignItems: 'center', marginBottom: 8 }}>
            <Clock width={20} height={20} />
            <h3 style={{ fontSize: '1.05rem' }}>No response?</h3>
          </div>
          <p className="muted" style={{ fontSize: '0.88rem', marginBottom: 18 }}>
            If the respondent ignores the notice, the case proceeds uncontested. The agents will
            treat the allegations as substantially admitted.
          </p>
          <button className="btn btn-block" disabled={busy} onClick={skip}>
            <Bot width={16} height={16} /> Skip — proceed uncontested
          </button>
        </div>
      </div>
    </section>
  )
}
