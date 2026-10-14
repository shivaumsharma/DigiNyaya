# Evaluation findings and what can be claimed

All numbers are from the 23,841-case district-court corpus, scored by an LLM judge that has **not** been validated against
human labels. Reproduce with `scripts/tag_disposal.py` and `scripts/accuracy_by_disposal.py` (both need only local files).

## 1. The corpus is mostly cases where the claimant wins by default
How the 23,623 district-court cases ended (keyword heuristic on the cached judgment text): ex parte decree 53.3%, decreed
24.5%, compromise 7.4%, dismissed 7.2%, withdrawn 4.5%, unknown 2.3%, dismissed for default 0.8%. About 12.7% ended without
any decision on the merits (compromise, withdrawn, dismissed for default) yet were accepted as final decisions.

Independent check on the tag: 97.3% of the cases tagged ex parte have a real claimant win, as an ex parte decree should.

## 2. Pipeline winner accuracy by how the case ended (15,399 judged cases)
| Group | n | Real claimant wins | Accuracy | Respondent-win recall | Balanced accuracy (95% CI) |
|---|---|---|---|---|---|
| Contested (decreed or dismissed) | 5,498 | 73.4% | 61.0% | 53.3% | 58.6% (57.1 to 60.0) |
| Ended without a merits decision | 2,147 | 68.5% | 65.6% | 59.5% | 63.9% (61.8 to 66.2) |
| Ex parte decree | 7,304 | 97.3% | 89.6% | 38.3% | 64.7% (61.2 to 68.1) |

On contested cases, accuracy minus the always-claimant base rate is -14.0 to -10.7 points (95% CI): the pipeline's raw winner
accuracy is clearly below the trivial predictor, while its balanced accuracy is clearly above chance.

## 3. Claims that are supported, and ones that are not
Supported (with the caveat on the judge):
* "On 5,498 contested district-court cases the pipeline's balanced accuracy was 58.6% (95% CI 57.1 to 60.0) against 50% chance,
  with 53.3% recall on respondent wins."
* "Raw winner accuracy is below the always-claimant baseline, so it is not reported as a headline."

Not supported:
* A pooled winner-accuracy figure (it mixes default decrees with contested cases).
* "Scored on 60K cases": more cases of the same kind add volume, not evidence. Report the contested subset size.
* Anything that treats the LLM judge's verdicts as ground truth, until the human-label agreement has been run.

## 3b. Leakage-free control (inputs rebuilt from pre-decision text only)
Same cases scored twice, original inputs vs inputs rebuilt from the part of the judgment before the decision. Paired comparison.

| Sample | Cases judged in both | Winner accuracy | Full match | Respondent-win recall |
|---|---|---|---|---|
| All cases (mostly ex parte) | 303 | 80.9% to 81.2% (+0.3; 95% CI -4.3 to +5.3) | 61.4% to 60.7% (-0.7; CI -6.9 to +5.6) | 51.0% to 62.7% (about 50 cases, no interval) |
| Contested dev cases | 279 | 71.0% to 73.1% (+2.2; CI -3.2 to +7.2) | 48.7% to 42.7% (-6.1; CI -12.5 to +0.4, p = 0.086) | 44.7% to 47.1% |

Reading: no detectable inflation of winner accuracy. On contested cases full match was 6.1 points lower, driven by the amount
component (within +/-20%: 77.5% to 73.6%), with an interval that just reaches zero: suggestive of some amount echo in the
original descriptions, not established. The text-only baseline scored exactly the majority rate on both versions. The predicted
winner changed in about 20% of cases, partly judge and pipeline noise.

Limits: 31 to 49 cases per run were judged originally but not in the control, so the comparison is on a subset; an inflation
under about 5 points cannot be ruled out; the control inputs are LLM-written; the verdicts come from the unvalidated judge.

Supportable wording: "Rebuilding inputs from pre-decision text did not reduce winner accuracy on contested cases (+2.2 points,
95% CI -3.2 to +7.2, n = 279). Full match was 6.1 points lower (95% CI -12.5 to +0.4), so the amount component may be modestly inflated."

## 4. Still open
* Human labels (the blind sheet) to validate the judge and the disposal tag.
* Intervals above cover sampling noise only, not judge or tag errors.

## 5. Sourcing more contested cases
`scripts/plan_corpus_scale.py` now passes `--max-ex-parte-share 0.3` to every sourcing command. Ex parte decrees past that share
are skipped before any LLM call. Contested cases are scarcer, so lower `--assumed-yield` (or raise `--max-complexes`) when the
cap is on; the measured 27.6% yield was taken with no cap and no pre-screen.

## 6. Held-out test set (freeze before any rework)
`python -m scripts.freeze_heldout` splits the contested cases into dev and test by a hash of the case id (stable as the corpus
grows, refuses to overwrite). Develop and compare on `python -m scripts.accuracy_by_disposal --split dev`; run `--split test`
only for the final reported comparison. The existing figures above were computed on all cases, so the test set is clean only
for changes designed after it was frozen.
