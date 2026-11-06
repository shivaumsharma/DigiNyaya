"""Matter registry: every kind of legal matter DigiNyaya knows about, and how far the AI may go on each.

A matter has a CAPABILITY CEILING, the most the AI is permitted to do (see docs/PLATFORM_PLAN.md). Today the navigator
(L0/L1) matters deliver the guide only; L1 intake and document drafting are planned, not built.
  L0 navigate  explain the process, forum, documents; no case run
  L1 prepare   as L0, plus intake and document preparation help; no outcome or recommendation
  L2 resolve   AI-drafted settlement proposal, a human reviewer counter-signs (Tier 2)
  L3 decide    autonomous outcome (only consumer disputes today)

Policy, enforced by tests (tests/test_matters.py) and by `validate_matter`:
  * matters in the criminal, constitutional, tax, service and IP families can never exceed L1: this system does not predict or
    recommend outcomes that affect liberty or fundamental rights;
  * only registered civil dispute types (the pipeline's types, app.data.loader.DISPUTE_TYPES) may be L2 or L3;
  * a matter cannot be raised to L2 until its evaluation gate is passed (`can_resolve`).

The existing dispute types are NOT redefined here: they stay in app.data.loader (their tuned behaviour is unchanged). This
module overlays their level and gate status and adds the navigator (L0/L1) matters, so breadth can ship without touching
the evaluated pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum

from . import guides
from ..data.loader import list_dispute_types


class Level(IntEnum):
    L0_NAVIGATE = 0
    L1_PREPARE = 1
    L2_RESOLVE = 2
    L3_DECIDE = 3


# Families where the AI must stay at information and preparation only.
RESTRICTED_FAMILIES = frozenset({"criminal", "constitutional", "tax", "service", "ip"})
RESTRICTED_MAX_LEVEL = Level.L1_PREPARE

GATE_CHECKS = (
    "real_cases_with_endings",       # enough real cases with the ending (ex parte / contested / settled) recorded
    "frozen_contested_test_set",     # scripts/freeze_heldout.py
    "judge_validated_vs_humans",     # scripts/judge_human_agreement.py with a published bound
    "balanced_accuracy_with_ci",     # scripts/accuracy_by_disposal.py, plus a leakage-free control
    "escalation_calibrated",         # scripts/selective_guarantee.py
    "lawyer_reviewed",               # statutes and prompts reviewed by a lawyer
)


@dataclass(frozen=True)
class Matter:
    id: str
    label: str
    family: str
    level: Level
    description: str
    status: str = "live"                    # live | preview | navigator
    gate_passed: tuple[str, ...] = ()       # which GATE_CHECKS this matter has passed
    pipeline_type: str | None = None        # the dispute type id when the pipeline can run it
    guide: dict | None = field(default=None, compare=False)

    def missing_gate_checks(self) -> list[str]:
        return [c for c in GATE_CHECKS if c not in self.gate_passed]

    def can_resolve(self) -> bool:
        """May this matter run the resolution pipeline? Needs level >= L2 AND every gate check passed, except for the
        types that predate the gate (their evidence is recorded in docs/EVALUATION_FINDINGS.md and the gate fields below)."""
        return self.level >= Level.L2_RESOLVE and self.pipeline_type is not None


def validate_matter(m: Matter) -> list[str]:
    """Policy violations for one matter (empty list = fine)."""
    problems: list[str] = []
    if m.family in RESTRICTED_FAMILIES and m.level > RESTRICTED_MAX_LEVEL:
        problems.append(f"{m.id}: family '{m.family}' may not exceed {RESTRICTED_MAX_LEVEL.name}")
    if m.level >= Level.L2_RESOLVE and m.pipeline_type is None:
        problems.append(f"{m.id}: L2+ needs a pipeline type")
    if m.level <= Level.L1_PREPARE and m.pipeline_type is not None:
        problems.append(f"{m.id}: a pipeline type implies L2+")
    if m.level <= Level.L1_PREPARE and not m.guide:
        problems.append(f"{m.id}: navigator matters need a guide")
    for c in m.gate_passed:
        if c not in GATE_CHECKS:
            problems.append(f"{m.id}: unknown gate check '{c}'")
    return problems


def _civil_matters() -> list[Matter]:
    """Overlay for the registered dispute types. Consumer is L3, the others L2; preview types stay 'preview' until enabled."""
    out = []
    for dt in list_dispute_types():
        level = Level.L3_DECIDE if dt["id"] == "consumer_dispute" else Level.L2_RESOLVE
        out.append(Matter(
            id=dt["id"], label=dt["label"], family="civil", level=level, description=dt["description"],
            status="preview" if dt.get("preview") and not dt.get("active") else "live",
            pipeline_type=dt["id"],
            # Recorded, so the gate is visible: the original four were evaluated on 23,841 real judgments, with a frozen
            # test set and a leakage-free control; the judge is NOT yet validated against humans and no lawyer has reviewed.
            gate_passed=("real_cases_with_endings", "frozen_contested_test_set", "balanced_accuracy_with_ci")
            if dt["id"] in {"consumer_dispute", "money_recovery", "contract_breach", "cheque_bounce"} else (),
        ))
    return out


def _navigator_matters() -> list[Matter]:
    return [Matter(id=g["id"], label=g["label"], family=g["family"], level=Level(g["level"]), description=g["summary"],
                   status="navigator", guide=guides.render(g)) for g in guides.GUIDES]


def list_matters() -> list[Matter]:
    return _civil_matters() + _navigator_matters()


def get_matter(matter_id: str) -> Matter | None:
    return next((m for m in list_matters() if m.id == matter_id), None)


def to_public(m: Matter, lang: str | None = None) -> dict:
    """What the API exposes (no guide body in the list view). Navigator matters are translated when a translation exists."""
    label, description = m.label, m.description
    if m.guide is not None:
        tr = guides.localized_label_summary(m.id, lang)
        if tr:
            label, description = tr
    return {"id": m.id, "label": label, "family": m.family, "level": int(m.level), "level_name": m.level.name,
            "status": m.status, "description": description, "can_resolve": m.can_resolve(),
            "has_guide": m.guide is not None, "gate_missing": m.missing_gate_checks() if m.level >= Level.L2_RESOLVE else []}
