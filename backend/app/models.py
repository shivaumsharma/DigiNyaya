"""Pydantic schemas for the DigiNyaya API."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator

from .language.config import is_pipeline_language, is_supported_language, normalize_language_code


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
    # Blocked by app.core.safety_gate at either checkpoint -- routed to human
    # legal review instead of an AI-generated answer. See `escalation` below.
    escalated = "escalated"


class Party(BaseModel):
    name: str
    role: str  # "claimant" or "respondent"
    aadhaar_verified: bool = False


class Evidence(BaseModel):
    filename: str
    kind: str = "document"  # invoice, screenshot, contract, receipt, photo, other
    note: Optional[str] = None


def _validate_optional_language(value: str | None) -> str | None:
    """Shared validator for the optional BCP-47 language hint on inbound
    submissions. ``None`` means "auto-detect" -- the Language Gateway will
    run LID on the text itself. A non-None value must normalize to either a
    gateway-supported user language or the pipeline language (en-IN);
    anything else is almost certainly a stale/typo'd frontend selector
    value, and is worth rejecting at the API boundary rather than letting
    it silently fall back to detection deep inside the gateway.
    """
    if value is None:
        return None
    normalized = normalize_language_code(value)
    if not (is_supported_language(normalized) or is_pipeline_language(normalized)):
        raise ValueError(f"Unsupported language code: {value!r}")
    return normalized


class ClaimSubmission(BaseModel):
    claimant_name: str
    respondent_name: str
    dispute_type: DisputeType = DisputeType.consumer_dispute
    claim_amount: float
    description: str
    evidence: list[Evidence] = Field(default_factory=list)
    # BCP-47 hint from the frontend's language selector; None = auto-detect
    # via the Language Gateway's LID call.
    language: Optional[str] = None

    _validate_language = field_validator("language")(_validate_optional_language)


class RespondentSubmission(BaseModel):
    statement: str
    accepts_liability: bool = False
    counter_offer: Optional[float] = None
    language: Optional[str] = None
    # Server-populated only: the Language Gateway fills this in with the
    # untranslated original statement once `statement` has been normalized
    # to English. Any value sent by the client here is ignored/overwritten.
    original_statement: Optional[str] = None

    _validate_language = field_validator("language")(_validate_optional_language)


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


class LanguageOption(BaseModel):
    code: str
    label: str
    native: str


class SupportedLanguagesResponse(BaseModel):
    languages: list[LanguageOption]
    default: str
    pipeline: str


class DocumentOut(BaseModel):
    id: str
    original_filename: Optional[str] = None
    mime_type: Optional[str] = None
    file_size: Optional[int] = None
    is_scanned: bool = False
    extraction_status: str = "pending"
    ocr_confidence: Optional[float] = None
    ocr_engine: Optional[str] = None
    error_message: Optional[str] = None
    uploaded_at: Optional[str] = None


class DocumentDetailOut(DocumentOut):
    raw_ocr_text: Optional[str] = None
    cleaned_text: Optional[str] = None


class DocumentReviewOut(BaseModel):
    document_id: str
    filename: Optional[str] = None
    # None means "couldn't assess yet" (no text extracted, or the LLM call
    # failed) -- deliberately distinct from False, never a false accusation.
    relevant: Optional[bool] = None
    looks_like: Optional[str] = None
    note: str = ""
    # Text-only heuristic, NOT forgery/tampering detection (see
    # app.agents.preliminary_review._document_relevance). None = couldn't
    # assess; False = no specific concern found; True = a specific,
    # nameable issue was found (authenticity_note explains it).
    authenticity_flag: Optional[bool] = None
    authenticity_note: str = ""


class DescriptionReviewOut(BaseModel):
    detailed_enough: bool
    note: str = ""


class WinnabilityOut(BaseModel):
    score: int
    label: str
    reasons: list[str] = Field(default_factory=list)


class PreliminaryReviewOut(BaseModel):
    documents: list[DocumentReviewOut]
    case_strength_note: str
    description_review: DescriptionReviewOut
    winnability: WinnabilityOut


class DiscrepancyOut(BaseModel):
    id: str
    document_ids: list[str]
    discrepancy_type: str
    severity: str
    confidence_score: float
    explanation: Optional[str] = None
    source_location: Optional[str] = None
    flagged_for_review: bool
    created_at: Optional[str] = None


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
    # Language Gateway metadata: the language the case was actually filed in,
    # and the claimant's untranslated original text. `description` above is
    # always the English pipeline copy; these two exist so the API layer can
    # localize the response back to the filer's language without a second
    # (lossier) round-trip through the translator.
    source_language: str = "en-IN"
    original_description: Optional[str] = None
    evidence: list[Evidence]
    respondent_submission: Optional[RespondentSubmission] = None
    steps: list[AgentStep] = Field(default_factory=list)
    mediation: Optional[dict[str, Any]] = None
    resolution: Optional[dict[str, Any]] = None
    # Set when app.core.safety_gate blocked this case -- see EscalationResult.to_dict().
    escalation: Optional[dict[str, Any]] = None
    # Manual human-review escalation (POST /api/cases/{id}/request-review),
    # distinct from the automatic safety_gate one above -- see app/routers/reviews.py.
    human_review_requested: bool = False
    reviewer_decision: Optional[dict[str, Any]] = None
    created_at: datetime


class ReviewQueueItemOut(BaseModel):
    case_id: str
    dispute_type: str
    claimant: Optional[str] = None
    respondent: Optional[str] = None
    claim_amount: float
    status: str
    reason: str
    created_at: Optional[str] = None


class CaseSummaryOut(BaseModel):
    """One row of a claimant's own case list (GET /api/cases). Filed-by-me
    only -- a case's respondent is identified by name, not by user id, so
    there's no way yet to also list "cases filed against me" here; that
    needs the respondent-identity gap closed first (see README's known
    issues / the notification-delivery gap)."""

    case_id: str
    dispute_type: str
    respondent: Optional[str] = None
    claim_amount: float
    status: str
    tier: int
    tier_label: str
    created_at: Optional[str] = None


class ReviewDecisionIn(BaseModel):
    approve: bool
    note: str = ""
    # Only meaningful when approve=False (or the case had no AI resolution to
    # countersign at all, e.g. a safety-gate escalation) -- the reviewer's own
    # relief figure becomes the final ruling. None = the AI's own resolution
    # amount stands unchanged (a straightforward Tier 2 countersignature).
    relief_amount: Optional[float] = None