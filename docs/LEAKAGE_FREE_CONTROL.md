# Leakage-free control experiment

## Why this exists
Every eval case's inputs were written by an LLM that could see the court's decision:
- **`case_description`** was written while the LLM also wrote `expected_outcome`, from the whole judgment.
- **`signals`** (what the pipeline actually consumes: evidence count, defence substance) were extracted by
  `scripts/extract_case_signals.py`, which sends `_window(body)` = the first 6,000 characters **plus the last 2,500**.
  The last 2,500 is where the conclusion sits, and for any judgment under 8,500 characters the model sees the **whole**
  text. The prompt only *instructs* it to ignore the conclusion. One signal ("defence collapsed procedurally, treat as
  unsupported") depends on what happened later.

`scripts/audit_outcome_leakage.py` found explicit result phrasing in only 1.1% of descriptions and showed it does not
drive the numbers. It **cannot** see subtle leakage. This control can: rebuild each sampled case's inputs from the
text *before* the decision, re-run the same pipeline and judge, and compare with the original on the same cases.

## What it does
`scripts.build_leakage_free_control`, per sampled case:
1. loads the cached raw judgment (`data_cache/indiankanoon/<docid>.json`; no new fetches);
2. cuts it before the decision: at the earliest decisive marker ("in the result", "the suit is decreed", ...) or at
   `--max-fraction` (default 60%) of the text, whichever is first, then trims back to a complete sentence;
3. asks the LLM for a neutral description from that text only, and re-runs the **same** signal-extraction prompt on it;
4. scans the new description with the audit's own patterns (strong result phrasing, named judge), retries once with a
   stricter instruction, and **excludes** the case if it is still flagged;
5. leaves everything else unchanged (category, `expected_outcome`, `claimed_amount_rupees`, ...).

It never modifies an existing file. Outputs go to `data_cache/`: `eval_leakfree_control.json`,
`eval_leakfree_original.json` (the original versions of the same cases), `leakfree_control_progress.json` (lets a
re-run resume) and `leakfree_control_report.json`.

## Steps (PowerShell, from `D:\Coding\Projects\DigiNyaya\backend`)
Needs `SARVAM_API_KEY`. **Start with a small run** to check the cuts look sane before spending on 400.

```powershell
# 1. build the control inputs (about 2 LLM calls per case). Smoke test first:
python -m scripts.build_leakage_free_control --n 40
#    then the real run (it resumes, skipping finished cases):
python -m scripts.build_leakage_free_control --n 400

# 2. score the control cases with the same pipeline + judge (scripted pipeline is free; one judge call per case).
#    --out keeps your existing verdict files untouched.
$env:DIGINYAYA_EVAL_DATASET = "data_cache\eval_leakfree_control.json"
python -m scripts.judge_real_outcomes --out data_cache\leakfree_control_verdicts.json
Remove-Item Env:DIGINYAYA_EVAL_DATASET

# 3. compare original vs control on exactly the same cases
python -m scripts.leakfree_control_report
```

Before step 2, eyeball a few controls: open `data_cache\eval_leakfree_control.json` and read 10 `case_description`
values next to their `control.original_description`. They should describe facts and claims, not a ruling, and still
contain enough to decide the case. Check `leakfree_control_report.json` too: a high `leak_excluded` or `too_short`
count means the cut is too aggressive or the texts are too short for this method.

## Reading the result
`leakfree_control_report` prints, for the cases judged in both runs: winner accuracy, macro-F1, full-match rate and the
amount metric, original vs control, each with a paired bootstrap CI and an exact McNemar test, plus the text-only
baseline's lift on both sets of descriptions.

| What you see | What it means |
| --- | --- |
| Control **clearly lower** (CI entirely below 0) | The headline was inflated by inputs that saw the decision. Report the control numbers as the honest ones. |
| **No detectable difference** | No inflation shows up at this sample size. The report states how large an inflation it could have missed. |
| Control **higher** | Unexpected. A cleaner narrative can make a case easier; inspect the controls before believing it. |
| Text-only lift **shrinks** on control descriptions | Part of that baseline's edge came from wording shaped by the decision. |

## Limits (say these in any write-up)
- It is a **sample** (`--n`), stratified by category x real outcome, not the whole corpus.
- The control inputs are LLM-written too, and the cut is heuristic (markers plus a fraction cap). A pre-decision cut can
  also remove facts the case needs, which would make the control look worse for a reason unrelated to leakage.
- The verdicts still come from an LLM judge that has **not yet been validated** against human labels.
- It tests leakage in the **inputs**. It says nothing about whether the labels (who prevailed) are right.
