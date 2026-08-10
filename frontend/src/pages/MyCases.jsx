import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api.js'
import { useLanguage } from '../i18n/LanguageContext.jsx'
import { Check, Clock, AlertTriangle, ArrowRight } from '../icons.jsx'

const STATUS_ICON = {
  draft: Clock,
  awaiting_response: Clock,
  ready: Clock,
  mediation_proposed: Clock,
  resolved: Check,
  escalated: AlertTriangle,
}

const STATUS_COLOR = {
  draft: 'var(--text-dim)',
  awaiting_response: 'var(--color-accent-700)',
  ready: 'var(--color-accent-700)',
  mediation_proposed: 'var(--color-accent-700)',
  resolved: 'var(--green)',
  escalated: 'var(--red)',
}

function fmtAmount(n) {
  return `Rs. ${Number(n || 0).toLocaleString('en-IN')}`
}

function fmtDate(iso) {
  if (!iso) return ''
  try {
    return new Date(iso).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' })
  } catch {
    return iso
  }
}

// The claimant's own case list -- previously the only way to find a case
// again was to already have its exact case_id (no listing endpoint
// existed). Filed-by-me only, see the backend's CaseSummaryOut docstring
// for why "cases against me" isn't listed here yet.
export default function MyCases() {
  const { t } = useLanguage()
  const [cases, setCases] = useState(null)
  const [err, setErr] = useState('')

  useEffect(() => {
    api.myCases().then(setCases).catch((ex) => setErr(ex.message))
  }, [])

  return (
    <section className="page fade-in container" style={{ width: '100%' }}>
      <div className="page-head">
        <h1 style={{ fontWeight: 400, marginBottom: 10 }}>{t('myCases.title')}</h1>
        <p style={{ fontSize: '0.95rem', maxWidth: '70ch', lineHeight: 1.6 }}>{t('myCases.subtitle')}</p>
      </div>

      {err && <p style={{ color: 'var(--red)', fontSize: '0.9rem' }}>{err}</p>}

      {cases === null && !err && <p className="sub">{t('myCases.loading')}</p>}

      {cases && cases.length === 0 && (
        <div className="card elev-sm card-pad" style={{ textAlign: 'center' }}>
          <p className="sub" style={{ marginBottom: 16 }}>{t('myCases.empty')}</p>
          <Link to="/disputes" className="btn btn-primary">
            {t('myCases.fileFirst')} <ArrowRight width={16} height={16} />
          </Link>
        </div>
      )}

      {cases && cases.length > 0 && (
        <div className="ev-list" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {cases.map((c) => {
            const Icon = STATUS_ICON[c.status] || Clock
            const color = STATUS_COLOR[c.status] || 'var(--text-dim)'
            return (
              <Link
                to={`/case/${c.case_id}`}
                key={c.case_id}
                className="card elev-sm card-pad"
                style={{ display: 'flex', alignItems: 'center', gap: 14, textDecoration: 'none', color: 'inherit' }}
              >
                <Icon width={20} height={20} style={{ flexShrink: 0, color }} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontWeight: 600 }}>
                    {c.case_id} <span className="sub" style={{ fontWeight: 400 }}>vs {c.respondent || '—'}</span>
                  </div>
                  <div className="sub" style={{ fontSize: '0.82rem', marginTop: 2 }}>
                    {t(`myCases.status.${c.status}`)} · {c.tier_label} · {fmtDate(c.created_at)}
                  </div>
                </div>
                <div style={{ fontWeight: 600, flexShrink: 0 }}>{fmtAmount(c.claim_amount)}</div>
              </Link>
            )
          })}
        </div>
      )}
    </section>
  )
}
