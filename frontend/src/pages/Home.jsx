import { useNavigate } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext.jsx'
import { useLanguage } from '../i18n/LanguageContext.jsx'
import { Logo } from '../icons.jsx'

const AGENT_ICONS = [
  (
    <svg key="ingestion" width="24" height="24" viewBox="0 0 24 24" fill="none">
      <rect x="5" y="3" width="14" height="18" rx="1.5" stroke="var(--color-accent)" strokeWidth="1.4" />
      <path d="M8 8h8M8 12h8M8 16h5" stroke="var(--color-accent)" strokeWidth="1.4" strokeLinecap="round" />
    </svg>
  ),
  (
    <svg key="research" width="24" height="24" viewBox="0 0 24 24" fill="none">
      <path
        d="M12 3v18M5 6l-3 7a3 3 0 0 0 6 0l-3-7ZM19 6l-3 7a3 3 0 0 0 6 0l-3-7ZM3 6h18"
        stroke="var(--color-accent)"
        strokeWidth="1.4"
        strokeLinejoin="round"
      />
    </svg>
  ),
  (
    <svg key="analysis" width="24" height="24" viewBox="0 0 24 24" fill="none">
      <path d="M4 20V10M11 20V4M18 20V13" stroke="var(--color-accent)" strokeWidth="1.4" strokeLinecap="round" />
    </svg>
  ),
  (
    <svg key="mediation" width="24" height="24" viewBox="0 0 24 24" fill="none">
      <circle cx="9" cy="12" r="5.2" stroke="var(--color-accent)" strokeWidth="1.4" />
      <circle cx="15" cy="12" r="5.2" stroke="var(--color-accent)" strokeWidth="1.4" />
    </svg>
  ),
  (
    <svg key="resolution" width="24" height="24" viewBox="0 0 24 24" fill="none">
      <path
        d="M5 21V5a2 2 0 0 1 2-2h8l4 4v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2Z"
        stroke="var(--color-accent)"
        strokeWidth="1.4"
        strokeLinejoin="round"
      />
      <path
        d="M8.5 13.5l2.3 2.3L15.5 11"
        stroke="var(--color-accent)"
        strokeWidth="1.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  ),
]

const ADVISORS = [
  { initials: 'SS', name: 'Shivaum Sharma' },
  { initials: 'HS', name: 'Harshita Shahi' },
  { initials: 'AB', name: 'Akshat Bagadia' },
]

export default function Home() {
  const { user } = useAuth()
  const { t } = useLanguage()
  const AGENTS = t('landing.agents').map((a, i) => ({ ...a, icon: AGENT_ICONS[i] }))
  const LIFECYCLE = t('landing.lifecycle')
  const LEGAL = t('landing.legal')
  const navigate = useNavigate()

  function goFileDispute() {
    if (user) navigate('/disputes')
    else navigate('/login', { state: { from: { pathname: '/disputes' } } })
  }

  return (
    <div className="fade-in">
      {/* ---------- Hero ---------- */}
      <section
        style={{
          maxWidth: 1400,
          margin: '0 auto',
          padding: 'var(--space-8) var(--space-8) var(--space-6)',
          display: 'grid',
          gridTemplateColumns: '1.05fr 0.95fr',
          gap: 56,
          alignItems: 'stretch',
        }}
        className="hero-grid-responsive"
      >
        <div>
          <div className="tag tag-outline" style={{ marginBottom: 18 }}>
            {t('landing.eyebrow')}
          </div>
          <h1 style={{ fontWeight: 400, fontSize: 46, lineHeight: 1.08, marginBottom: 18 }}>
            {t('landing.heroTitle')}
          </h1>
          <p style={{ fontSize: '1.03rem', lineHeight: 1.65, maxWidth: '52ch', opacity: 0.8, marginBottom: 26 }}>
            {t('landing.heroLede')}
          </p>
          <div style={{ display: 'flex', gap: 14, marginBottom: 'var(--space-6)', alignItems: 'center' }}>
            <button className="btn btn-primary btn-lg" onClick={goFileDispute}>
              {t('landing.ctaFile')}
            </button>
            <a href="#lifecycle" className="btn btn-ghost">
              {t('landing.ctaWatch')}
            </a>
          </div>
          <hr className="hr" style={{ margin: '0 0 var(--space-4)' }} />
          <div style={{ display: 'flex' }}>
            <div style={{ flex: 1, paddingRight: 20 }}>
              <div style={{ fontFamily: 'var(--font-heading)', fontSize: 30, fontWeight: 600, color: 'var(--color-accent-700)' }}>
                {t('landing.stat1Value')}
              </div>
              <div style={{ fontSize: 13, opacity: 0.65, marginTop: 4 }}>{t('landing.stat1Label')}</div>
            </div>
            <div style={{ flex: 1, padding: '0 20px', borderLeft: '1px solid var(--color-divider)' }}>
              <div style={{ fontFamily: 'var(--font-heading)', fontSize: 30, fontWeight: 600 }}>{t('landing.stat2Value')}</div>
              <div style={{ fontSize: 13, opacity: 0.65, marginTop: 4 }}>{t('landing.stat2Label')}</div>
            </div>
            <div style={{ flex: 1, paddingLeft: 20, borderLeft: '1px solid var(--color-divider)' }}>
              <div style={{ fontFamily: 'var(--font-heading)', fontSize: 30, fontWeight: 600 }}>{t('landing.stat3Value')}</div>
              <div style={{ fontSize: 13, opacity: 0.65, marginTop: 4 }}>{t('landing.stat3Label')}</div>
            </div>
          </div>
        </div>

        <div
          className="card elev-md"
          style={{
            padding: 'var(--space-5) var(--space-5) var(--space-4)',
            display: 'flex',
            flexDirection: 'column',
            justifyContent: 'space-between',
            height: '100%',
          }}
        >
          <div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 16 }}>
              <div className="card-title" style={{ fontFamily: 'var(--font-heading)', fontWeight: 600, fontSize: 15 }}>
                {t('landing.previewTitle')}
              </div>
              <span className="tag tag-accent">{t('landing.previewTag')}</span>
            </div>
            <PipelinePreview steps={t('landing.previewSteps')} />
          </div>
          <button className="btn btn-block" onClick={goFileDispute} style={{ marginTop: 24 }}>
            {t('landing.previewCta')}
          </button>
        </div>
      </section>

      {/* ---------- Trust strip ---------- */}
      <section style={{ borderTop: '1px solid var(--color-divider)', borderBottom: '1px solid var(--color-divider)', padding: 'var(--space-3) var(--space-8)' }}>
        <div style={{ maxWidth: 1400, margin: '0 auto', display: 'flex', flexWrap: 'wrap', gap: '10px 36px', fontSize: 13, opacity: 0.7 }}>
          {t('landing.trust').map((line) => (
            <span key={line}>{line}</span>
          ))}
        </div>
      </section>

      {/* ---------- Tier comparison ---------- */}
      <section style={{ maxWidth: 1400, margin: '0 auto', padding: 'var(--space-8)' }}>
        <div className="tag tag-outline" style={{ marginBottom: 14 }}>
          {t('landing.tiersEyebrow')}
        </div>
        <h2 style={{ fontWeight: 400, marginBottom: 36 }}>{t('landing.tiersTitle')}</h2>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 28 }}>
          <div className="card elev-sm" style={{ padding: 'var(--space-6)', borderColor: 'var(--color-accent)' }}>
            <div className="tag tag-accent" style={{ marginBottom: 14 }}>
              {t('landing.tier1Tag')}
            </div>
            <div className="card-title" style={{ fontFamily: 'var(--font-heading)', fontWeight: 600, fontSize: 19 }}>
              {t('landing.tier1Title')}
            </div>
            <div style={{ fontSize: 13, opacity: 0.8, margin: '8px 0 14px' }}>
              {t('landing.tier1Desc')}
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13, paddingTop: 12, borderTop: '1px solid var(--color-divider)' }}>
              <span style={{ opacity: 0.6 }}>{t('landing.turnaroundLabel')}</span>
              <strong>{t('landing.tier1Turn')}</strong>
            </div>
          </div>
          <div className="card elev-sm" style={{ padding: 'var(--space-6)' }}>
            <div className="tag tag-outline" style={{ marginBottom: 14 }}>
              {t('landing.tier2Tag')}
            </div>
            <div className="card-title" style={{ fontFamily: 'var(--font-heading)', fontWeight: 600, fontSize: 19 }}>
              {t('landing.tier2Title')}
            </div>
            <div style={{ fontSize: 13, opacity: 0.8, margin: '8px 0 14px' }}>
              {t('landing.tier2Desc')}
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13, paddingTop: 12, borderTop: '1px solid var(--color-divider)' }}>
              <span style={{ opacity: 0.6 }}>{t('landing.turnaroundLabel')}</span>
              <strong>{t('landing.tier2Turn')}</strong>
            </div>
          </div>
        </div>
      </section>

      {/* ---------- Agents ---------- */}
      <section id="agents" style={{ background: 'var(--color-surface)', padding: 'var(--space-8)' }}>
        <div style={{ maxWidth: 1400, margin: '0 auto' }}>
          <div className="tag tag-outline" style={{ marginBottom: 14 }}>
            {t('landing.agentsEyebrow')}
          </div>
          <h2 style={{ fontWeight: 400, marginBottom: 36 }}>{t('landing.agentsTitle')}</h2>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 18 }}>
            {AGENTS.map((a) => (
              <div key={a.title} className="card elev-sm" style={{ padding: 'var(--space-4)' }}>
                <div style={{ marginBottom: 12 }}>{a.icon}</div>
                <div style={{ fontSize: '0.95rem', fontWeight: 600, marginBottom: 6 }}>{a.title}</div>
                <div style={{ fontSize: '0.82rem', opacity: 0.7, lineHeight: 1.55 }}>{a.desc}</div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ---------- Lifecycle ---------- */}
      <section id="lifecycle" style={{ maxWidth: 1400, margin: '0 auto', padding: 'var(--space-8)' }}>
        <div className="tag tag-outline" style={{ marginBottom: 14 }}>
          {t('landing.lifecycleEyebrow')}
        </div>
        <h2 style={{ fontWeight: 400, marginBottom: 40 }}>{t('landing.lifecycleTitle')}</h2>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 0, position: 'relative' }}>
          <div
            style={{
              position: 'absolute',
              top: 13,
              left: '12.5%',
              right: '12.5%',
              height: 1.5,
              background: 'var(--color-divider)',
            }}
          />
          {LIFECYCLE.map((step, i) => (
            <div key={step.title} style={{ textAlign: 'center', position: 'relative' }}>
              <div
                style={{
                  width: 26,
                  height: 26,
                  borderRadius: '50%',
                  background: i === 3 ? 'var(--color-accent-700)' : 'var(--color-bg)',
                  border: `1.5px solid var(--color-accent-700)`,
                  color: i === 3 ? 'var(--color-neutral-100)' : 'var(--color-accent-700)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  fontSize: 12,
                  margin: '0 auto 14px',
                  position: 'relative',
                  zIndex: 1,
                  fontFamily: 'var(--font-heading)',
                  fontWeight: 600,
                }}
              >
                {i + 1}
              </div>
              <div style={{ fontSize: 13, opacity: 0.55, marginBottom: 6 }}>{step.day}</div>
              <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 6 }}>{step.title}</div>
              <div style={{ fontSize: 13, opacity: 0.7, lineHeight: 1.55, maxWidth: '22ch', margin: '0 auto' }}>{step.desc}</div>
            </div>
          ))}
        </div>
      </section>

      {/* ---------- Metrics (dark) ---------- */}
      <section id="metrics" style={{ background: 'var(--color-neutral-900)', color: 'var(--color-neutral-100)', padding: 'var(--space-8)' }}>
        <div style={{ maxWidth: 1400, margin: '0 auto' }}>
          <div className="tag tag-outline" style={{ marginBottom: 14, borderColor: 'var(--color-accent-300)', color: 'var(--color-accent-300)' }}>
            {t('landing.metricsEyebrow')}
          </div>
          <h2 style={{ fontWeight: 400, marginBottom: 36, color: 'var(--color-neutral-100)' }}>{t('landing.metricsTitle')}</h2>
          <div style={{ display: 'grid', gridTemplateColumns: '1.1fr 0.9fr', gap: 56, alignItems: 'center' }} className="metrics-grid-responsive">
            <div>
              <div style={{ fontSize: '0.8rem', opacity: 0.6, marginBottom: 14, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                {t('landing.medianLabel')}
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
                {t('landing.metricBars').map((m, i) => (
                  <MetricBar key={m.label} label={m.label} value={m.value} width={['3%', '7%', '100%'][i]} muted={i === 2} />
                ))}
              </div>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20 }}>
              {t('landing.statTiles').map((s) => (
                <StatTile key={s.label} value={s.value} label={s.label} />
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* ---------- Legal framework ---------- */}
      <section id="legal" style={{ padding: 'var(--space-8)' }}>
        <div style={{ maxWidth: 1400, margin: '0 auto' }}>
          <div className="tag tag-outline" style={{ marginBottom: 14 }}>
            {t('landing.legalEyebrow')}
          </div>
          <h2 style={{ fontWeight: 400, marginBottom: 8 }}>{t('landing.legalTitle')}</h2>
          <p style={{ fontSize: '0.92rem', lineHeight: 1.7, opacity: 0.8, maxWidth: '80ch', marginBottom: 26 }}>
            {t('landing.legalLedePre')} <strong>{t('landing.legalLedeBold')}</strong>{t('landing.legalLedePost')}
          </p>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 16 }}>
            {LEGAL.map((l) => (
              <div key={l.title} style={{ border: '1px solid var(--color-divider)', borderRadius: 'var(--radius-md)', padding: 'var(--space-4)' }}>
                <strong style={{ fontSize: '0.86rem' }}>{l.title}</strong>
                <div style={{ fontSize: '0.8rem', opacity: 0.6, marginTop: 4 }}>{l.sub}</div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ---------- Advisory panel ---------- */}
      <section id="advisory" style={{ background: 'var(--color-surface)', padding: 'var(--space-8)' }}>
        <div style={{ maxWidth: 1400, margin: '0 auto' }}>
          <div className="tag tag-outline" style={{ marginBottom: 14 }}>
            {t('landing.advisoryEyebrow')}
          </div>
          <h2 style={{ fontWeight: 400, marginBottom: 8 }}>{t('landing.advisoryTitle')}</h2>
          <p style={{ fontSize: '0.82rem', opacity: 0.55, marginBottom: 32 }}>
            {t('landing.advisoryNote')}
          </p>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 28 }}>
            {ADVISORS.map((a) => (
              <div key={a.initials} style={{ display: 'flex', gap: 16, alignItems: 'center' }}>
                <div
                  style={{
                    width: 56,
                    height: 56,
                    borderRadius: '50%',
                    background: 'var(--color-neutral-900)',
                    color: 'var(--color-neutral-100)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    fontFamily: 'var(--font-heading)',
                    fontSize: 18,
                    fontWeight: 600,
                    flexShrink: 0,
                  }}
                >
                  {a.initials}
                </div>
                <div>
                  <div style={{ fontSize: 15, fontWeight: 600 }}>{a.name}</div>
                  <div style={{ fontSize: '0.8rem', opacity: 0.6, marginTop: 2 }}>{t('landing.advisoryRole')}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ---------- Closing CTA ---------- */}
      <section style={{ padding: 'var(--space-8)', borderTop: '1px solid var(--color-divider)' }}>
        <div style={{ maxWidth: 1400, margin: '0 auto', textAlign: 'center' }}>
          <h2 style={{ fontWeight: 400, marginBottom: 14 }}>{t('landing.ctaTitle')}</h2>
          <p style={{ fontSize: '0.95rem', opacity: 0.7, margin: '0 auto 26px', maxWidth: '60ch' }}>
            {t('landing.ctaLede')}
          </p>
          <button className="btn btn-primary btn-lg" onClick={goFileDispute}>
            {t('landing.ctaFile')}
          </button>
        </div>
      </section>

      <footer style={{ background: 'var(--color-neutral-900)', color: 'var(--color-neutral-300)', padding: 'var(--space-6) var(--space-8)', fontSize: 13 }}>
        <div style={{ maxWidth: 1400, margin: '0 auto', display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: 20 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <Logo width={18} height={18} />
            {t('landing.footerAddr')}
          </div>
          <div>{t('landing.footerGrievance')}</div>
          <div>{t('landing.footerCopyright')}</div>
        </div>
      </footer>
    </div>
  )
}

function MetricBar({ label, value, width, muted }) {
  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.84rem', marginBottom: 5 }}>
        <span>{label}</span>
        <strong style={{ fontFamily: 'var(--font-heading)' }}>{value}</strong>
      </div>
      <div style={{ height: 8, background: 'var(--color-neutral-700)', borderRadius: 2, overflow: 'hidden' }}>
        <div style={{ width, height: '100%', background: muted ? 'var(--color-neutral-500)' : 'var(--color-accent-400)' }} />
      </div>
    </div>
  )
}

function StatTile({ value, label }) {
  return (
    <div style={{ border: '1px solid var(--color-neutral-700)', borderRadius: 'var(--radius-md)', padding: 'var(--space-4)' }}>
      <div style={{ fontFamily: 'var(--font-heading)', fontSize: 32, fontWeight: 600, color: 'var(--color-accent-300)' }}>{value}</div>
      <div style={{ fontSize: '0.78rem', opacity: 0.65, marginTop: 4 }}>{label}</div>
    </div>
  )
}

const PIPELINE_STATES = ['done', 'running', 'pending', 'pending']

function PipelinePreview({ steps: rawSteps }) {
  const steps = rawSteps.map((s, i) => ({ ...s, state: PIPELINE_STATES[i] }))
  return (
    <div style={{ display: 'flex', flexDirection: 'column' }}>
      {steps.map((s, i) => (
        <div key={s.title} style={{ display: 'flex', gap: 14, opacity: s.state === 'pending' ? 0.5 : 1 }}>
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
            {s.state === 'done' && (
              <div
                style={{
                  width: 26,
                  height: 26,
                  borderRadius: '50%',
                  background: 'var(--color-accent-700)',
                  color: 'var(--color-neutral-100)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  fontSize: '0.72rem',
                  flexShrink: 0,
                }}
              >
                ✓
              </div>
            )}
            {s.state === 'running' && (
              <div
                style={{
                  width: 26,
                  height: 26,
                  borderRadius: '50%',
                  border: '2px solid var(--color-accent)',
                  flexShrink: 0,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                }}
              >
                <div className="spinner" />
              </div>
            )}
            {s.state === 'pending' && (
              <div style={{ width: 26, height: 26, borderRadius: '50%', border: '2px solid var(--color-divider)', flexShrink: 0 }} />
            )}
            {i < steps.length - 1 && <div style={{ width: 1.5, flex: 1, background: 'var(--color-divider)', minHeight: 26 }} />}
          </div>
          <div style={{ paddingBottom: 18 }}>
            <div style={{ fontSize: '0.92rem', fontWeight: 600 }}>{s.title}</div>
            <div style={{ fontSize: '0.8rem', opacity: 0.65, marginTop: 2 }}>{s.detail}</div>
          </div>
        </div>
      ))}
    </div>
  )
}
