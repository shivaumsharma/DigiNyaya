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

## 4. Still open
* Human labels (the blind sheet) to validate the judge and the disposal tag.
* The leakage-free control (`scripts/build_leakage_free_control.py`) to test whether the skill survives inputs that cannot hint at the decision.
* Intervals above cover sampling noise only, not judge or tag errors.

## 5. Sourcing more contested cases
`scripts/plan_corpus_scale.py` now passes `--max-ex-parte-share 0.3` to every sourcing command. Ex parte decrees past that share
are skipped before any LLM call. Contested cases are scarcer, so lower `--assumed-yield` (or raise `--max-complexes`) when the
cap is on; the measured 27.6% yield was taken with no cap and no pre-screen.
