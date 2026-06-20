"""The shared "blackboard" that flows through the orchestration graph.

Every agent reads from and writes to a single typed CaseContext instead of
passing positional args and stashing scratch keys on a dict. This makes the
data flow explicit, validates structure, and lets the orchestrator branch on
typed fields (confidence, coverage, routing decisions).
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# Structured agent outputs
# --------------------------------------------------------------------------- #
class IngestionResult(BaseModel):
    dispute_subtype: str
    signals: list[str] = Field(default_factory=list)
    claim_amount: float
    claim_amount_display: str
    amounts_detected: list[str] = Field(default_factory=list)
    key_dates: list[str] = Field(default_factory=list)
    evidence_count: int
    evidence_kinds: list[str] = Field(default_factory=list)
    respondent_responded: bool
    tier1_eligible: bool
    recommended_tier: int
    confidence: float
    reasoning: str = ""
    engine: str = "scripted"


class RetrievedPrecedent(BaseModel):
    id: str
    title: str
    court: str
    year: int
    citation: str
    summary: str
    principle: str
    outcome: str
    outcome_detail: str
    relief_amount_ratio: float
    compliance_days: int
    relevance: float
    matched_signals: list[str] = Field(default_factory=list)


class ResearchResult(BaseModel):
    precedents: list[RetrievedPrecedent] = Field(default_factory=list)
    corpus_size: int
    coverage_score: float  # 0..1 — how well the corpus covers this case
    coverage_label: str  # "strong" | "moderate" | "thin"
    method: str  # "semantic" | "keyword"
    query: str = ""


class AnalysisResult(BaseModel):
    claimant_position: list[str] = Field(default_factory=list)
    respondent_position: list[str] = Field(default_factory=list)
    undisputed_facts: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    neutral_summary: str = ""
    strength: dict[str, str] = Field(default_factory=dict)  # {claimant, respondent}
    strength_score: dict[str, float] = Field(default_factory=dict)
    needs_more_research: bool = False
    confidence: float = 1.0
    engine: str = "scripted"


class MediationProposal(BaseModel):
    type: str
    amount: float
    amount_display: str
    compliance_days: int
    headline: str
    explanation: str = ""
    rationale: list[str] = Field(default_factory=list)
    based_on: list[str] = Field(default_factory=list)
    pct_full_relief: int = 0
    cohort_size: int = 0
    confidence: float = 1.0
    engine: str = "scripted"
    validator_notes: list[str] = Field(default_factory=list)


class ResolutionDoc(BaseModel):
    header: str
    subheader: str
    case_id: str
    date: str
    parties: dict[str, str]
    basis: str
    claim_amount_display: str
    findings: list[str]
    order: list[str]
    cited_precedents: list[dict[str, str]]
    relief_amount: float
    relief_amount_display: str
    compliance_days: int
    compliance_deadline: str
    via_mediation: bool
    requires_human_signoff: bool = False
    engine: str = "scripted"
    footer: str = ""


# --------------------------------------------------------------------------- #
# The blackboard
# --------------------------------------------------------------------------- #
class RouteDecision(BaseModel):
    tier: int
    tier_label: str
    autonomous: bool
    requires_human_signoff: bool
    reason: str


class CaseContext(BaseModel):
    # Identity & raw facts
    case_id: str
    owner_id: str
    dispute_type: str
    claimant_name: str
    respondent_name: str
    claim_amount: float
    description: str
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    respondent_submission: Optional[dict[str, Any]] = None

    # Orchestration state
    tier: int = 1
    tier_label: str = "Tier 1 — Fully Autonomous AI Resolution"
    route: Optional[RouteDecision] = None
    research_retries: int = 0
    via_mediation: bool = True  # set when resolution is triggered

    # Agent results (the blackboard)
    ingestion: Optional[IngestionResult] = None
    research: Optional[ResearchResult] = None
    analysis: Optional[AnalysisResult] = None
    mediation: Optional[MediationProposal] = None
    resolution: Optional[ResolutionDoc] = None

    @classmethod
    def from_case(cls, case: dict) -> "CaseContext":
        return cls(
            case_id=case["case_id"],
            owner_id=case.get("owner_id", ""),
            dispute_type=case["dispute_type"],
            claimant_name=case["claimant"]["name"],
            respondent_name=case["respondent"]["name"],
            claim_amount=case["claim_amount"],
            description=case["description"],
            evidence=case.get("evidence", []),
            respondent_submission=case.get("respondent_submission"),
            tier=case.get("tier", 1),
            tier_label=case.get("tier_label", "Tier 1 — Fully Autonomous AI Resolution"),
        )
