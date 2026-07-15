"""The Orchestrator — a hand-rolled state machine over the agents.

Unlike a linear pipeline, this graph makes real decisions:
  • ROUTE uses Agent 1's confidence to choose Tier 1 (autonomous) vs. a genuine
    Tier 2 escalation (human sign-off required) — and that choice changes the
    final order.
  • A LOOP edge lets Analysis send the workflow back to Research with a broadened
    query when precedent coverage is thin (capped to avoid cycles).
  • A PAUSE node yields control for the human mediation decision, then resumes.

Nodes are generators that mutate the shared CaseContext and yield events. A tiny
runner follows the transition table until it hits a pause or terminal state.
"""

from __future__ import annotations

from typing import Callable, Iterator

from .. import llm
from ..agents import analysis, ingestion, mediation, research, resolution
from .context import CaseContext, RouteDecision
from .events import make_event

TITLES = {
    "orchestrator": "Orchestrator",
    "ingestion": "Agent 1 · Case Ingestion",
    "research": "Agent 2 · Precedent Research",
    "analysis": "Agent 3 · Argument Analysis",
    "mediation": "Agent 4 · Mediation",
    "resolution": "Agent 5 · Resolution Drafting",
}

_TIER2_LABEL = "Tier 2 — AI-Assisted Virtual Judge"
_TIER1_LABEL = "Tier 1 — Fully Autonomous AI Resolution"


def _running(agent: str, detail: str) -> dict:
    return make_event("agent", agent=agent, title=TITLES[agent], status="running", detail=detail)


def _done(agent: str, result) -> dict:
    payload = result.output.model_dump() if result.output else {}
    payload["engine"] = result.engine
    payload["confidence"] = result.confidence
    return make_event("agent", agent=agent, title=TITLES[agent], status="done", detail=result.detail, payload=payload)


# ----------------------------- nodes ----------------------------- #
def ingest_node(ctx: CaseContext) -> Iterator[dict]:
    yield _running("ingestion", "Reading claim narrative & evidence…")
    res = ingestion.run(ctx)
    ctx.ingestion = res.output
    yield _done("ingestion", res)


def route_node(ctx: CaseContext) -> Iterator[dict]:
    ing = ctx.ingestion
    tier = ing.recommended_tier if ing else 1
    autonomous = tier == 1
    label = _TIER1_LABEL if autonomous else _TIER2_LABEL
    ctx.route = RouteDecision(
        tier=tier,
        tier_label=label,
        autonomous=autonomous,
        requires_human_signoff=not autonomous,
        reason=ing.reasoning if ing else "",
    )
    ctx.tier, ctx.tier_label = tier, label
    yield make_event(
        "routing",
        detail=(
            f"Confidence {int((ing.confidence if ing else 0) * 100)}% — routed to {label}."
            if autonomous
            else f"Confidence {int((ing.confidence if ing else 0)*100)}% below autonomy threshold — escalating."
        ),
        payload={"tier": tier, "tier_label": label, "autonomous": autonomous, "confidence": ing.confidence if ing else 0},
    )
    if not autonomous:
        yield make_event(
            "escalated",
            detail="Escalated to Tier 2: AI will still research, analyse and draft, but the order requires human counter-signature.",
            payload={"tier": tier},
        )


def research_node(ctx: CaseContext) -> Iterator[dict]:
    note = " (broadened re-search)" if ctx.research_retries else ""
    yield _running("research", f"Querying NCDRC / State / District judgments{note}…")
    res = research.run(ctx)
    ctx.research = res.output
    yield _done("research", res)


def analyze_node(ctx: CaseContext) -> Iterator[dict]:
    yield _running("analysis", "Reading both submissions & reasoning…")
    res = analysis.run(ctx)
    ctx.analysis = res.output
    yield _done("analysis", res)
    if ctx.analysis and ctx.analysis.needs_more_research:
        yield make_event("loop", detail="Precedent coverage thin — orchestrator is looping back to Research with a broader query.")


def mediate_node(ctx: CaseContext) -> Iterator[dict]:
    yield _running("mediation", "Modelling a settlement on precedent outcomes…")
    res = mediation.run(ctx)
    ctx.mediation = res.output
    yield _done("mediation", res)


def resolve_node(ctx: CaseContext) -> Iterator[dict]:
    if ctx.via_mediation:
        yield make_event("orchestrator", agent="orchestrator", title=TITLES["orchestrator"], status="done",
                         detail="Both parties accepted mediation. Formalising the settlement.", payload={"via_mediation": True})
    else:
        yield make_event("orchestrator", agent="orchestrator", title=TITLES["orchestrator"], status="done",
                         detail="Mediation declined. Triggering autonomous resolution.", payload={"via_mediation": False})

    yield _running("resolution", "Drafting the binding resolution order…")
    # Stream the findings token-by-token for a live "watch it write" effect.
    acc = ""
    for delta in llm.generate_stream(resolution.findings_prompt(ctx), system=llm.SYSTEM_PROMPT, max_tokens=260):
        acc += delta
        yield make_event("token", agent="resolution", payload={"delta": delta})
    res = resolution.finalize(ctx, acc or None)
    ctx.resolution = res.output
    yield _done("resolution", res)
    yield make_event(
        "resolved",
        detail="Case RESOLVED. Order issued and compliance monitoring activated."
        + (" Pending human sign-off (Tier 2)." if res.output.requires_human_signoff else ""),
        payload={
            "resolved": True,
            "compliance_deadline": res.output.compliance_deadline,
            "requires_human_signoff": res.output.requires_human_signoff,
        },
    )


# ----------------------------- transitions ----------------------------- #
def _after_analyze(ctx: CaseContext) -> str:
    if ctx.analysis and ctx.analysis.needs_more_research and ctx.research_retries < 1:
        ctx.research_retries += 1
        return "research"
    return "mediate"


NODES: dict[str, Callable[[CaseContext], Iterator[dict]]] = {
    "ingest": ingest_node,
    "route": route_node,
    "research": research_node,
    "analyze": analyze_node,
    "mediate": mediate_node,
    "resolve": resolve_node,
}

TRANSITIONS: dict[str, Callable[[CaseContext], str]] = {
    "ingest": lambda c: "route",
    "route": lambda c: "research",
    "research": lambda c: "analyze",
    "analyze": _after_analyze,
    "mediate": lambda c: "await",
    "resolve": lambda c: "end",
}


def _run(ctx: CaseContext, start: str) -> Iterator[dict]:
    node = start
    while True:
        for ev in NODES[node](ctx):
            yield ev
        nxt = TRANSITIONS[node](ctx)
        if nxt == "await":
            yield make_event(
                "awaiting_decision",
                detail="Mediation proposal ready. Awaiting the parties' decision.",
                payload={"proposal": ctx.mediation.model_dump() if ctx.mediation else {}},
            )
            return
        if nxt == "end":
            return
        node = nxt


def run_pipeline(ctx: CaseContext) -> Iterator[dict]:
    """Agents 1-4 with routing + loop, ending at the mediation pause."""
    yield make_event("orchestrator", agent="orchestrator", title=TITLES["orchestrator"], status="running",
                     detail="Initialising multi-agent workflow…")
    llm_status = llm.status()
    engine = llm_status.get("engine", llm_status.get("provider", "unknown"))
    yield make_event("orchestrator", agent="orchestrator", title=TITLES["orchestrator"], status="done",
                     detail=f"Engaging agents · {engine}.",
                     payload={"engine": engine, "llm": llm.is_available()})
    yield from _run(ctx, "ingest")


def run_resolution(ctx: CaseContext, *, via_mediation: bool) -> Iterator[dict]:
    """Agent 5: produce the final order (streamed)."""
    ctx.via_mediation = via_mediation
    yield from _run(ctx, "resolve")
