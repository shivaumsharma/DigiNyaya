import { useEffect, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import { api, streamSSE } from '../api.js'
import { useLanguage } from '../i18n/LanguageContext.jsx'
import { AGENT_ICONS, Check, Clock, Handshake, Gavel, Shield } from '../icons.jsx'
import Stepper from '../components/Stepper.jsx'
import ResolutionDoc from '../components/ResolutionDoc.jsx'

const AGENT_ORDER = ['orchestrator', 'ingestion', 'research', 'analysis', 'mediation', 'resolution']

export default function Resolve() {
  const { id } = useParams()
  const { lang, t } = useLanguage()
  const [caseData, setCaseData] = useState(null)
  const [agents, setAgents] = useState({}) // agent -> {status, detail, payload, title}
  const [notices, setNotices] = useState([]) // routing / escalation / loop decisions
  const [draft, setDraft] = useState('') // live streamed resolution findings
  const [phase, setPhase] = useState('loading') // loading|pipeline|mediation|resolving|resolved|escalated
  const [escalation, setEscalation] = useState(null)
  const [error, setError] = useState(null)
  const startedRef = useRef(false)
  const cursorRef = useRef(0)
  const abortRef = useRef(null)

  useEffect(() => {
    if (startedRef.current) return
    startedRef.current = true
    ;(async () => {
      try {
        const c = await api.getCase(id, lang)
        setCaseData(c)
        if (c.status === 'awaiting_response' || c.status === 'ready') {
          await api.runPipeline(id)
        }
        if (c.status === 'resolved') setPhase('resolved')
        else if (c.status === 'escalated') {
          setPhase('escalated')
          setEscalation(c.escalation || null)
        }
        openStream(0)
      } catch (e) {
        setError(e.message || 'Failed to load case')
      }
    })()
    return () => abortRef.current && abortRef.current()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id])

  function openStream(after) {
    if (abortRef.current) abortRef.current()
    const langParam = lang ? `&lang=${encodeURIComponent(lang)}` : ''
    abortRef.current = streamSSE(`/cases/${id}/events?after=${after}${langParam}`, {
      onEvent: applyEvent,
      onError: (e) => setError(e.message),
    })
  }

  function applyEvent(ev) {
    if (typeof ev.seq === 'number') cursorRef.current = Math.max(cursorRef.current, ev.seq)

    switch (ev.type) {
      case 'token':
        setDraft((d) => d + (ev.payload?.delta || ''))
        return
      case 'routing':
      case 'escalated':
      case 'loop':
        setNotices((n) => [...n, { kind: ev.type, detail: ev.detail, payload: ev.payload }])
        if (ev.type === 'routing' && ev.payload) {
          setCaseData((c) => (c ? { ...c, tier: ev.payload.tier, tier_label: ev.payload.tier_label } : c))
        }
        scrollBottom()
        return
      case 'awaiting_decision':
        setPhase((p) => (p === 'resolving' || p === 'resolved' ? p : 'mediation'))
        scrollBottom()
        return
      case 'resolved':
        setPhase('resolved')
        api.getCase(id, lang).then(setCaseData).catch(() => {})
        scrollBottom()
        return
      case 'escalated_terminal':
        setPhase('escalated')
        setEscalation(ev.payload || null)
        api.getCase(id, lang).then(setCaseData).catch(() => {})
        scrollBottom()
        return
      case 'error':
        setError(ev.detail)
        return
      case 'orchestrator':
      case 'agent':
      default:
        setAgents((prev) => ({
          ...prev,
          [ev.agent]: {
            title: ev.title,
            status: ev.status,
            detail: ev.detail,
            payload: ev.payload && Object.keys(ev.payload).length ? ev.payload : prev[ev.agent]?.payload,
          },
        }))
        if (phase === 'loading') setPhase('pipeline')
        scrollBottom()
    }
  }

  function scrollBottom() {
    requestAnimationFrame(() => {
      const el = document.querySelector('.agent-stream')
      if (el) el.scrollTop = el.scrollHeight
    })
  }

  async function decide(accept) {
    try {
      setDraft('')
      await api.mediationDecision(id, accept)
      setPhase('resolving')
      openStream(cursorRef.current)
    } catch (e) {
      setError(e.message)
    }
  }

  const mediation = agents.mediation?.status === 'done' ? agents.mediation.payload : null
  const resolution = agents.resolution?.status === 'done' ? agents.resolution.payload : null
  const visibleAgents = AGENT_ORDER.filter((a) => agents[a])
  const resolving = phase === 'resolving' && !resolution

  return (
    <section className="page fade-in container">
      <Stepper current={3} />
      <div className="page-head between flex">
        <div>
          <h1 style={{ fontWeight: 400, marginBottom: 10 }}>{t('resolve.title')}</h1>
          <p style={{ fontSize: '0.95rem' }}>{t('resolve.subtitle', { id })}</p>
        </div>
        {phase === 'resolved' && (
          <span className="resolved-badge">
            <Check width={16} height={16} /> {t('resolve.resolvedBadge')}
          </span>
        )}
        {phase === 'escalated' && (
          <span className="tag tag-outline">
            <Shield width={14} height={14} /> {t('resolve.escalatedBadge')}
          </span>
        )}
      </div>

      {error && (
        <div className="error-banner">
          <strong>{t('resolve.errorTitle')}</strong> {error}
          <button className="btn btn-small" onClick={() => { setError(null); openStream(cursorRef.current) }}>
            {t('resolve.reconnect')}
          </button>
        </div>
      )}

      <div className="theatre">
        <div className="agent-stream" style={{ maxHeight: 640, overflowY: 'auto', paddingRight: 4 }}>
          {agents.orchestrator && <AgentRow agent="orchestrator" data={agents.orchestrator} t={t} />}

          {notices.map((n, i) => (
            <NoticeRow key={i} notice={n} t={t} />
          ))}

          {visibleAgents
            .filter((a) => a !== 'orchestrator')
            .map((a) => (
              <AgentRow key={a} agent={a} data={agents[a]} draft={a === 'resolution' && resolving ? draft : ''} t={t} />
            ))}

          {phase === 'escalated' && <EscalationPanel escalation={escalation} t={t} />}

          {phase === 'mediation' && mediation && <MediationPanel proposal={mediation} onDecide={decide} t={t} />}

          {(phase === 'resolving' || phase === 'resolved') && resolution && <ResolutionDoc doc={resolution} />}
        </div>

        <SidePanel caseData={caseData} phase={phase} resolution={resolution} t={t} />
      </div>
    </section>
  )
}

function NoticeRow({ notice, t }) {
  const meta = {
    routing: { label: t('resolve.notice.routing'), cls: 'notice-route' },
    escalated: { label: t('resolve.notice.escalated'), cls: 'notice-escalate' },
    loop: { label: t('resolve.notice.loop'), cls: 'notice-loop' },
  }[notice.kind] || { label: t('resolve.notice.default'), cls: '' }
  return (
    <div className={`notice-row ${meta.cls}`}>
      <span className="notice-label">{meta.label}</span>
      <span className="notice-detail">{notice.detail}</span>
    </div>
  )
}

function AgentRow({ agent, data, draft, t }) {
  const Icon = AGENT_ICONS[agent]
  const running = data.status === 'running'
  const done = data.status === 'done'
  return (
    <div className={`agent-row visible ${running ? 'running' : ''} ${done ? 'done' : ''}`}>
      <div className="agent-icon">
        {running ? <div className="spinner" /> : done && agent !== 'orchestrator' ? <Check width={18} height={18} /> : <Icon width={20} height={20} />}
      </div>
      <div className="agent-body">
        <div className="agent-title">
          {data.title}
          <span className={`agent-status ${data.status}`}>{data.status}</span>
          {data.payload?.engine && <EngineTag engine={data.payload.engine} />}
        </div>
        <div className="agent-detail">{data.detail}</div>
        {draft && (
          <div className="llm-quote streaming">
            <span className="llm-tag">{t('resolve.draftingLive')}</span>
            {draft}
            <span className="caret" />
          </div>
        )}
        {done && <AgentPayload agent={agent} payload={data.payload} t={t} />}
      </div>
    </div>
  )
}

function EngineTag({ engine }) {
  if (engine === 'llm') return <span className="engine-tag llm">✦ LLM</span>
  if (engine === 'semantic') return <span className="engine-tag">semantic</span>
  return null
}

function AgentPayload({ agent, payload, t }) {
  if (!payload) return null

  if (agent === 'ingestion') {
    return (
      <div className="payload">
        <div className="chips">
          <span className="chip gold">{payload.dispute_subtype}</span>
          <span className="chip">{payload.claim_amount_display}</span>
          <span className="chip">{t('resolve.ingestion.evidenceItems', { n: payload.evidence_count })}</span>
          <span className="chip">{t('resolve.ingestion.confidence', { pct: Math.round((payload.confidence || 0) * 100) })}</span>
          {payload.recommended_tier === 1 ? (
            <span className="chip green">{t('resolve.ingestion.tier1Eligible')}</span>
          ) : (
            <span className="chip amber">{t('resolve.ingestion.tier2Review')}</span>
          )}
        </div>
        {payload.signals?.length > 0 && (
          <div className="chips">
            {payload.signals.slice(0, 6).map((s) => (
              <span className="chip" key={s}>{s.replace(/_/g, ' ')}</span>
            ))}
          </div>
        )}
      </div>
    )
  }

  if (agent === 'research') {
    return (
      <div className="payload">
        <div className="mini-head">
          {t('resolve.research.summary', {
            count: payload.precedents?.length,
            method: payload.method === 'semantic' ? t('resolve.research.semantic') : t('resolve.research.keyword'),
            label: payload.coverage_label,
          })}
        </div>
        {payload.precedents?.slice(0, 4).map((p) => (
          <div className="prec-item" key={p.id}>
            <div className="pt">
              <span>{p.title}</span>
              <span className="rel">{p.relevance}%</span>
            </div>
            <div className="pc">{p.citation} · {p.outcome_detail}</div>
          </div>
        ))}
      </div>
    )
  }

  if (agent === 'analysis') {
    return (
      <div className="payload">
        {payload.neutral_summary && (
          <div className="llm-quote">
            <span className="llm-tag">{payload.engine === 'llm' ? t('resolve.analysis.aiSummaryTag') : t('resolve.analysis.summaryTag')}</span>
            {payload.neutral_summary}
          </div>
        )}
        <div className="chips">
          <span className="chip">{t('resolve.analysis.claimantLabel')} <strong style={{ marginLeft: 4 }}>{payload.strength?.claimant}</strong></span>
          <span className="chip">{t('resolve.analysis.respondentLabel')} <strong style={{ marginLeft: 4 }}>{payload.strength?.respondent}</strong></span>
        </div>
        {payload.contradictions?.length > 0 && (
          <div>
            <div className="mini-head">{t('resolve.analysis.contradictions')}</div>
            <div className="chips">
              {payload.contradictions.map((c, i) => (
                <span className="chip danger" key={i}>{c}</span>
              ))}
            </div>
          </div>
        )}
      </div>
    )
  }

  if (agent === 'mediation') {
    return (
      <div className="payload">
        {payload.validator_notes?.length > 0 && (
          <div className="mini-head" style={{ color: 'var(--gold)' }}>
            {t('resolve.mediation.validatorLabel')} {payload.validator_notes.join(' ')}
          </div>
        )}
      </div>
    )
  }

  return null
}

function EscalationPanel({ escalation, t }) {
  return (
    <div className="mediation-banner fade-in" style={{ borderColor: 'var(--color-divider)' }}>
      <h3>
        <Shield width={22} height={22} /> {t('resolve.escalatedTitle')}
      </h3>
      <p className="headline-big">
        {escalation?.user_message || t('resolve.escalatedDefaultMessage')}
      </p>
      {escalation?.checkpoint && (
        <div className="chips">
          <span className="chip">
            {t('resolve.escalated.checkpointLabel')}{' '}
            {escalation.checkpoint === 'pre_filter' ? t('resolve.escalated.checkpointPre') : t('resolve.escalated.checkpointPost')}
          </span>
          {escalation.case_id && <span className="chip">{t('resolve.escalated.caseLabel')} {escalation.case_id}</span>}
        </div>
      )}
      {escalation?.details && Object.keys(escalation.details).length > 0 && (
        <div style={{ marginTop: 14 }}>
          <p style={{ fontSize: '0.8rem', fontWeight: 600, color: 'var(--text-dim)', marginBottom: 6 }}>
            {t('resolve.escalated.reasonLabel')}
          </p>
          <ul style={{ margin: 0, paddingLeft: 20, fontSize: '0.88rem', lineHeight: 1.6 }}>
            {Object.values(escalation.details).map((reason, i) => (
              <li key={i}>{reason}</li>
            ))}
          </ul>
        </div>
      )}
      <p className="muted" style={{ fontSize: '0.85rem', marginTop: 12 }}>
        {t('resolve.escalated.reviewNotice')}
      </p>
    </div>
  )
}

function MediationPanel({ proposal, onDecide, t }) {
  return (
    <div className="mediation-banner fade-in">
      <h3>
        <Handshake width={22} height={22} /> {t('resolve.mediation.panelTitle')}
      </h3>
      <p className="headline-big">{proposal.headline}</p>
      {proposal.explanation && (
        <div className="llm-quote">
          <span className="llm-tag">{proposal.engine === 'llm' ? t('resolve.mediation.aiRecommendation') : t('resolve.mediation.recommendation')}</span>
          {proposal.explanation}
        </div>
      )}
      <div className="flex between" style={{ alignItems: 'flex-end' }}>
        <div>
          <div className="muted" style={{ fontSize: '0.8rem' }}>{t('resolve.mediation.recommendedSettlement')}</div>
          <div className="proposal-amount">{proposal.amount_display}</div>
          <div className="muted" style={{ fontSize: '0.85rem' }}>
            {proposal.type?.replace(/_/g, ' ')} · {t('resolve.mediation.withinDays', { days: proposal.compliance_days })}
          </div>
        </div>
        <span className="chip gold">{t('resolve.mediation.fullReliefRate', { pct: proposal.pct_full_relief })}</span>
      </div>
      <ul className="rationale">
        {proposal.rationale?.map((r, i) => (
          <li key={i}>{r}</li>
        ))}
      </ul>
      <div className="decision-row">
        <button className="btn btn-primary" onClick={() => onDecide(true)}>
          <Check width={16} height={16} /> {t('resolve.mediation.accept')}
        </button>
        <button className="btn" onClick={() => onDecide(false)}>
          <Gavel width={16} height={16} /> {t('resolve.mediation.decline')}
        </button>
      </div>
    </div>
  )
}

function SidePanel({ caseData, phase, resolution, t }) {
  if (!caseData) return <div className="side" />
  const statusLabel = {
    loading: t('resolve.status.loading'),
    pipeline: t('resolve.status.pipeline'),
    mediation: t('resolve.status.mediation'),
    resolving: t('resolve.status.resolving'),
    resolved: t('resolve.status.resolved'),
    escalated: t('resolve.status.escalated'),
  }[phase]
  return (
    <div className="side">
      <div className="card card-pad">
        <div className="mini-head">{t('resolve.side.caseFile')}</div>
        <div className="kv"><span className="k">{t('resolve.side.caseId')}</span><span className="v">{caseData.case_id}</span></div>
        <div className="kv"><span className="k">{t('resolve.side.status')}</span><span className="v" style={{ color: phase === 'resolved' ? 'var(--green)' : phase === 'escalated' ? 'var(--text-dim)' : 'var(--gold)' }}>{statusLabel}</span></div>
        <div className="kv"><span className="k">{t('resolve.side.tierLabel')}</span><span className="v">{t('resolve.side.tier', { n: caseData.tier })}</span></div>
        <div className="kv"><span className="k">{t('resolve.side.claimant')}</span><span className="v">{caseData.claimant?.name}</span></div>
        <div className="kv"><span className="k">{t('resolve.side.respondent')}</span><span className="v">{caseData.respondent?.name}</span></div>
        <div className="kv"><span className="k">{t('resolve.side.claimAmount')}</span><span className="v">₹{Number(caseData.claim_amount).toLocaleString('en-IN')}</span></div>
        <div className="kv"><span className="k">{t('resolve.side.evidence')}</span><span className="v">{t('resolve.side.itemsCount', { n: caseData.evidence?.length })}</span></div>
        <div className="kv"><span className="k">{t('resolve.side.response')}</span><span className="v">{caseData.respondent_submission ? t('resolve.side.filed') : t('resolve.side.uncontested')}</span></div>
      </div>

      {resolution && (
        <div className="card card-pad">
          <div className="mini-head">{t('resolve.side.complianceMonitor')}</div>
          <div className="compliance">
            <Clock width={26} height={26} />
            <div>
              <div className="cd">{t('resolve.side.complianceDays', { n: resolution.compliance_days })}</div>
              <div className="muted" style={{ fontSize: '0.8rem' }}>{t('resolve.side.deadline', { date: resolution.compliance_deadline })}</div>
            </div>
          </div>
          {resolution.requires_human_signoff && (
            <p className="muted" style={{ fontSize: '0.82rem', marginTop: 12, color: 'var(--gold)' }}>
              {t('resolve.side.tier2Notice')}
            </p>
          )}
          <p className="muted" style={{ fontSize: '0.82rem', marginTop: 12 }}>
            {t('resolve.side.autoEscalation')}
          </p>
        </div>
      )}

      <div className="card card-pad">
        <div className="mini-head">{t('resolve.side.timeSaved')}</div>
        <p style={{ fontSize: '0.9rem' }}>
          {t('resolve.side.timeSavedTemplate', {
            here: t('resolve.side.timeSavedHere'),
            vs: t('resolve.side.timeSavedVs'),
          })}
        </p>
      </div>
    </div>
  )
}
