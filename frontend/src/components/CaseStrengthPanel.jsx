import { Bot, Check, AlertTriangle, Clock } from '../icons.jsx'

function scoreColor(score) {
  if (score >= 70) return 'var(--green)'
  if (score >= 40) return 'var(--color-accent-700)'
  return 'var(--red)'
}

// Shared with NewCase.jsx (claimant, pre-filing) and Respondent.jsx (shown
// the same read on the case they're replying to) -- one advisory "agent"
// (app.agents.preliminary_review, NOT part of the real 5-agent pipeline)
// reviewing the description + evidence on file. Visually matches Resolve.jsx's
// agent-row treatment (.agent-row/.agent-icon/.agent-status) so it reads as
// consistent "an agent is doing this" rather than a one-off widget.
export default function CaseStrengthPanel({ review, title, subtitle, t }) {
  if (!review) return null
  const { documents, case_strength_note, description_review, winnability } = review
  const color = scoreColor(winnability.score)

  return (
    <div className="agent-row visible done">
      <div className="agent-icon" style={{ borderColor: color, color }}>
        <Bot width={20} height={20} />
      </div>
      <div className="agent-body">
        <div className="agent-title">
          {title}
          <span className="agent-status done">{t('caseStrength.doneTag')}</span>
        </div>
        <div className="agent-detail">{subtitle}</div>

        <div className="flex gap" style={{ alignItems: 'center', marginTop: 16 }}>
          <div
            style={{
              width: 56, height: 56, borderRadius: '50%', display: 'grid', placeItems: 'center',
              border: `2px solid ${color}`, color, flexShrink: 0,
              fontFamily: 'var(--font-heading)', fontWeight: 700, fontSize: '1.15rem',
            }}
          >
            {winnability.score}
          </div>
          <div>
            <div style={{ fontWeight: 600, textTransform: 'capitalize', color }}>
              {winnability.label} {t('caseStrength.caseSuffix')}
            </div>
            {winnability.reasons.length > 0 && (
              <ul style={{ margin: '4px 0 0', paddingLeft: 18, fontSize: '0.82rem', color: 'var(--text-dim)' }}>
                {winnability.reasons.map((r, i) => (
                  <li key={i}>{r}</li>
                ))}
              </ul>
            )}
          </div>
        </div>

        {!description_review.detailed_enough && description_review.note && (
          <div className="review-note warn" style={{ marginTop: 16 }}>
            <AlertTriangle width={16} height={16} style={{ flexShrink: 0, color: 'var(--color-accent-700)' }} />
            <span>{description_review.note}</span>
          </div>
        )}

        <div className={`review-note ${documents.some((d) => d.relevant === true) ? 'ok' : 'warn'}`} style={{ marginTop: 12 }}>
          {documents.some((d) => d.relevant === true) ? (
            <Check width={18} height={18} style={{ flexShrink: 0, color: 'var(--green)' }} />
          ) : (
            <AlertTriangle width={18} height={18} style={{ flexShrink: 0, color: 'var(--color-accent-700)' }} />
          )}
          <span>{case_strength_note}</span>
        </div>

        {documents.length > 0 && (
          <div className="ev-list">
            {documents.map((d) => (
              <div className="review-doc" key={d.document_id}>
                {d.relevant === true ? (
                  <Check width={16} height={16} style={{ flexShrink: 0, marginTop: 2, color: 'var(--green)' }} />
                ) : d.relevant === false ? (
                  <AlertTriangle width={16} height={16} style={{ flexShrink: 0, marginTop: 2, color: 'var(--red)' }} />
                ) : (
                  <Clock width={16} height={16} style={{ flexShrink: 0, marginTop: 2, color: 'var(--text-dim)' }} />
                )}
                <div>
                  <div>
                    <strong>{d.filename}</strong>
                    {d.looks_like && <span className="looks-like">{d.looks_like}</span>}
                  </div>
                  {d.note && <p className="sub" style={{ margin: '3px 0 0' }}>{d.note}</p>}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
