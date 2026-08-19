import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api.js'
import { Shield, Clock } from '../icons.jsx'

// Internal reviewer tool, not citizen-facing -- plain English throughout
// rather than routed through the i18n dictionaries the rest of the app uses,
// since reviewers are a small trusted staff group, not the general public.
export default function ReviewerQueue() {
  const navigate = useNavigate()
  const [cases, setCases] = useState(null)
  const [error, setError] = useState('')
  const [evalMetrics, setEvalMetrics] = useState(null)
  const [opsMetrics, setOpsMetrics] = useState(null)

  useEffect(() => {
    api.reviewQueue().then(setCases).catch((e) => setError(e.message))
    // Best-effort -- absent in most environments (data_cache/ is gitignored,
    // only produced by manually running scripts/judge_real_outcomes.py), so
    // a failure here should never block the queue itself from rendering.
    api.evalMetrics().then(setEvalMetrics).catch(() => {})
    api.opsMetrics().then(setOpsMetrics).catch(() => {})
  }, [])

  return (
    <section className="page fade-in container">
      <div className="page-head">
        <h1 style={{ fontWeight: 400, marginBottom: 8 }}>
          <Shield width={22} height={22} style={{ verticalAlign: -3, marginRight: 8 }} />
          Review queue
        </h1>
        <p style={{ fontSize: '0.95rem' }}>Cases awaiting a human decision -- escalated, manually flagged, or a Tier 2 draft awaiting counter-signature.</p>
      </div>

      {opsMetrics && (
        <div className="card elev-sm card-pad" style={{ marginBottom: 16, fontSize: '0.85rem' }}>
          <div style={{ fontWeight: 600, marginBottom: 4 }}>Case volume</div>
          <p className="sub" style={{ margin: 0 }}>
            <strong>{opsMetrics.total_cases}</strong> total ·{' '}
            {Object.entries(opsMetrics.by_tier).map(([tier, n]) => `Tier ${tier}: ${n}`).join(', ')} ·{' '}
            {Object.entries(opsMetrics.by_status).map(([s, n]) => `${s.replace('_', ' ')}: ${n}`).join(', ')}
          </p>
          <p className="sub" style={{ margin: '4px 0 0' }}>
            Escalation rate: {opsMetrics.escalation_rate_of_terminal != null ? `${Math.round(opsMetrics.escalation_rate_of_terminal * 100)}%` : '—'} of
            {' '}concluded cases (of all filed: {opsMetrics.escalation_rate_of_total != null ? `${Math.round(opsMetrics.escalation_rate_of_total * 100)}%` : '—'}) ·{' '}
            {opsMetrics.awaiting_review_count} awaiting review now
          </p>
        </div>
      )}

      {evalMetrics && evalMetrics.available && (
        <div className="card elev-sm card-pad" style={{ marginBottom: 16, fontSize: '0.85rem' }} title={evalMetrics.methodology}>
          <div style={{ fontWeight: 600, marginBottom: 4 }}>Real-judgment eval (calibration signal, not a validated accuracy figure)</div>
          <p className="sub" style={{ margin: 0 }}>
            AI resolution vs. real court outcome, {evalMetrics.sample_size} cases:{' '}
            <strong>{evalMetrics.counts.match} match</strong>, <strong>{evalMetrics.counts.partial} partial</strong>,{' '}
            <strong>{evalMetrics.counts.mismatch} mismatch</strong>
            {evalMetrics.counts.judge_failed > 0 && `, ${evalMetrics.counts.judge_failed} judge-failed`}
            {' · '}
            {Math.round(evalMetrics.match_rate * 100)}% exact match, {Math.round(evalMetrics.match_or_partial_rate * 100)}% match-or-partial
          </p>
        </div>
      )}

      {error && <p style={{ color: 'var(--red)' }}>{error}</p>}
      {cases && cases.length === 0 && <p className="sub">Nothing awaiting review right now.</p>}

      {cases && cases.length > 0 && (
        <div className="ev-list">
          {cases.map((c) => (
            <div
              key={c.case_id}
              className="card elev-sm card-pad"
              style={{ cursor: 'pointer', marginBottom: 10 }}
              onClick={() => navigate(`/reviewer/${c.case_id}`)}
            >
              <div className="flex between" style={{ alignItems: 'flex-start' }}>
                <div>
                  <div style={{ fontWeight: 600 }}>{c.claimant} vs {c.respondent}</div>
                  <p className="sub" style={{ margin: '4px 0 0' }}>
                    {c.dispute_type.replace('_', ' ')} · ₹{Number(c.claim_amount).toLocaleString('en-IN')}
                  </p>
                </div>
                <span className="tag tag-outline">{c.reason}</span>
              </div>
              <p className="sub" style={{ marginTop: 8, display: 'flex', alignItems: 'center', gap: 6, fontSize: '0.78rem' }}>
                <Clock width={13} height={13} /> {c.case_id}
              </p>
            </div>
          ))}
        </div>
      )}
    </section>
  )
}
