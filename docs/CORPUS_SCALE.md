# Scaling the eval corpus (60K goal)

Three scripts, run in this order. Nothing here touches `eval_judgments.json`; new cases go to separate slice files and are
checked before they are merged.

| Step | Command (from `backend/`) | What it answers |
|---|---|---|
| 1. Supply | `python -m scripts.district_supply_census` | How many civil cases can really be downloaded, by state and year |
| 1b. Free check | `python -m scripts.pilot_extractability` | Upper bound on the yield with no LLM: how many candidates yield usable text |
| 2. Pilot | `python -m scripts.plan_corpus_scale --pilot 150` then run the printed commands | What share of attempted cases survives the LLM filter (the **yield**) |
| 3. Plan | `python -m scripts.plan_corpus_scale --target 60000 --already-have <N> --assumed-yield <measured>` | Per state-year quotas, one command each, archive GB, calls |
| 4. Source | run `data_cache\corpus_plan.commands.txt` (slices are disjoint; run several in parallel) | |
| 5. Gate | `python -m scripts.corpus_gate --batch-dir data_cache\dc_slices --write-clean data_cache\dc_clean.json` | Which cases are fit to use |

## What the census found (measured on the live bucket, Oct 2026)

* 401,843 civil-coded, decided cases with a PDF flag in the metadata, but only **~230,000 in archives that exist**.
  Delhi, Haryana, Punjab, Kerala and Goa (~171k) have metadata and no archives.
* Downloadable supply is **Andhra Pradesh 73%, Telangana 26%**, Bihar 1% (a single court).
* A 150-PDF sample of AP/Telangana archives: all English (0 of 132 readable files had Telugu-dominant text), but **12% have no
  usable text layer** (scanned: they need OCR) and Telangana orders are short (median ~1,500 characters, one page). The
  sample is of archive contents, not of the candidate cases, so treat it as a smoke test.
* The census checks that an archive exists, not that every case's PDF is in it.

## LLM-free extractability check (measured Oct 2026, no LLM)

360 candidates from 18 of the largest small-archive courts (AP 2022, AP 2023, Telangana 2022): **87% yielded usable text**
(AP 2022 75%, AP 2023 88%, Telangana 2022 98%). The failures were scanned PDFs with no text layer (no OCR in that run). Of the
usable text, median 5,638 characters; 23% under 1,500 characters; 91% mention a decree/dismissal/disposal; 2% contain
criminal-procedure terms. This is an upper bound on the yield, not the yield, and the courts were not randomly chosen.

## The sourcing script's old default was the ceiling

`--max-per-complex` defaults to 25, a guard for small targets. With ~125 courts a year it caps the whole corpus near
18k at a 20% yield. The planner defaults to 150 and the gate separately fails any court above 2% of a batch.

## What decides whether 60K is reachable

Total target 60,000, already held 23,841, so 36,159 new. Reach by assumed yield (per-court cap 150, state share <= 65%, year share <= 25% of a state):

| Yield | New cases plannable |
|---|---|
| 10% | 17,856 |
| 15% | 25,838 |
| 20% | 33,266 (about 166k extraction attempts, 72% of all downloadable candidates) |
| 30% | 36,143 (target met) |

The yield is **unmeasured**. Run the pilot. The plan does not subtract the cases you already hold from the supply, so the true
ceiling is lower by however many of your 23,841 came from this pool.

## What the pilot taught us (AP 2023, one court, measured)

`added=50 rejected=128 extraction_failed=3` = **27.6% yield** (50 of 181) from a single court complex. Nearly all rejections were
`is_genuine=True, is_final=False`. On a text sample of 106 AP 2023 cases the endings were: ex parte decrees 43%, compromise 19%,
withdrawn 10%, dismissed for default 4%, contested decree 6%, dismissed 8%, no operative wording 10%. So (a) about a third end
without any decision on the merits, which the sourcing script now skips before the LLM (`--no-prescreen` turns that off), and
(b) **ex parte decrees dominate the usable cases**: the defendant never appeared, so the claimant wins almost by default. Each kept
case is tagged `source.disposal` and the gate warns when ex parte exceeds 40%. Report results for contested cases separately.

## Stopping and resuming a sourcing run

Ctrl+C is safe. Each accepted case is written to the slice file the moment it is accepted, and the court archives and
extracted text are cached. A sidecar file next to the slice (`<slice>.tally.json`) records every attempt, so the yield can be
read off an interrupted run (`tally (all runs of this file): ... yield=NN%` is printed every 10 accepted cases and at the end).
Cases already rejected or unreadable are skipped on resume, so you are not charged for them again. Rerun the same command to
resume. The tally only exists for runs started with this version; a slice started earlier has accepted cases saved but no counts.

## What the gate does

Rejects: malformed, duplicate id or docid (against the existing corpus and within the batch), near-duplicate description
(MinHash estimate, Jaccard >= 0.8), too short, Indic script >= 30%, personal identifiers (phone, Aadhaar-style number, e-mail,
PAN), a description that states the decision or names the judge, a category the eval cannot map, overlap with the precedent corpus.

Flags (kept): honorific + name left in, some Indic script, weak outcome phrasing, amount echo, very long text.

Batch checks (PASS/WARN/FAIL): rejection rate, category depth, largest state share, largest court share, largest year share,
weak-phrasing rate, amount-echo rate, human-verified count. **The state-share check will warn on this data.** That is the
finding to report alongside any result, not something to hide.

## What the gate does not do

* It cannot tell whether the extracted `expected_outcome` is correct. That needs the human labels.
* It does not remove the narrative leakage the leakage-free control measures; run that on a sample of the new cases too.
* Redaction relies on the extraction prompt (no party-name list exists for this source): read the `honorific_name_left` flags.
* After the gate: run `scripts.audit_outcome_leakage` and `scripts.split_eval_for_precedents` on the merged file before scoring.
