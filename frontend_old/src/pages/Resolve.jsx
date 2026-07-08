import { useEffect, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import { api, streamSSE } from '../api.js'
import { AGENT_ICONS, Check, Clock, Handshake, Gavel } from '../icons.jsx'
import Stepper from '../components/Stepper.jsx'
import ResolutionDoc from '../components/ResolutionDoc.jsx'

const AGENT_ORDER = ['orchestrator', 'ingestion', 'research', 'analysis', 'mediation', 'resolution']

export default function Resolve() {
  const { id } = useParams()
  const [caseData, setCaseData] = useState(null)
  const [agents, setAgents] = useState({}) // agent -> {status, detail, payload, title}
  const [notices, setNotices] = useState([]) // routing / escalation / loop decisions
  const [draft, setDraft] = useState('') // live streamed resolution findings
  const [phase, setPhase] = useState('loading') // loading|pipeline|mediation|resolving|resolved
  const [error, setError] = useState(null)
  const startedRef = useRef(false)
  const cursorRef = useRef(0)
  const abortRef = useRef(null)

  useEffect(() => {
    if (startedRef.current) return
    startedRef.current = true
    ;(async () => {
      try {
        const c = await api.getCase(id)
        setCaseData(c)
        if (c.status === 'awaiting_response' || c.status === 'ready') {
          await api.runPipeline(id)
        }
        if (c.status === 'resolved') setPhase('resolved')
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
    abortRef.current = streamSSE(`/cases/${id}/events?after=${after}`, {
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
        api.getCase(id).then(setCaseData).catch(() => {})
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
    <section className="page fade-in">
      <Stepper current={3} />
      <div className="page-head between flex">
        <div>
          <h2>AI Resolution in progress</h2>
          <p>
            Five specialised agents, coordinated by an orchestrator, are resolving case{' '}
            <strong>{id}</strong> in real time.
          </p>
        </div>
        {phase === 'resolved' && (
          <span className="resolved-badge">
            <Check width={16} height={16} /> Case resolved
          </span>
        )}
      </div>

      {error && (
        <div className="error-banner">
          <strong>Something interrupted the workflow.</strong> {error}
          <button className="btn btn-small" onClick={() => { setError(null); openStream(cursorRef.current) }}>
            Reconnect
          </button>
        </div>
      )}

      <div className="theatre">
        <div className="agent-stream" style={{ maxHeight: 640, overflowY: 'auto', paddingRight: 4 }}>
          {agents.orchestrator && <AgentRow agent="orchestrator" data={agents.orchestrator} />}

          {notices.map((n, i) => (
            <NoticeRow key={i} notice={n} />
          ))}

          {visibleAgents
            .filter((a) => a !== 'orchestrator')
            .map((a) => (
              <AgentRow key={a} agent={a} data={agents[a]} draft={a === 'resolution' && resolving ? draft : ''} />
            ))}

          {phase === 'mediation' && mediation && <MediationPanel proposal={mediation} onDecide={decide} />}

          {(phase === 'resolving' || phase === 'resolved') && resolution && <ResolutionDoc doc={resolution} />}
        </div>

        <SidePanel caseData={caseData} phase={phase} resolution={resolution} />
      </div>
    </section>
  )
}

function NoticeRow({ notice }) {
  const meta = {
    routing: { label: 'Orchestrator · Routing', cls: 'notice-route' },
    escalated: { label: 'Orchestrator · Escalation', cls: 'notice-escalate' },
    loop: { label: 'Orchestrator · Loop-back', cls: 'notice-loop' },
  }[notice.kind] || { label: 'Orchestrator', cls: '' }
  return (
    <div className={`notice-row ${meta.cls}`}>
      <span className="notice-label">{meta.label}</span>
      <span className="notice-detail">{notice.detail}</span>
    </div>
  )
}

function AgentRow({ agent, data, draft }) {
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
            <span className="llm-tag">✦ Drafting live…</span>
            {draft}
            <span className="caret" />
          </div>
        )}
        {done && <AgentPayload agent={agent} payload={data.payload} />}
      </div>
    </div>
  )
}

function EngineTag({ engine }) {
  if (engine === 'llm') return <span className="engine-tag llm">✦ LLM</span>
  if (engine === 'semantic') return <span className="engine-tag">semantic</span>
  return null
}

function AgentPayload({ agent, payload }) {
  if (!payload) return null

  if (agent === 'ingestion') {
    return (
      <div className="payload">
        <div className="chips">
          <span className="chip gold">{payload.dispute_subtype}</span>
          <span className="chip">{payload.claim_amount_display}</span>
          <span className="chip">{payload.evidence_count} evidence items</span>
          <span className="chip">{Math.round((payload.confidence || 0) * 100)}% confidence</span>
          {payload.recommended_tier === 1 ? (
            <span className="chip green">Tier 1 eligible ✓</span>
          ) : (
            <span className="chip amber">Recommends Tier 2 review</span>
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
          {payload.precedents?.length} precedents · {payload.method === 'semantic' ? 'semantic vector search' : 'keyword search'} · coverage {payload.coverage_label}
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
            <span className="llm-tag">{payload.engine === 'llm' ? '✦ AI neutral summary' : 'Neutral summary'}</span>
            {payload.neutral_summary}
          </div>
        )}
        <div className="chips">
          <span className="chip">Claimant: <strong style={{ marginLeft: 4 }}>{payload.strength?.claimant}</strong></span>
          <span className="chip">Respondent: <strong style={{ marginLeft: 4 }}>{payload.strength?.respondent}</strong></span>
        </div>
        {payload.contradictions?.length > 0 && (
          <div>
            <div className="mini-head">Contradictions flagged</div>
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
            Validator: {payload.validator_notes.join(' ')}
          </div>
        )}
      </div>
    )
  }

  return null
}

function MediationPanel({ proposal, onDecide }) {
  return (
    <div className="mediation-banner fade-in">
      <h3>
        <Handshake width={22} height={22} /> Agent 4 · Mediation Proposal
      </h3>
      <p className="headline-big">{proposal.headline}</p>
      {proposal.explanation && (
        <div className="llm-quote">
          <span className="llm-tag">{proposal.engine === 'llm' ? '✦ AI recommendation' : 'Recommendation'}</span>
          {proposal.explanation}
        </div>
      )}
      <div className="flex between" style={{ alignItems: 'flex-end' }}>
        <div>
          <div className="muted" style={{ fontSize: '0.8rem' }}>Recommended settlement</div>
          <div className="proposal-amount">{proposal.amount_display}</div>
          <div className="muted" style={{ fontSize: '0.85rem' }}>
            {proposal.type?.replace(/_/g, ' ')} · within {proposal.compliance_days} days
          </div>
        </div>
        <span className="chip gold">{proposal.pct_full_relief}% full-relief rate</span>
      </div>
      <ul className="rationale">
        {proposal.rationale?.map((r, i) => (
          <li key={i}>{r}</li>
        ))}
      </ul>
      <div className="decision-row">
        <button className="btn btn-primary" onClick={() => onDecide(true)}>
          <Check width={16} height={16} /> Accept mediation
        </button>
        <button className="btn" onClick={() => onDecide(false)}>
          <Gavel width={16} height={16} /> Decline — issue AI resolution
        </button>
      </div>
    </div>
  )
}

function SidePanel({ caseData, phase, resolution }) {
  if (!caseData) return <div className="side" />
  const statusLabel = {
    loading: 'Loading',
    pipeline: 'Agents working',
    mediation: 'Awaiting decision',
    resolving: 'Drafting order',
    resolved: 'Resolved',
  }[phase]
  return (
    <div className="side">
      <div className="card card-pad">
        <div className="mini-head">Case file</div>
        <div className="kv"><span className="k">Case ID</span><span className="v">{caseData.case_id}</span></div>
        <div className="kv"><span className="k">Status</span><span className="v" style={{ color: phase === 'resolved' ? 'var(--green)' : 'var(--gold)' }}>{statusLabel}</span></div>
        <div className="kv"><span className="k">Tier</span><span className="v">Tier {caseData.tier}</span></div>
        <div className="kv"><span className="k">Claimant</span><span className="v">{caseData.claimant?.name}</span></div>
        <div className="kv"><span className="k">Respondent</span><span className="v">{caseData.respondent?.name}</span></div>
        <div className="kv"><span className="k">Claim amount</span><span className="v">₹{Number(caseData.claim_amount).toLocaleString('en-IN')}</span></div>
        <div className="kv"><span className="k">Evidence</span><span className="v">{caseData.evidence?.length} items</span></div>
        <div className="kv"><span className="k">Response</span><span className="v">{caseData.respondent_submission ? 'Filed' : 'Uncontested'}</span></div>
      </div>

      {resolution && (
        <div className="card card-pad">
          <div className="mini-head">Compliance monitor</div>
          <div className="compliance">
            <Clock width={26} height={26} />
            <div>
              <div className="cd">{resolution.compliance_days} days</div>
              <div className="muted" style={{ fontSize: '0.8rem' }}>Deadline {resolution.compliance_deadline}</div>
            </div>
          </div>
          {resolution.requires_human_signoff && (
            <p className="muted" style={{ fontSize: '0.82rem', marginTop: 12, color: 'var(--gold)' }}>
              Tier 2 — order is provisional pending human counter-signature.
            </p>
          )}
          <p className="muted" style={{ fontSize: '0.82rem', marginTop: 12 }}>
            Auto-escalation notice generated if the respondent fails to comply by the deadline.
          </p>
        </div>
      )}

      <div className="card card-pad">
        <div className="mini-head">Time saved</div>
        <p style={{ fontSize: '0.9rem' }}>
          <strong style={{ color: 'var(--gold)' }}>~4 minutes</strong> here vs.{' '}
          <strong>6–24 months</strong> in a Consumer Forum today.
        </p>
      </div>
    </div>
  )
}
