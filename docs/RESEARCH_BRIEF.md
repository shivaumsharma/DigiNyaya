# DigiNyaya: research brief for initial discussion

Status as of 22 Oct 2026. Every number below was measured on the author's data unless marked otherwise. Where something is
unverified or not yet validated, it says so. Reproduction commands are in section 9.

## 1. The system
DigiNyaya is a working AI-assisted dispute-resolution system for Indian civil disputes (FastAPI backend, React frontend,
Sarvam AI models). A case runs through a staged pipeline: intake and language processing, precedent research (embedding
retrieval over a small precedent corpus), evidence analysis, mediation (a scripted relief calculation with LLM drafting),
and a resolution document. A safety gate escalates to a human reviewer on defined conditions (criminal matter detected,
jurisdiction mismatch, low confidence, too few precedents). Every AI order is labelled a provisional recommendation pending
human sign-off. Optional statute grounding (31 paraphrased provisions, unreviewed by a lawyer) and three preview dispute
types (tenancy, property, employment) exist behind flags.

## 2. The evaluation setup
* **Data:** 23,841 archived real judgments (eCourts district courts, mostly via an open AWS dataset, plus some Indian Kanoon
  material). Each case has an LLM-written description of the facts and an LLM-written account of the real outcome.
* **Scoring:** the pipeline decides each case from the description; an LLM judge (sarvam-105b) compares its decision with the
  real outcome. The judge returns two booleans (did the claimant prevail, per the AI and per the court) and a relief-similarity
  flag; the verdict (match / partial / mismatch) is computed in code. "Full match" = right winner and relief amount within +/-20%.
* **The judge has not been validated against human labels.** A blind 150-case label sheet exists and is unlabelled.
* No live users: this is an offline evaluation on archived judgments.

## 3. Headline numbers (pooled, all cases)
| Metric | Result |
|---|---|
| Resolved without escalation | 65.3% (34.7% escalated) |
| Full match with the court | 56.3% of 15,573 judged (39.4% before an amount fix) |
| Relief amount within +/-20% | 75.7% (7.9% before the fix) |
| Right winner | 75.1%, below the 83.1% of "claimant always wins" |
| Macro-F1 (winner) | 0.634 vs 0.454 for that baseline |
| Confidence vs correctness | correlation 0.19 (weak) |

Caveats already known: the amount fix was designed after looking at this data (not held-out); the judge flipped the winner
on 50 of 679 identical AI outputs (non-deterministic); 62% of descriptions repeat an amount found in the outcome ("amount
echo"), so the amount metric is a soft upper bound.

## 4. Main finding: the corpus is dominated by default decrees
How the 23,623 district-court cases ended (keyword heuristic on cached judgment text; independent check: 97.3% of the cases
tagged ex parte have a real claimant win, as expected):

| Ending | Share |
|---|---|
| Ex parte decree (defendant never appeared) | 53.3% |
| Decreed (contested or unclear) | 24.5% |
| Compromise | 7.4% |
| Dismissed | 7.2% |
| Withdrawn | 4.5% |
| Unknown | 2.3% |
| Dismissed for default | 0.8% |

About 12.7% ended with no decision on the merits yet were accepted as "final decisions" by the sourcing LLM.

## 5. Pipeline accuracy split by how the case ended (15,399 judged cases)
| Group | n | Real claimant wins | Accuracy | Respondent-win recall | Balanced accuracy (95% bootstrap CI) |
|---|---|---|---|---|---|
| Contested (decreed or dismissed) | 5,498 | 73.4% | 61.0% | 53.3% | **58.6% (57.1 to 60.0)** |
| Ended without a merits decision | 2,147 | 68.5% | 65.6% | 59.5% | 63.9% (61.8 to 66.2) |
| Ex parte decree | 7,304 | 97.3% | 89.6% | 38.3% | 64.7% (61.2 to 68.1) |

On contested cases, accuracy minus the always-claimant base rate is -14.0 to -10.7 points (95% CI): raw winner accuracy is
clearly below the trivial predictor, while balanced accuracy is clearly above chance (50%). The pooled 75.1% is therefore not a
meaningful headline.

## 6. Leakage and validity checks
* **Explicit outcome leakage** in the descriptions (outcome phrasing, named judges): about 1.1%; does not drive the numbers.
* **Text-only baseline** (description-only classifier): beats the pipeline pooled on winner accuracy (86.8% vs 75.1%) and
  macro-F1 (0.674 vs 0.634); the pipeline is better at respondent-win recall (54.9% vs 28.7%). On the contested control samples the
  text-only baseline scored exactly the majority rate (no lift).
* **Leakage-free control:** the same cases re-scored with inputs rebuilt from the text before the decision only (paired).

| Sample | n | Winner accuracy | Full match |
|---|---|---|---|
| All cases (mostly ex parte) | 303 | 80.9% to 81.2% (+0.3; 95% CI -4.3 to +5.3) | 61.4% to 60.7% (-0.7; CI -6.9 to +5.6) |
| Contested dev cases | 279 | 71.0% to 73.1% (+2.2; CI -3.2 to +7.2) | 48.7% to 42.7% (-6.1; CI -12.5 to +0.4, p = 0.086) |

No detectable inflation of winner accuracy; a suggestive (not established) drop in full match driven by the amount component.
Limits: 31 to 49 cases per run judged originally but not in the control; inflation under ~5 points cannot be ruled out; control
inputs are LLM-written; verdicts come from the unvalidated judge.

## 7. Data supply (for any scaled version)
* About 230,000 downloadable civil candidates exist in the free eCourts bucket (401,843 in metadata; the rest have no archives).
  Andhra Pradesh 73%, Telangana 26%, Bihar 1%. Delhi, Haryana, Punjab, Kerala and Goa have metadata but no PDFs.
* A pilot (one court, Andhra Pradesh 2023, no ex parte cap, no pre-screen) accepted 50 of 181 attempted cases (27.6%). Text
  extraction succeeded for 93.9% of 360 sampled cases (86.9% without OCR).
* Scaling by volume mostly adds more default decrees; a frozen held-out set of contested cases is built instead
  (7,492 contested cases: 2,231 test, 5,261 dev). Contested categories other than small claims and property are thin
  (test cases: contract 145, tenancy 50, consumer 12, employment 2, partnership 2).

## 8. Known limitations and open items
1. The LLM judge is unvalidated (human labels not yet collected). Every accuracy figure depends on it.
2. The ending tag is a keyword heuristic (validated only indirectly, see section 4).
3. Geographic concentration: almost all cases come from two states.
4. Outcomes and descriptions are LLM-extracted, not human-verified.
5. The amount fix was tuned on the full corpus; only changes designed after the held-out freeze are cleanly testable.
6. Statute grounding and the preview dispute types are unreviewed by a lawyer.
7. A judge-prompt fix (ignoring the "provisional / non-binding" wording on every AI order) is described in the code; its
   effect size has not been verified from the repository and should not be quoted until it is reproduced from the verdict files.

## 9. Reproduce (from `backend/`, needs the local `data_cache`)
`python -m scripts.tag_disposal` (endings), `python -m scripts.accuracy_by_disposal` (section 5, with intervals),
`python -m scripts.freeze_heldout` (frozen split), `python -m scripts.build_leakage_free_control` and
`python -m scripts.leakfree_control_report` (section 6), `python -m scripts.audit_outcome_leakage`, `python -m scripts.corpus_gate`.
More detail: `docs/EVALUATION_FINDINGS.md`, `docs/CORPUS_SCALE.md`, `docs/LEAKAGE_FREE_CONTROL.md`, `docs/LABELLING_GUIDE.md`.

## 10. Possible research directions (for discussion)
1. **Evaluation methodology:** how default decrees and unvalidated LLM judges inflate benchmark results for legal-outcome
   prediction in Indian courts; stratifying by case ending, balanced accuracy, leakage-free controls, and judge validation.
2. **Selective prediction with guarantees:** calibrating when the system should escalate, using the existing fixed-sequence
   threshold tooling (confidence is currently only weakly informative, correlation 0.19).
3. **Judge reliability:** agreement between the LLM judge and human labels with exact confidence bounds (tooling built, labels pending).
4. **Architecture comparison on the frozen contested set:** routing by case type, a learned winner model, calibrated escalation.

## 11. Questions for the professor
* Which of these directions is publishable as a first paper, and what is the minimum validation (human-label sample size, a second labeller) it needs?
* Is a single-state-dominated corpus acceptable if clearly disclosed, or is a second source needed?
* Which venues and what framing (benchmark / methodology / system) would you advise?
