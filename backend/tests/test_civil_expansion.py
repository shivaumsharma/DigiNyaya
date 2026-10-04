"""Civil expansion, phase 1: tenancy / property / employment as native dispute types that BEHAVE exactly like the
types the real-judgment eval maps them to. The equivalence tests are the point: the mediation thresholds and the
full-claim override were tuned on real data through the mapped types, so a native type that drifted from its
alias would silently change results and break comparability with the published baseline."""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from app.core import graph
from app.core.context import CaseContext
from app.core.safety_gate import EscalationCondition, check_escalation
from app.data import loader
from app.data.loader import effective_type, get_dispute_type, list_dispute_types, precedent_category
from app.main import SAMPLE_CLAIMS
from app.models import ClaimSubmission, DisputeType

NEW_TYPES = {"tenancy_dispute": "money_recovery", "property_dispute": "contract_breach", "employment_dispute": "money_recovery"}
ORIGINAL = ("consumer_dispute", "money_recovery", "contract_breach", "cheque_bounce")


def _signup(client, email, password="correcthorsebatterystaple", name="Test User"):
    r = client.post("/auth/signup/email", json={"email": email, "password": password, "full_name": name, "preferred_language": "en-IN"})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


class TestRegistry:
    def test_new_types_exist_are_tier2_and_inherit_the_expected_behaviour(self):
        for new, alias in NEW_TYPES.items():
            dt = next(d for d in loader.DISPUTE_TYPES if d["id"] == new)
            assert dt["tier"] == 2 and dt["behaves_as"] == alias and dt["preview"] is True
            assert effective_type(new) == alias

    def test_original_types_behave_as_themselves(self):
        for t in ORIGINAL:
            assert effective_type(t) == t
        assert effective_type("never_heard_of_it") == "never_heard_of_it"

    def test_preview_types_are_inactive_unless_the_env_flag_is_set(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(loader.PREVIEW_ENV, None)
            assert all(not d["active"] for d in list_dispute_types() if d.get("preview"))
            assert all(d["active"] for d in list_dispute_types() if not d.get("preview"))
        with patch.dict(os.environ, {loader.PREVIEW_ENV: "1"}):
            assert all(d["active"] for d in list_dispute_types())

    def test_list_returns_copies_so_the_registry_cannot_be_mutated(self):
        listed = list_dispute_types()
        listed[-1]["active"] = "tampered"
        assert get_dispute_type(listed[-1]["id"])["active"] != "tampered"

    def test_every_registry_type_has_evidence_guidance_and_a_prompt_label(self):
        from app.agents import nlp
        from app.agents.preliminary_review import _EXPECTED_EVIDENCE_BY_TYPE
        for dt in loader.DISPUTE_TYPES:
            assert dt["id"] in _EXPECTED_EVIDENCE_BY_TYPE, dt["id"]
            assert nlp.dispute_label(dt["id"]) != "dispute", dt["id"]

    def test_pydantic_enum_matches_the_registry(self):
        assert {e.value for e in DisputeType} == {d["id"] for d in loader.DISPUTE_TYPES}


class TestPrecedentCategory:
    def _corpus(self, n_native, native="tenancy_dispute"):
        return [{"category": native}] * n_native + [{"category": "money_recovery"}] * 40

    def test_falls_back_to_the_alias_until_enough_native_precedents_exist(self):
        with patch("app.data.loader.load_precedents", return_value=self._corpus(loader.MIN_NATIVE_PRECEDENTS - 1)):
            assert precedent_category("tenancy_dispute") == "money_recovery"
        with patch("app.data.loader.load_precedents", return_value=self._corpus(loader.MIN_NATIVE_PRECEDENTS)):
            assert precedent_category("tenancy_dispute") == "tenancy_dispute"

    def test_original_types_always_use_their_own_category(self):
        assert precedent_category("consumer_dispute") == "consumer_dispute"

    def test_the_bundled_corpus_has_no_invented_native_precedents(self):
        assert not [p for p in loader.load_precedents() if p["category"] in NEW_TYPES]


def _decision(ctx: CaseContext) -> dict:
    """Everything a reader of the outcome cares about, minus wording that legitimately differs by label."""
    list(graph.run_pipeline(ctx))
    out = {
        "tier": ctx.tier, "escalated": ctx.escalation is not None,
        "conditions": sorted(ctx.escalation["triggered_conditions"]) if ctx.escalation else [],
        "precedents": [p.id for p in ctx.research.precedents] if ctx.research else None,
    }
    if ctx.escalation is None and ctx.mediation is not None:
        m = ctx.mediation
        out.update(type=m.type, amount=m.amount, days=m.compliance_days, interest=m.interest_rate_pct,
                   strength=dict(ctx.analysis.strength_score) if ctx.analysis else None)
        list(graph.run_resolution(ctx, via_mediation=True))
        if ctx.resolution is not None:
            r = ctx.resolution
            out.update(relief=r.relief_amount, order=r.order, deadline_days=r.compliance_days)
    return out


def _ctx(dispute_type, description, amount, n_evidence, respondent):
    return CaseContext(
        case_id="DN-EQ-0001", owner_id="u", dispute_type=dispute_type, claimant_name="Claimant", respondent_name="Respondent",
        claim_amount=amount, description=description, evidence=[{"filename": f"e{i}.pdf", "kind": "document"} for i in range(n_evidence)],
        respondent_submission=respondent,
    )


SCENARIOS = {
    "tenancy_dispute": [
        SAMPLE_CLAIMS["tenancy_dispute"]["claim"]["description"],
        "The tenant has not vacated after the lease ended and owes Rs. 40,000 in arrears. I am seeking possession of the flat and the arrears.",
        "My landlord is refusing to return my security deposit of Rs. 30,000 after I vacated the flat.",
    ],
    "employment_dispute": [
        SAMPLE_CLAIMS["employment_dispute"]["claim"]["description"],
        "I was terminated without notice and want to be reinstated with continuity of service and back wages.",
        "My employer has not paid my notice period pay of Rs. 50,000 after I resigned.",
    ],
    "property_dispute": [
        SAMPLE_CLAIMS["property_dispute"]["claim"]["description"],
        "I paid an advance of Rs. 2,00,000 for a plot and the seller did not complete the sale deed. I am seeking possession of the plot.",
        "My neighbour has occupied part of my land and I want him to vacate it and pay Rs. 25,000 damages.",
    ],
}
RESPONDENTS = [None, {"statement": "The respondent disputes the claim.", "accepts_liability": False},
               {"statement": "The claim is barred by limitation and was settled by a signed agreement dated 2 May 2023.", "accepts_liability": False}]


class TestEquivalenceWithTheAliasedType:
    """The same facts under the native type and under its alias must lead to the same decision."""

    @pytest.mark.parametrize("native", list(NEW_TYPES))
    def test_decisions_are_identical(self, native):
        alias = NEW_TYPES[native]
        compared = 0
        for description in SCENARIOS[native]:
            for n_evidence in (1, 3):
                for respondent in RESPONDENTS:
                    a = _decision(_ctx(native, description, 60000.0, n_evidence, respondent))
                    b = _decision(_ctx(alias, description, 60000.0, n_evidence, respondent))
                    assert a == b, f"{native} drifted from {alias}\n native: {a}\n alias:  {b}\n case: {description[:70]!r}"
                    compared += 1
        assert compared == len(SCENARIOS[native]) * 2 * len(RESPONDENTS)

    def test_the_comparison_is_not_vacuous(self):
        """Guard against the equivalence tests passing only because every case escalates or dismisses alike."""
        outcomes = {(d["escalated"], d.get("type")) for native in NEW_TYPES for description in SCENARIOS[native]
                    for d in [_decision(_ctx(native, description, 60000.0, 3, RESPONDENTS[1]))]}
        assert len(outcomes) >= 2, outcomes

    def test_native_eval_mapping_matches_the_default_approximation(self):
        from scripts.run_real_judgment_eval import NATIVE_CATEGORY_TO_DISPUTE_TYPE
        expected = {"tenancy_disputes": "money_recovery", "employment_disputes": "money_recovery",
                    "property_neighbor_disputes": "contract_breach"}
        assert set(NATIVE_CATEGORY_TO_DISPUTE_TYPE) == set(expected)
        for category, native in NATIVE_CATEGORY_TO_DISPUTE_TYPE.items():
            assert effective_type(native) == expected[category], category


class TestSafetyGate:
    def _case(self, dispute_type, description="The tenant has not paid rent for five months."):
        return {"dispute_type": dispute_type, "description": description, "claimant_name": "A", "respondent_name": "B", "claim_amount": 60000}

    def test_new_types_are_registered_so_they_do_not_trip_the_scope_check(self):
        for t in NEW_TYPES:
            r = check_escalation(self._case(t))
            assert r is None or EscalationCondition.JURISDICTION_MISMATCH.value not in r.triggered_conditions, t

    def test_an_unknown_type_is_still_out_of_scope(self):
        r = check_escalation(self._case("family_dispute"))
        assert r is not None and EscalationCondition.JURISDICTION_MISMATCH.value in r.triggered_conditions

    def test_criminal_text_still_escalates_for_the_new_types(self):
        for t in NEW_TYPES:
            r = check_escalation(self._case(t, "My landlord assaulted me and threatened to kill me over the rent."))
            assert r is not None and EscalationCondition.CRIMINAL_MATTER.value in r.triggered_conditions, t


class TestApi:
    def test_dispute_types_endpoint_lists_the_new_types_as_inactive(self, client):
        types = {d["id"]: d for d in client.get("/api/dispute-types").json()}
        for t in NEW_TYPES:
            assert types[t]["active"] is False and types[t]["preview"] is True
        assert all(types[t]["active"] for t in ORIGINAL)

    def test_filing_an_inactive_type_is_rejected_server_side(self, client, db_session):
        token = _signup(client, "civil1@example.com")
        claim = dict(SAMPLE_CLAIMS["tenancy_dispute"]["claim"])
        r = client.post("/api/cases", json=claim, headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 422 and "not open for filing" in r.json()["detail"]

    def test_filing_works_once_preview_types_are_enabled(self, client, db_session):
        token = _signup(client, "civil2@example.com")
        with patch.dict(os.environ, {loader.PREVIEW_ENV: "1"}):
            for t in NEW_TYPES:
                r = client.post("/api/cases", json=dict(SAMPLE_CLAIMS[t]["claim"]), headers={"Authorization": f"Bearer {token}"})
                assert r.status_code == 200, (t, r.text)
                assert r.json()["tier"] == 2

    def test_original_types_are_unaffected(self, client, db_session):
        token = _signup(client, "civil3@example.com")
        r = client.post("/api/cases", json=dict(SAMPLE_CLAIMS["consumer_dispute"]["claim"]), headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200

    def test_sample_claims_exist_for_every_type_and_validate(self, client):
        for dt in loader.DISPUTE_TYPES:
            sample = client.get(f"/api/sample-claim?dispute_type={dt['id']}").json()
            assert sample["claim"]["dispute_type"] == dt["id"], dt["id"]
            ClaimSubmission(**sample["claim"])
