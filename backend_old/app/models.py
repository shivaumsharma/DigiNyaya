"""Pydantic schemas for the DigiNyaya API."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class DisputeType(str, Enum):
    consumer_dispute = "consumer_dispute"
    money_recovery = "money_recovery"
    contract_breach = "contract_breach"
    cheque_bounce = "cheque_bounce"


class CaseStatus(str, Enum):
    draft = "draft"
    awaiting_response = "awaiting_response"
    ready = "ready"
    processing = "processing"
    mediation_proposed = "mediation_proposed"
    mediation_accepted = "mediation_accepted"
    resolved = "resolved"


class Party(BaseModel):
    name: str
    role: str  # "claimant" or "respondent"
    aadhaar_verified: bool = False


class Evidence(BaseModel):
    filename: str
    kind: str = "document"  # invoice, screenshot, contract, receipt, photo, other
    note: Optional[str] = None


class LoginRequest(BaseModel):
    aadhaar_last4: str = Field(..., min_length=4, max_length=4)
    name: str


class LoginResponse(BaseModel):
    citizen_id: str
    name: str
    aadhaar_verified: bool
    masked_aadhaar: str


class ClaimSubmission(BaseModel):
    claimant_name: str
    respondent_name: str
    dispute_type: DisputeType = DisputeType.consumer_dispute
    claim_amount: float
    description: str
    evidence: list[Evidence] = Field(default_factory=list)


class RespondentSubmission(BaseModel):
    statement: str
    accepts_liability: bool = False
    counter_offer: Optional[float] = None


class CaseCreateResponse(BaseModel):
    case_id: str
    status: CaseStatus
    tier: int
    tier_label: str
    created_at: datetime


class MediationDecision(BaseModel):
    accept: bool
    party: str = "claimant"  # which side is responding


class AgentStep(BaseModel):
    agent: str
    title: str
    status: str  # running | done | error
    detail: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


class CaseView(BaseModel):
    case_id: str
    status: CaseStatus
    tier: int
    tier_label: str
    dispute_type: DisputeType
    claimant: Party
    respondent: Party
    claim_amount: float
    description: str
    evidence: list[Evidence]
    respondent_submission: Optional[RespondentSubmission] = None
    steps: list[AgentStep] = Field(default_factory=list)
    mediation: Optional[dict[str, Any]] = None
    resolution: Optional[dict[str, Any]] = None
    created_at: datetime
