"""Safety Gate — escalation checks that can block the AI pipeline entirely.

Five conditions gate the pipeline at two checkpoints. If any condition
triggers, the case is escalated to a human legal authority instead of
receiving an AI-generated mediation/resolution:

CHECKPOINT A — pre-filter, runs on the raw case input *before* the 5-agent
pipeline executes (nothing has run yet, so there is nothing to discard):
  1. Criminal matter detected  — keyword stub; needs a real legal classifier.
  5. Jurisdiction/scope mismatch — keyword + category stub; needs refinement.

CHECKPOINT B — post-check, runs on the finished pipeline output *after* all
five agents have run, before the result is persisted/returned to the user.
A trigger here means a real result was produced but must be discarded:
  2. Composite confidence below a hard floor (0.4) — stricter than the
     reporting-only weighting in app.core.confidence.
  3. Fewer than 2 relevant precedents/sources retrieved.
  4. Agent disagreement — Research vs. Analysis reaching opposite
     conclusions on the core outcome; a simple stub scoring check.

Call convention: a single entry point, ``check_escalation(case_input,
pipeline_output=None)``, dispatches to the right checkpoint based on whether
``pipeline_output`` was supplied. Returns ``None`` when nothing triggers
(caller proceeds normally), or an ``EscalationResult`` when the case must be
escalated instead.

Both ``case_input`` and ``pipeline_output`` are duck-typed: a plain dict (as
used in unit tests and easy to construct from a request payload) or an
object with matching attributes (the real ``CaseContext`` Pydantic model)
both work unmodified, via the ``_get()`` accessor below. This keeps the gate
testable with plain mocks while still dropping into the live pipeline
without any adapter layer.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from .. import llm

logger = logging.getLogger("diginyaya.safety_gate")


class EscalationCondition(str, Enum):
    CRIMINAL_MATTER = "criminal_matter_detected"
    JURISDICTION_MISMATCH = "jurisdiction_scope_mismatch"
    LOW_CONFIDENCE = "confidence_below_floor"
    NO_PRECEDENT = "no_precedent_retrieved"
    AGENT_DISAGREEMENT = "agent_disagreement"


# ---------------------------------------------------------------------------
# Tunables — thresholds/keyword lists are stubs called out explicitly where
# the user asked for a proper classifier later; adjust here as the harness
# in scripts/eval_cases.py (or a dedicated escalation eval) surfaces data.
# ---------------------------------------------------------------------------

CONFIDENCE_FLOOR = 0.4  # stricter, hard cutoff -- distinct from the 0.6 Tier-1 autonomy bar in ingestion.py
MIN_RELEVANT_PRECEDENTS = 2

# STUB: keyword heuristic. A real implementation needs a proper legal-issue
# classifier (or at minimum a much larger, jurisdiction-reviewed lexicon) --
# these phrases are deliberately narrow/specific to avoid false-positiving on
# ordinary civil language (e.g. bare "fraud"/"fraudulent" is already a
# legitimate consumer-dispute signal in app.agents.nlp for unauthorized bank
# transactions, so it is intentionally NOT included here on its own).
CRIMINAL_INDICATORS: tuple[str, ...] = (
    "fir filed", "police complaint", "criminal complaint", "criminal case",
    "assault", "assaulted", "physically attacked", "attacked me physically",
    "threatened to kill", "death threat", "criminal intimidation",
    "extortion", "blackmail", "kidnap", "abduction",
    "criminal breach of trust", "forged", "forgery",
    "rape", "sexual assault", "molest",
    "domestic violence", "dowry harassment",
    "weapon", "knife attack", "attacked with a",
)

# STUB: category + keyword heuristic for matters this civil-dispute system
# should never adjudicate at all, regardless of confidence.
REGISTERED_CIVIL_DISPUTE_TYPES: frozenset[str] = frozenset(
    {"consumer_dispute", "money_recovery", "contract_breach", "cheque_bounce"}
)

OUT_OF_SCOPE_INDICATORS: tuple[str, ...] = (
    "minor child", "my minor", "child custody", "custody of the child",
    "divorce", "matrimonial dispute", "child support", "alimony",
    "constitutional", "fundamental right", "writ petition", "habeas corpus",
    "interim injunction", "seeking an injunction", "restraining order", "stay order",
)

# STUB: crude directional cross-check between what the precedent corpus
# implies (mean relief ratio) and what the Analysis agent independently
# assessed (claimant strength label). Flag for refinement -- e.g. weighting
# by coverage/strength_score rather than the coarse strength label.
AGREEMENT_RATIO_HIGH = 0.7
AGREEMENT_RATIO_LOW = 0.3

ESCALATION_MESSAGE = (
    "This case cannot be resolved through automated mediation and requires "
    "review by a qualified human legal authority."
)


@dataclass(frozen=True)
class EscalationResult:
    """Returned when the safety gate blocks the pipeline (pre) or discards
    its output (post). Never surfaced with raw internal reasoning by
    default -- ``details``/``triggered_conditions`` are for logging/ops, the
    caller decides what (if anything) beyond ``user_message`` to show.
    """

    triggered_conditions: list[str]
    details: dict[str, str]
    user_message: str
    court_registration_status: dict[str, str]
    case_id: str | None
    checkpoint: str  # "pre_filter" | "post_check"
    timestamp: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def check_court_registration(case_id: str) -> dict[str, str]:
    """STUB for a future live district-court-database integration.

    Deliberately trivial and side-effect-free for now -- the interface
    (case_id in, status dict out) is final so this can be swapped for a real
    API call later without touching any caller of check_escalation().
    """
    return {"status": "not_integrated", "message": "Court database integration pending"}


# ---------------------------------------------------------------------------
# Duck-typed field access -- works for a plain dict (unit tests, request
# payloads) or an attribute-bearing object (the real CaseContext/ResultDoc
# Pydantic models) without any adapter layer.
# ---------------------------------------------------------------------------

def _get(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _match_any(text: str, phrases: tuple[str, ...]) -> list[str]:
    return [p for p in phrases if p in text]


def _combined_text(case_input: Any) -> str:
    description = _get(case_input, "description", "") or ""
    respondent = _get(case_input, "respondent_submission")
    statement = _get(respondent, "statement", "") or "" if respondent else ""
    return f"{description}\n{statement}".lower()


# ---------------------------------------------------------------------------
# CHECKPOINT A -- pre-filter (conditions 1 & 5)
# ---------------------------------------------------------------------------

# LLM second-opinion prompt for a criminal-indicator keyword hit. Real-
# judgment testing found the keyword list alone produces two distinct kinds
# of wrong answer: (1) false positives on ordinary civil narratives that
# happen to mention a crime-adjacent word in passing (30 tenancy/property
# cases this session), and (2) cases that ARE genuinely retaliatory criminal
# filings over what's fundamentally a civil dispute -- 2 of the 3 real
# escalation__criminal_matter test cases are exactly this (a High Court
# quashed both FIRs under CrPC S482 as "abuse of process", explicitly
# calling the underlying dispute civil). A keyword hit alone can't tell
# these apart from genuine crime (the 3rd test case: a real securities-
# fraud prosecution) -- this asks the question directly instead of trusting
# keyword presence as dispositive.
_CRIMINAL_CONFIRM_SCHEMA = (
    '{"is_genuine_criminal_matter": <bool: true ONLY if this describes conduct a criminal '
    "court would actually charge/convict on in its own right (real violence, genuine fraud "
    "with criminal intent, threats, extortion, sexual offenses, etc.) -- false if this is "
    "fundamentally a civil/commercial dispute (money owed, property, contract, tenancy, "
    "employment) where a party used criminal-sounding language (mentioning a filed police "
    'complaint/FIR, alleging "cheating" or "criminal breach of trust") as leverage or '
    'retaliation, rather than describing conduct that itself meets a real criminal offense\'s '
    'elements>, "reasoning": "<=20 word explanation"}'
)


def _llm_confirm_criminal(combined_text: str, hits: list[str]) -> bool | None:
    """Returns True/False when the LLM gives a usable answer, None when it's
    unavailable/fails -- callers must treat None as "can't confirm, stay
    safe" (i.e. still escalate), never as a silent false."""
    prompt = (
        "A keyword filter flagged this case as possibly describing a criminal matter "
        f"(matched: {', '.join(hits)}). Decide whether it's genuinely criminal or a civil "
        f"dispute using crime-adjacent language. Return JSON only, matching this schema: "
        f"{_CRIMINAL_CONFIRM_SCHEMA}\n\nCASE TEXT:\n{combined_text[:3000]}"
    )
    data = llm.generate_json(prompt, system=llm.SYSTEM_PROMPT, model="classification", max_tokens=200, temperature=0.0)
    if not data:
        return None
    verdict = data.get("is_genuine_criminal_matter")
    return bool(verdict) if isinstance(verdict, bool) else None


def _check_criminal_matter(combined_text: str) -> str | None:
    hits = _match_any(combined_text, CRIMINAL_INDICATORS)
    if not hits:
        return None
    confirmed = _llm_confirm_criminal(combined_text, hits)
    if confirmed is False:
        logger.info(
            "criminal-matter keyword hit downgraded by LLM confirmation",
            extra={"event": "criminal_matter_llm_downgrade", "hits": hits},
        )
        return None
    # confirmed is True, or None (LLM unavailable/unusable) -- fail safe and
    # still escalate on an unconfirmed hit rather than silently trust a
    # keyword match with no independent check.
    return f"Criminal-matter indicator(s) detected: {', '.join(hits)}"


def _check_jurisdiction_mismatch(case_input: Any, combined_text: str) -> str | None:
    dispute_type = _get(case_input, "dispute_type")
    if dispute_type and dispute_type not in REGISTERED_CIVIL_DISPUTE_TYPES:
        return f"Dispute type '{dispute_type}' is not a registered civil dispute category this system handles."
    hits = _match_any(combined_text, OUT_OF_SCOPE_INDICATORS)
    if hits:
        return f"Out-of-scope matter indicator(s) detected: {', '.join(hits)}"
    return None


def _check_pre_filter(case_input: Any) -> EscalationResult | None:
    combined_text = _combined_text(case_input)
    triggered: list[EscalationCondition] = []
    details: dict[str, str] = {}

    # cheque_bounce (Section 138 NI Act) is a REGISTERED dispute type
    # (app/data/loader.py) that is BY NATURE prosecuted via a criminal
    # complaint -- its own case text routinely contains "criminal
    # case"/"complaint" language that would otherwise trip this check. It's
    # already kept out of Tier-1 autonomy and human-reviewed
    # (app/agents/ingestion.py's TIER1_ELIGIBLE_TYPES) specifically because
    # of its criminal classification -- that gate is the intended safety net
    # for this type, not this keyword check, which exists to catch
    # UNREGISTERED criminal matters the system was never meant to touch at
    # all. Checked against the corpus: only 2/33 cheque_bounce precedent
    # summaries would otherwise collide with CRIMINAL_INDICATORS.
    if _get(case_input, "dispute_type") != "cheque_bounce":
        reason = _check_criminal_matter(combined_text)
        if reason:
            triggered.append(EscalationCondition.CRIMINAL_MATTER)
            details[EscalationCondition.CRIMINAL_MATTER.value] = reason

    reason = _check_jurisdiction_mismatch(case_input, combined_text)
    if reason:
        triggered.append(EscalationCondition.JURISDICTION_MISMATCH)
        details[EscalationCondition.JURISDICTION_MISMATCH.value] = reason

    if not triggered:
        return None
    return _build_result(triggered, details, _get(case_input, "case_id"), checkpoint="pre_filter")


# ---------------------------------------------------------------------------
# CHECKPOINT B -- post-check (conditions 2, 3 & 4)
# ---------------------------------------------------------------------------

def _extract_composite_score(pipeline_output: Any) -> float | None:
    resolution = _get(pipeline_output, "resolution")
    candidates = [
        _get(resolution, "composite_confidence") if resolution is not None else None,
        _get(pipeline_output, "composite_confidence"),
    ]
    for cc in candidates:
        if isinstance(cc, dict) and isinstance(cc.get("score"), (int, float)):
            return float(cc["score"])
        if isinstance(cc, (int, float)):
            return float(cc)

    direct = _get(pipeline_output, "confidence")
    if isinstance(direct, (int, float)):
        return float(direct)
    return None


def _extract_precedents(pipeline_output: Any) -> list[Any]:
    research = _get(pipeline_output, "research")
    precedents = _get(research, "precedents") if research is not None else None
    if precedents is None:
        precedents = _get(pipeline_output, "precedents")
    return list(precedents) if precedents else []


def _check_confidence_floor(pipeline_output: Any) -> str | None:
    score = _extract_composite_score(pipeline_output)
    if score is None:
        return None  # nothing to check against -- not this condition's job to require it
    if score < CONFIDENCE_FLOOR:
        return f"Composite confidence {score:.3f} is below the hard escalation floor of {CONFIDENCE_FLOOR}."
    return None


def _check_precedent_coverage(pipeline_output: Any) -> str | None:
    precedents = _extract_precedents(pipeline_output)
    if len(precedents) < MIN_RELEVANT_PRECEDENTS:
        return (
            f"Only {len(precedents)} precedent/source(s) retrieved "
            f"(minimum {MIN_RELEVANT_PRECEDENTS} required)."
        )
    return None


def _check_agent_disagreement(pipeline_output: Any) -> str | None:
    # NOTE: analysis.contradictions is deliberately NOT used as a signal here.
    # In app.agents.analysis, that field only ever records a claimant-vs-
    # respondent quantum gap (a counter-offer below the claim amount) -- i.e.
    # the two *litigants* disagreeing, which is normal for any contested
    # case. It is not evidence that the *agents* disagree with each other,
    # and using it as one produced false positives on ordinary contested
    # cases with a counter-offer during eval (see scripts/eval_cases.py).
    #
    # Directional cross-check instead: what the retrieved precedents imply
    # vs. what Analysis independently assessed. A coarse stub -- see
    # AGREEMENT_RATIO_* -- but it actually compares two different agents'
    # outputs, which is what this condition is meant to catch.
    analysis = _get(pipeline_output, "analysis")
    precedents = _extract_precedents(pipeline_output)
    strength = _get(analysis, "strength") if analysis is not None else None
    claimant_strength = _get(strength, "claimant") if strength is not None else None
    ratios = [
        r for p in precedents
        if isinstance((r := _get(p, "relief_amount_ratio")), (int, float))
    ]
    if ratios and claimant_strength:
        mean_ratio = sum(ratios) / len(ratios)
        if mean_ratio >= AGREEMENT_RATIO_HIGH and claimant_strength == "weak":
            return (
                f"Precedents lean favorable (mean relief ratio {mean_ratio:.2f}) but Analysis "
                "rated the claimant's case 'weak' -- Research and Analysis disagree on outcome direction."
            )
        if mean_ratio <= AGREEMENT_RATIO_LOW and claimant_strength == "strong":
            return (
                f"Precedents lean unfavorable (mean relief ratio {mean_ratio:.2f}) but Analysis "
                "rated the claimant's case 'strong' -- Research and Analysis disagree on outcome direction."
            )
    return None


def _check_post_check(case_input: Any, pipeline_output: Any) -> EscalationResult | None:
    triggered: list[EscalationCondition] = []
    details: dict[str, str] = {}

    reason = _check_confidence_floor(pipeline_output)
    if reason:
        triggered.append(EscalationCondition.LOW_CONFIDENCE)
        details[EscalationCondition.LOW_CONFIDENCE.value] = reason

    reason = _check_precedent_coverage(pipeline_output)
    if reason:
        triggered.append(EscalationCondition.NO_PRECEDENT)
        details[EscalationCondition.NO_PRECEDENT.value] = reason

    reason = _check_agent_disagreement(pipeline_output)
    if reason:
        triggered.append(EscalationCondition.AGENT_DISAGREEMENT)
        details[EscalationCondition.AGENT_DISAGREEMENT.value] = reason

    if not triggered:
        return None

    case_id = _get(case_input, "case_id") or _get(pipeline_output, "case_id")
    return _build_result(triggered, details, case_id, checkpoint="post_check")


# ---------------------------------------------------------------------------
# Shared result assembly + logging
# ---------------------------------------------------------------------------

def _build_result(
    triggered: list[EscalationCondition],
    details: dict[str, str],
    case_id: str | None,
    *,
    checkpoint: str,
) -> EscalationResult:
    timestamp = datetime.now(timezone.utc).isoformat()

    for condition in triggered:
        logger.warning(
            "safety gate escalation triggered",
            extra={
                "event": "safety_gate_triggered",
                "condition": condition.value,
                "case_id": case_id,
                "checkpoint": checkpoint,
                "timestamp": timestamp,
            },
        )

    return EscalationResult(
        triggered_conditions=[c.value for c in triggered],
        details=details,
        user_message=ESCALATION_MESSAGE,
        court_registration_status=check_court_registration(case_id or ""),
        case_id=case_id,
        checkpoint=checkpoint,
        timestamp=timestamp,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def check_escalation(case_input: Any, pipeline_output: Any = None) -> EscalationResult | None:
    """Run the safety gate at the appropriate checkpoint.

    ``pipeline_output is None``  -> CHECKPOINT A (pre-filter, conditions 1 & 5).
    ``pipeline_output is not None`` -> CHECKPOINT B (post-check, conditions 2-4).

    Returns None when nothing triggers (caller proceeds normally), otherwise
    an EscalationResult the caller must use INSTEAD of the pipeline's answer.
    """
    if pipeline_output is None:
        return _check_pre_filter(case_input)
    return _check_post_check(case_input, pipeline_output)
