"""Lightweight keyword / signal extraction shared by the agents.

This is deliberately dependency-free (no external LLM call) so the demo is
fully deterministic and offline. The signal maps mirror the tags used in the
precedent corpus so the Research Agent can do meaningful relevance matching.
"""

from __future__ import annotations

import re

from ..data.loader import effective_type

# Maps a domain signal -> trigger phrases that may appear in free text.
SIGNAL_LEXICON: dict[str, list[str]] = {
    # "not deliver"/"never deliver" alone stopped matching real text once
    # SIGNAL_LEXICON triggers moved to \b-anchored regex (see
    # _SIGNAL_PATTERNS below): the trailing \b requires a word boundary
    # right after "deliver", which the common past-tense phrasing "never
    # delivered" doesn't have (no boundary between "deliver" and "ed").
    # Confirmed via the golden eval suite: a textbook "never delivered
    # though marked delivered" non-delivery case silently stopped matching
    # this signal, which cascaded into no recognised subtype -> Tier 2
    # instead of the expected Tier 1. Listing the inflected forms
    # explicitly (rather than loosening the shared \b regex, which exists
    # specifically to stop short/generic triggers matching inside unrelated
    # words) fixes this without reopening that original bug.
    "non_delivery": [
        "not deliver", "not delivered", "not delivering",
        "never deliver", "never delivered", "never delivering",
        "non-delivery", "did not arrive", "didn't arrive",
        "not received", "never received", "undelivered",
    ],
    "defective_product": ["defective", "not working", "stopped working", "faulty", "broken", "malfunction", "damaged", "defect"],
    "refund": ["refund", "money back", "return my money", "reimburse"],
    "replacement": ["replace", "replacement", "exchange"],
    "counterfeit": ["fake", "counterfeit", "duplicate", "not genuine", "first copy"],
    # "false" and "different from" were removed -- both are generic enough to
    # match completely unrelated text (e.g. "the defendant filed false
    # complaints" -- nothing to do with a product being misrepresented) and
    # were confirmed, via a real case in scripts/judge_real_outcomes.py's
    # eval set, to inject a fabricated "product misrepresentation" narrative
    # into Agent 3/5's prompts for a case that was never about a product at
    # all (a construction-firm profit-sharing dispute). See
    # [[diginyaya_real_judgment_eval]] memory / IK-EVAL-125730407.
    "misrepresentation": ["misled", "misleading", "misrepresent", "not as described", "refurbished"],
    "warranty": ["warranty", "guarantee", "guaranteed"],
    "ecommerce": ["online", "website", "app", "ecommerce", "e-commerce", "marketplace", "ordered online"],
    # Bare "support" removed -- confirmed, via a real case, to false-positive
    # on generic uses that have nothing to do with customer support (e.g. a
    # hostile respondent reply "why do you support such application" --
    # "support" there means "endorse", not "customer support"). "customer
    # support"/"technical support" are specific enough phrases to keep.
    "service_deficiency": ["service", "customer support", "technical support", "customer care", "no response", "ignored", "deficiency"],
    "repair_delay": ["repair", "service centre", "service center", "repair delay", "delay in repair", "waiting for repair"],
    "banking": ["bank", "account", "debit", "atm", "upi", "transaction"],
    "unauthorized_transaction": ["unauthorized", "unauthorised", "fraudulent", "without my consent", "didn't authorise"],
    "wrongful_billing": ["overcharged", "wrongful bill", "billed", "extra charge", "charged twice", "double charge"],
    "subscription": ["subscription", "auto-renew", "auto renew", "recurring", "renewed"],
    "insurance": ["insurance", "policy", "claim repudiat", "premium"],
    "food_delivery": ["food", "restaurant", "contaminated", "stale", "spoiled"],
    # "booking" and "hotel" removed -- both confirmed, via real cases, to
    # false-positive well outside travel (an apartment "booking amount" in a
    # real-estate refund case; a "5-star hotel" as the property TYPE in a
    # land-allotment-cancellation case, nothing to do with a travel
    # booking). "flight"/"ticket"/"airline" are specific enough to keep.
    "travel": ["flight", "ticket", "airline", "cancelled flight"],
    "real_estate": ["flat", "apartment", "builder", "possession", "property"],
    "electronics": ["laptop", "phone", "mobile", "tv", "television", "electronic", "gadget", "appliance"],
    # Bare "car" removed -- confirmed to false-positive on "car park" (a
    # property-dispute case describing a building's parking facility, not a
    # vehicle dispute). "vehicle"/"automobile"/"engine"/"bike" don't share
    # this specific collision and are kept.
    "automobile": ["vehicle", "bike", "automobile", "engine"],
    "furniture": ["furniture", "sofa", "table", "chair", "bed"],
    # Money recovery / contract breach / cheque bounce signals.
    "loan_default": ["loan", "lent", "lend", "borrowed", "friendly loan", "promissory note", "iou", "did not repay", "failed to repay", "hasn't repaid", "has not repaid"],
    "unpaid_dues": ["unpaid", "outstanding amount", "outstanding dues", "owes me", "owes him", "owes her", "hasn't paid", "has not paid", "failed to pay", "non-payment", "overdue payment", "did not pay back"],
    "cheque_dishonour": ["cheque bounce", "cheque bounced", "dishonoured", "dishonored", "insufficient funds", "cheque returned", "stop payment", "section 138", "ni act", "post-dated cheque", "postdated cheque"],
    "breach_of_agreement": ["breach of contract", "breached the contract", "breached the agreement", "breach of agreement", "violated the contract", "violated the agreement", "failed to perform", "failed to complete the work", "broke the agreement", "non-performance", "did not honour the agreement", "did not honor the agreement", "did not fulfil the contract", "did not fulfill the contract"],
}

# Short noun-phrase used to fill "You are mediating a ___" / "summarising this
# ___" style prompt text, so the LLM isn't misled into applying Consumer
# Protection Act framing to a loan, contract, or cheque-bounce case.
DISPUTE_TYPE_PROMPT_LABELS: dict[str, str] = {
    "consumer_dispute": "consumer dispute",
    "money_recovery": "money recovery / loan repayment dispute",
    "contract_breach": "breach of contract dispute",
    "cheque_bounce": "cheque bounce (dishonoured cheque) dispute",
    "tenancy_dispute": "tenancy / rent dispute",
    "property_dispute": "property / neighbour dispute",
    "employment_dispute": "employment / unpaid wages dispute",
}


def dispute_label(dispute_type: str) -> str:
    return DISPUTE_TYPE_PROMPT_LABELS.get(dispute_type, "dispute")


# Real-judgment testing (IK-EVAL-175380192, IK-EVAL-38336739 -- both bank-vs-
# defaulting-borrower recovery suits) found the direction of relief was
# already correct (the respondent/borrower was correctly ordered to pay the
# claimant/bank), but mediation.py's only monetary vocabulary
# ("full_refund"/"partial_refund"/"compensation") reads as consumer-refund
# framing -- a poor semantic fit for a debt-recovery decree, and gives an
# LLM judge no lexical anchor to map "claimant"/"respondent" onto
# "bank"/"borrower" when comparing against a real judgment's own wording.
_DEBT_RECOVERY_DISPUTE_TYPES = frozenset({"money_recovery", "cheque_bounce"})
_MONETARY_RELIEF_PHRASES = {
    "full_refund": "a full refund",
    "partial_refund": "a partial refund",
    "compensation": "compensation",
}
_DEBT_RECOVERY_RELIEF_PHRASES = {
    "full_refund": "recovery of the outstanding debt in full",
    "partial_refund": "partial recovery of the outstanding debt",
    "compensation": "compensation",
}


def monetary_relief_phrase(relief_kind: str, dispute_type: str) -> str:
    """Human-readable phrase for a monetary (non-dismissed, non-non-monetary)
    relief kind, worded appropriately for the dispute type -- "a full
    refund" reads correctly for a consumer dispute, but "recovery of the
    outstanding debt" is the correct framing for money_recovery/
    cheque_bounce, where the claimant is a creditor collecting a debt, not a
    buyer being refunded."""
    table = _DEBT_RECOVERY_RELIEF_PHRASES if effective_type(dispute_type) in _DEBT_RECOVERY_DISPUTE_TYPES else _MONETARY_RELIEF_PHRASES
    return table.get(relief_kind, relief_kind.replace("_", " "))


# What KIND of relief is actually being asked for -- distinct from the
# dispute-subtype signals above. Found via real-judgment testing
# (scripts/judge_real_outcomes.py) that resolution.py could only ever draft
# a "pay the claimant Rs X" order, even when the real ask (and the real
# court's actual order) was to remove/undo something, declare a document
# void, replace goods in kind, or hand over possession -- 8 of 34 compared
# cases failed for exactly this reason, not because the money was wrong.
# Checked in this priority order because a claim can mention several; e.g.
# eviction suits often also use "possession", so possession is checked
# first to avoid it being masked by a later, less specific match.
_RELIEF_TYPE_LEXICON: list[tuple[str, tuple[str, ...]]] = [
    # Checked first, ahead of everything else: a case that belongs in
    # arbitration is a THRESHOLD/jurisdictional issue -- this forum isn't
    # deciding the merits at all, which should never be masked by an
    # incidental injunction/declaration/possession mention elsewhere in the
    # same text (real-judgment testing: IK-EVAL-91437553 -- the real court
    # referred the parties to arbitration under the partnership deed's
    # clause, but the system had no such outcome available at all and
    # defaulted to a monetary compensation figure it should never have
    # computed).
    ("arbitration_referral", (
        "refer to arbitration", "referred to arbitration", "arbitration clause",
        "arbitration agreement", "bound by the arbitration clause", "appoint an arbitrator",
        "arbitration and conciliation act", "section 8 of the arbitration",
    )),
    # Checked ahead of possession/injunction: partnership/co-ownership
    # disputes routinely ask for BOTH a partition and a protective injunction
    # in the same breath (e.g. "sought a partition of the property... and an
    # injunction to prevent the respondent from alienating... his share") --
    # partition is the more specific, primary relief in these cases (declares
    # each party's share), so it should win the single relief_kind slot
    # rather than the more generic, secondary injunction mention. Found via
    # real-judgment testing: partnership_business_disputes had no relief type
    # for this at all and defaulted to a monetary compensation figure no real
    # court in the sample actually awarded.
    #
    # NOTE: the fixed-phrase list this used to be ("partition of the
    # property", "seeking a partition", etc.) missed the majority of real
    # property_neighbor_disputes partition suits -- courts overwhelmingly
    # phrase this as "suit for the partition of joint family/ancestral
    # property", "partition and separate possession", "partition of
    # ancestral agricultural lands", none of which contain any of those
    # fixed phrases verbatim. See _PARTITION_RE below (checked directly in
    # detect_relief_type, same priority slot) for the actual matching logic
    # now used -- this tuple only carries the partnership-dissolution
    # phrasings, which don't collide with the bare "partition" word at all.
    ("partition", (
        "dissolution of the partnership",
        "dissolve the partnership", "rendition of accounts", "accounts of the partnership",
    )),
    # heirship_declaration is NOT in this list -- see _HEIRSHIP_DECLARATION_RE
    # and its own check in detect_relief_type() below. A plain substring
    # trigger on "legal heir"/"legal heirs" was tried first and measured to
    # be too promiscuous: real-judgment testing found it firing on cases
    # where "legal heir(s)" is mere PARTY-IDENTITY background (a partition
    # suit "against his mother and brother... to divide properties... one
    # share to each co-heir", a money-recovery suit "against the legal heirs
    # of a deceased individual" being sued for a debt) rather than the actual
    # relief sought, wrongly overriding the correct partition/monetary
    # classification. Requiring a declaratory word near "heir" (see the
    # regex) is what actually distinguishes "asking the court to declare who
    # the heirs are" from "the parties happen to be heirs of someone."
    # Checked ahead of possession/injunction for the same reason as
    # partition above: a specific-performance suit routinely also mentions
    # "consequential injunction" or a "possessory agreement" in the same
    # breath ("suit for specific performance of a contract and consequential
    # injunction", "specific performance of a possessory agreement of sale
    # and a permanent injunction") -- specific performance (compel the
    # respondent to actually complete the contract, e.g. execute and
    # register the sale deed) is the more specific, primary ask in these
    # cases. Found via real-judgment testing at the 438-case scale: 38 real
    # cases (both IK-EVAL and DC-EVAL, spanning contract_disputes and
    # property_neighbor_disputes) use this exact phrase, and BEFORE this
    # trigger existed 26 of the 38 (68%) fell through to the generic
    # "monetary" bucket -- resolution.py could only ever draft "pay the
    # claimant Rs X", even though the real ask (and what real courts
    # actually ordered: "specific performance and registration",
    # "specific performance of a registered sale deed") was to complete the
    # transaction, not pay damages in place of it. Deliberately just the one
    # phrase, not a wider net of paraphrases -- it already accounts for
    # 100% of the identified population (38/38 contain it verbatim), so
    # guessing at looser alternatives would only add false-positive risk
    # with no evidence it's needed.
    ("specific_performance", ("specific performance",)),
    # Employment/termination disputes: getting the job back (+ back wages) is
    # the standard Labour Court remedy for illegal termination, distinct from
    # a one-off monetary damages award. Checked ahead of possession/
    # injunction/declaration since "reinstate" can co-occur with generic
    # relief language but is far more specific to what a Labour Court
    # actually orders in these matters. Found via real-judgment testing:
    # employment_disputes had no relief type for this at all and defaulted
    # to a monetary compensation figure real Labour Courts didn't award in
    # multiple sampled cases (they ordered reinstatement + back wages, or
    # nothing, not a lump-sum payment).
    # NOTE: bare "reinstate"/"reinstatement"/"reinstated" deliberately
    # excluded -- confirmed, via a real case (IK-EVAL-172879753, a housing
    # refund dispute), to false-positive on non-employment senses of the
    # same word ("...cancellations and reinstatements of sanctioned plans by
    # [a development authority]" -- reinstating an administrative approval,
    # not a person). The phrases below are specific enough to real Labour
    # Court terminology that they don't share this risk.
    ("reinstatement", (
        "did not reinstate", "seeking reinstatement", "restore him to his post",
        "restore her to her post", "continuity of service", "back wages",
    )),
    # Real-judgment testing at 257-case scale found this original 5-phrase
    # list missed the large majority of actual possession suits: real
    # judgment text overwhelmingly phrases the ask as bare "possession"
    # ("suit for possession", "recovery of possession", "seeking possession
    # of the property") rather than the narrower "vacant possession"/"hand
    # over possession"/"recover possession" phrasings, and South Indian
    # courts commonly use "ejectment" instead of "eviction" entirely (see
    # IK-EVAL-98413367 -- a Bangalore ejectment suit that fell through to
    # "monetary" and lost its correct possession relief type as a result).
    # Deliberately NOT adding a bare "possession of" trigger -- that false-
    # positived on cases merely describing a party's EXISTING possession as
    # background fact (e.g. "the plaintiff claimed to be in possession of
    # the property" in a specific-performance contract dispute), not the
    # relief actually being sought.
    ("possession", (
        "vacant possession", "hand over possession", "eviction", "evict",
        "recover possession", "recovery of possession", "suit for possession",
        "seeking possession", "delivery of possession", "restore possession",
        "ejectment", "quit and vacate",
    )),
    ("injunction", ("injunction", "restrain", "restraining order", "stop the respondent", "cease and desist", "remove the", "removal of the")),
    # "declaration" (the noun) added below after finding, via real-judgment
    # testing, that it does NOT contain "declare" as a substring -- they
    # diverge at the 7th character ("declar-E" vs "declar-ATION") -- so
    # phrasing like "sought a declaration of their status" or "obtain a
    # declaration recognising them as..." silently fell through to the
    # generic "monetary" bucket even though "declare"/"declaration that"
    # were already triggers. Confirmed this alone accounts for cases that
    # otherwise had no non-monetary trigger fire at all, not just heirship
    # ones (heirship specifically now has its own, more specific bucket
    # above, checked first).
    ("declaration", ("declare", "declaration", "declared void", "null and void", "declaratory")),
    ("replacement", ("replace the", "replacement of", "provide a replacement", "exchange the", "provide a new", "seeking a new", "delivery of a new", "deliver a new")),
]

# A small set of STRONG, unambiguous declaration phrases, checked before the
# general priority-ordered scan above. Real-judgment testing (IK-EVAL-
# 48210005) found a case whose text mentioned both an injunction-flavored
# phrase ("mandatory injunction to regain possession") and this stronger,
# more specific declaratory phrase ("declared void") -- the fixed scan order
# above lets the earlier, more generic "injunction"/"restrain" entry win
# every time it co-occurs with a later entry, even when the later signal is
# far more specific to what was actually decided. These phrases are
# specific enough that their presence should outrank a co-occurring generic
# injunction/restrain mention, which is often just incidental/interim
# language rather than the actual relief granted.
_STRONG_DECLARATION_SIGNALS: tuple[str, ...] = ("null and void", "declared void")

# A succession/heirship suit is asking the court to establish WHO the heirs
# are -- logically prior to dividing an estate among them (checked ahead of
# "partition" below) and a materially different ask from "declare this
# instrument void" (the generic "declaration" bucket's original use case,
# see _STRONG_DECLARATION_SIGNALS above). Requires a declaratory word
# (declare/declared/declaration/declaratory) within ~100 characters of a
# heir* word, rather than a bare "legal heir" substring -- see the comment
# where the old, too-promiscuous version of this trigger used to live in
# _RELIEF_TYPE_LEXICON for why proximity matters here. "succession
# certificate" and "declar*...successor(s)" are separate, narrower alternate
# phrasings for the same ask that don't happen to use the word "heir" at all.
#
# WHY THIS EXISTS (numbers below are a one-time historical finding, not a
# reproducible fixture against current code -- the whole point is that the
# code changed because of them, so re-measuring today no longer shows the
# "before" state): found via real-judgment testing that a genuine heirship/
# succession suit was, before this trigger and resolution.py's dedicated
# heirship_declaration action phrase both existed, either (a) missed by
# every relief-type trigger and defaulted to a flat, unrelated claim amount
# -- a status/identity question has no money at stake, so "monetary" was
# never going to be right -- or (b) routed to the generic "declaration"
# bucket, whose "void the respondent's contrary instrument" template
# doesn't match what a heirship decree actually grants. (a) mismatched the
# real court's outcome in every case sampled; (b) matched only a minority
# of the time. Both gaps are what this trigger and that dedicated action
# phrase exist to close. For a CURRENT read on how heirship_declaration
# cases are actually doing, query data_cache/real_judgment_verdict_
# comparison.json for cases matching this regex rather than trusting any
# number written here -- it will already be stale by the time it's read.
_HEIRSHIP_DECLARATION_RE = re.compile(
    r"declar\w*.{0,100}heir\w*|heir\w*.{0,100}declar\w*|succession certificate|declar\w*.{0,60}successors?\b",
    re.IGNORECASE | re.DOTALL,
)

# Bare "partition" as a word, EXCLUDING "partition wall"/"partition fence" --
# real property_neighbor_disputes boundary cases use that exact phrase for a
# physical dividing wall between two plots (a completely different fact
# pattern from a co-ownership partition suit), confirmed via a real case
# (IK-EVAL-194178256: "demolished a common partition wall") that would
# otherwise misclassify an ejectment/injunction dispute as a partition suit.
# Checked against the full property_neighbor_disputes corpus: 218/970 cases
# contain the word "partition" at all, and exactly 1 of those is the wall/
# fence sense -- the fixed-phrase list this replaces required one of a
# handful of exact orderings ("partition of the property", "partition
# suit", ...) and missed real phrasings like "suit for the partition of
# joint family properties", "partition of ancestral agricultural lands",
# and "civil suit for partition and separate possession" entirely, none of
# which contain any of those fixed phrases verbatim.
_PARTITION_RE = re.compile(r"partition(?!\s+wall|\s+fence)", re.IGNORECASE)


def detect_relief_type(text: str) -> str:
    """Return the primary non-monetary relief type sought, or 'monetary' if
    none of the recognised non-monetary patterns are present. A claim may
    ALSO seek incidental damages alongside a non-monetary primary ask (very
    common in practice -- e.g. injunction + damages) -- this only decides
    the PRIMARY relief type; callers still compute a monetary amount
    separately for any secondary/incidental compensation."""
    lowered = text.lower()
    arbitration_triggers = dict(_RELIEF_TYPE_LEXICON)["arbitration_referral"]
    if any(trigger in lowered for trigger in arbitration_triggers):
        return "arbitration_referral"
    # Checked BEFORE _STRONG_DECLARATION_SIGNALS: a will-contest-plus-
    # heirship fact pattern is common in real succession disputes ("sought a
    # declaration that they are the legal heirs and that the fraudulent will
    # is null and void"), and heirship is the more specific of the two asks
    # -- the same "more specific wins" reasoning already used for why
    # "partition" is checked ahead of "possession"/"injunction" below.
    # Getting this order backwards would let a co-occurring "null and void"
    # mention route the case to the generic declaration bucket even when
    # heirship is the actual primary relief sought.
    if _HEIRSHIP_DECLARATION_RE.search(text):
        return "heirship_declaration"
    if any(sig in lowered for sig in _STRONG_DECLARATION_SIGNALS):
        return "declaration"
    if _PARTITION_RE.search(text):
        return "partition"
    for relief_type, triggers in _RELIEF_TYPE_LEXICON:
        if any(trigger in lowered for trigger in triggers):
            return relief_type
    return "monetary"

# Captures an optional Indian numeric-scale word (lakh/crore) after the
# number. Without this, "Rs. 5.6 crores" parsed as literally 5.6 -- a real
# amount off by a factor of 10 million, confirmed against a real judgment
# during scripts/judge_real_outcomes.py (claim inferred as ~Rs 2, real award
# was Rs 1.37 crore). Indian rupee amounts are routinely written this way
# ("Rs 50 lakh", "2.5 crore") rather than with full digit strings.
_AMOUNT_RE = re.compile(
    r"(?:rs\.?|inr|₹)\s*([\d,]+(?:\.\d+)?)\s*(crores?|cr\.?|lakhs?|lacs?)?",
    re.IGNORECASE,
)
_SCALE_MULTIPLIERS = {
    "crore": 10_000_000, "crores": 10_000_000, "cr": 10_000_000,
    "lakh": 100_000, "lakhs": 100_000, "lac": 100_000, "lacs": 100_000,
}
_DATE_RE = re.compile(
    r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{2,4})\b",
    re.IGNORECASE,
)


# Phrases indicating the respondent never actually engaged with the
# proceeding (defaulted / ex-parte) despite a submission record existing --
# distinct from a genuine denial. Real courts almost always decree for the
# claimant by default in these cases, so this must be scored like an
# uncontested case, not a generic firm denial.
_NO_DEFENSE_LEXICON = (
    "did not enter an appearance", "did not appear", "failed to appear",
    "no defense was filed", "no defence was filed",
    "did not present any defense", "did not present any defence",
    "without a sustainable defense", "without a sustainable defence",
    "failed to apply for leave to defend", "did not apply for leave to defend",
    "did not file a written statement", "proceeded ex-parte", "ex parte",
)

# Markers of a SPECIFIC, dispositive legal or factual defense (a named
# ground or a concrete counter-fact) as opposed to a bare denial. Found via
# the 46-case real-judgment benchmark (scripts/judge_real_outcomes.py):
# analysis.py's respondent strength previously reacted only to whether a
# counter-offer was made, so a rock-solid "no contract existed" / "barred by
# limitation" defense scored identically to a vague denial -- the dominant
# remaining failure mode (11/23 mismatches) was the AI awarding relief the
# real court had refused because of exactly this kind of defense.
_DEFENSE_SUBSTANCE_LEXICON = (
    "no contract", "no agreement", "not a tenant", "not an employee",
    "employer-employee relationship", "employment relationship",
    "no privity", "landlord-tenant relationship", "lack of jurisdiction",
    "no jurisdiction", "jurisdiction", "limitation", "barred",
    "not maintainable", "maintainable", "locus standi", "arbitration",
    "res judicata", "estoppel", "condonation", "cause of action",
    "non-joinder", "already paid", "already delivered", "took delivery",
    "cleared all dues", "full and final settlement", "settlement was reached",
    "forgery", "encroach", "title document", "site plan", "possession since",
    "never worked for", "never issued", "stolen", "stop payment",
    # Tenancy/ownership-protection defenses: found via real-judgment testing
    # to be a dominant, previously-unscored mismatch pattern for tenancy
    # disputes -- e.g. a landlord's eviction suit failing because the
    # landlord "failed to prove ownership" is exactly as dispositive as any
    # of the entries above, but none of them matched this common phrasing.
    "failed to prove ownership", "failed to prove his ownership",
    "failed to prove her ownership", "failed to prove title",
    "protected tenant", "statutory tenant", "rent control act",
    "bonafide requirement", "bona fide requirement", "not proved",
    "did not prove", "no relationship of landlord and tenant",
    "denied the existence of tenancy", "adverse possession",
)
# Adding "lack of privity"/"lack of title"/similar title-dispute phrasing
# here was tried and reverted: motivated by 4 of 9 sampled tenancy over-
# granting mismatches (AI granted possession, real court dismissed) having
# a genuinely dispositive, SUPPORTED defense that scored 0.0 (neither
# phrasing matched anything above). Adding it looked safe -- pure additive
# lexicon coverage, no downweighting -- but POC-tested on the exact 24
# cases it changes: 0 improved, 4 regressed (including the case that
# motivated it, IK-EVAL-110485783, which stayed "mismatch" anyway). This is
# now the THIRD reverted attempt at tenancy's over-granting pattern (the
# c_strength > r_strength guard in mediation.py, this lexicon addition) --
# the real signal driving it is still not identified. See
# [[diginyaya_real_judgment_eval]].

# Pure threshold/procedural objections -- a SUBSET of
# _DEFENSE_SUBSTANCE_LEXICON, not a separate list, so a defense containing
# one of these is still counted for the "does this defense have any
# substance at all" question above; this only distinguishes, WITHIN that
# already-matched population, defenses that raise ONLY a jurisdiction/
# limitation/maintainability-type objection with nothing on the merits.
# Raising the term is not evidence it succeeds -- real courts routinely
# hear and reject a bare limitation/jurisdiction plea and then decide the
# merits anyway. A prior attempt at this exact distinction (a hard
# procedural-vs-merits split, scored independently rather than as a
# same-lexicon subset) was tried TWICE and reverted both times: it
# regressed EVERY category measured full-corpus (see score_defense_
# substance's own docstring). This is a narrower, smaller-magnitude version
# (a capped ceiling only on the PURE-procedural case, not a wholesale
# rescoring) explicitly gated on a small-scale POC before any full run --
# see [[diginyaya_real_judgment_eval]].
_PROCEDURAL_ONLY_TERMS = frozenset({
    "lack of jurisdiction", "no jurisdiction", "jurisdiction", "limitation",
    "barred", "not maintainable", "maintainable", "locus standi",
    "res judicata", "estoppel", "condonation", "cause of action", "non-joinder",
})


def defendant_defaulted(text: str) -> bool:
    """True if the respondent's own statement describes a failure to engage
    with the proceeding at all (ex-parte / no defense filed), rather than a
    denial on the merits."""
    if not text:
        return False
    lowered = text.lower()
    return any(p in lowered for p in _NO_DEFENSE_LEXICON)



# Real-judgment testing found the dominant remaining failure mode isn't
# missing a dispositive-sounding phrase -- it's the opposite: naming a real
# legal doctrine ("SICA protection", "Order 23 Rule 1") is being treated as
# automatically dispositive just because it sounds official, regardless of
# whether the respondent actually backed it with anything. Real courts
# reject unsupported invocations of a doctrine constantly; the doctrine's
# NAME isn't evidence, a checkable fact behind it is. This marker phrase is
# how scripts/run_real_judgment_eval.py's _build_ctx() flags that no such
# fact was extracted -- see that module for the corresponding
# "supported by specific facts" phrase used when one was found.
_UNSUPPORTED_ASSERTION_MARKER = "without citing any specific supporting facts"


def score_defense_substance(text: str) -> float:
    """Heuristic 0..1 estimate of how specific/dispositive a respondent's
    defense is, as opposed to a bare, unparticularised denial. Deliberately
    keyword-based (not an LLM call) so the Analysis agent stays free and
    deterministic on every case, including uncontested ones.

    A named doctrine with no concrete fact behind it (the unsupported-
    assertion marker) is capped well below what any keyword match alone
    would otherwise score -- a bare "we are protected under X" is not
    meaningfully different from a bare denial just because X is a real
    legal term.

    NOTE: a procedural-vs-merits split (downweighting jurisdiction/
    limitation-only defenses) was tried TWICE and reverted both times --
    each time it moved individual traced cases in the right direction, but
    measured end-to-end against the full corpus it made EVERY category flat
    or worse (overall 30.9% -> 28.7%, small-claims itself 9.2% -> 6.7%,
    employment 15.4% -> 0.0%).

    A THIRD, narrower attempt (this one, see _PROCEDURAL_ONLY_TERMS below --
    a same-lexicon subset capped only when EVERY matched term is a bare
    threshold objection, not a wholesale rescoring) was POC-tested on the
    exact 142 cases it changes before being kept: net positive (22 ruling-
    only improved vs. 18 regressed), with real gains in the categories that
    motivated it (small_claims_debt_recovery ruling-only 32.1%->46.4%,
    property_neighbor_disputes 51.8%->55.4%) and a genuine, accepted trade-
    off in contract_disputes (50.0%->40.0%, n=20) -- 2 real cases where a
    jurisdiction/limitation defense the cap now downweights had actually
    succeeded in real court. Not a free win, but a real net positive,
    unlike the two prior attempts. Do not widen this further (e.g. back to
    a flat split covering ANY defense, not just pure-procedural-only ones)
    without a fresh proof-of-concept sample. See [[diginyaya_real_judgment_eval]].
    """
    if not text:
        return 0.0
    lowered = text.lower()
    matched = [m for m in _DEFENSE_SUBSTANCE_LEXICON if m in lowered]
    if not matched:
        return 0.0
    if _UNSUPPORTED_ASSERTION_MARKER in lowered:
        return 0.2
    matches = len(matched)
    score = 0.5 if matches == 1 else 0.75 if matches == 2 else 0.9
    if all(m in _PROCEDURAL_ONLY_TERMS for m in matched):
        # Every matched term is a bare threshold objection with nothing on
        # the merits -- cap well below what the same match count would
        # otherwise score, rather than removing the signal entirely (a
        # respondent who raises the SAME procedural point three times over
        # still hasn't raised a merits defense).
        score = min(score, 0.3)
    return score


_DOCUMENTARY_INSTRUMENT_LEXICON = (
    "promissory note", "loan agreement", "hypothecation agreement",
    "signed agreement", "signed contract", "written agreement",
    "registered deed", "registered sale deed", "sale deed", "lease deed",
    "rent agreement", "title deed", "invoice", "purchase order", "cheque",
    "demand draft", "bank statement", "receipt no", "agreement to sell",
    "loan document", "iou", "acknowledgement of debt", "bond",
)
# NOTE: informal payment instruments (RTGS/NEFT/bank transfer, "agreement
# to repay") were tried here and reverted -- the detector correctly fired
# on the real cases it targeted (net_strength moved the right direction),
# but not far enough to clear mediation.py's near-tie dismissal floor, and
# it produced no net accuracy gain measured end-to-end. See
# [[diginyaya_real_judgment_eval]]: the bottleneck is that floor, not this
# lexicon's coverage.


def has_documentary_instrument(text: str) -> bool:
    """True if the CLAIMANT's own narrative names a specific transactional
    instrument (a signed agreement, cheque, invoice, promissory note...)
    rather than only a general grievance -- the same "concrete fact, not
    just an assertion" distinction score_defense_substance already applies
    to the respondent's side, mirrored here for the claimant's evidence.

    Deliberately keyword-based (not an LLM call), for the same reason as
    score_defense_substance: small-claims and debt-recovery cases typically
    rest on exactly one such instrument with a thin evidence *count*, which
    the count-only scoring in analysis.py has no way to reward -- see
    [[diginyaya_real_judgment_eval]] for why that specifically depresses
    accuracy in those two categories.
    """
    if not text:
        return False
    lowered = text.lower()
    return any(m in lowered for m in _DOCUMENTARY_INSTRUMENT_LEXICON)


# Word-boundary versions of every SIGNAL_LEXICON trigger, compiled once at
# import time rather than per call. Plain substring matching (the previous
# implementation) let short/generic triggers match inside unrelated words --
# confirmed via a real case where "app" (meant for "mobile app") matched
# inside "appeals" and "applications", words that appear in nearly every
# legal document, injecting a fabricated "ecommerce" signal into an
# unrelated property dispute. \b works correctly for multi-word triggers
# too (e.g. "cheque bounce") since it only anchors the start/end of the
# whole phrase, not each internal word.
_SIGNAL_PATTERNS: dict[str, re.Pattern[str]] = {
    signal: re.compile(r"\b(?:" + "|".join(re.escape(t) for t in triggers) + r")\b")
    for signal, triggers in SIGNAL_LEXICON.items()
}


def extract_signals(text: str) -> list[str]:
    """Return the domain signals present in a block of free text."""
    lowered = text.lower()
    return [signal for signal, pattern in _SIGNAL_PATTERNS.items() if pattern.search(lowered)]


def extract_amounts(text: str) -> list[float]:
    out: list[float] = []
    for raw, scale in _AMOUNT_RE.findall(text):
        try:
            amount = float(raw.replace(",", ""))
        except ValueError:
            continue
        multiplier = _SCALE_MULTIPLIERS.get(scale.lower().rstrip("."), 1) if scale else 1
        out.append(amount * multiplier)
    return out


def extract_dates(text: str) -> list[str]:
    return _DATE_RE.findall(text)


def inr(amount: float) -> str:
    """Format a number in the Indian numbering system with a rupee symbol."""
    amount = round(float(amount), 2)
    whole = int(amount)
    frac = amount - whole
    s = str(whole)
    if len(s) > 3:
        last3 = s[-3:]
        rest = s[:-3]
        groups = []
        while len(rest) > 2:
            groups.insert(0, rest[-2:])
            rest = rest[:-2]
        if rest:
            groups.insert(0, rest)
        s = ",".join(groups) + "," + last3
    out = f"Rs. {s}"
    if frac:
        out += f".{int(round(frac * 100)):02d}"
    return out
