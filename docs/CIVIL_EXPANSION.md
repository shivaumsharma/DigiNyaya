# Civil expansion: tenancy, property and employment

## Where this stands
**Phase 1 is built: new identities, identical behaviour.** `tenancy_dispute`, `property_dispute` and
`employment_dispute` exist as native dispute types with their own labels, evidence guidance, sample claims,
safety-gate registration and statutes. They **behave exactly like the type the real-judgment eval already maps them
to** (tenancy and employment like `money_recovery`, property like `contract_breach`) because the mediation
thresholds and the full-claim amount override were tuned on real data through those mapped types. A native type that
drifted from its alias would silently change results and break comparability with the published baseline.
`tests/test_civil_expansion.py` proves the equivalence on 18 scenario runs per type (54 in all) and was mutation-checked (breaking
a type's alias makes it fail).

**They are not open to users.** The types are listed as "roadmap" (inactive); filing one returns 422 server-side.
Set `DIGINYAYA_ENABLE_PREVIEW_TYPES=1` to open them (e.g. in a staging environment).

## What phase 1 does NOT do (be honest about this before anyone relies on it)
- **No native precedents.** There are no real tenancy, property or employment precedents in the corpus, and none
  were invented. Retrieval borrows `money_recovery` / `contract_breach` precedents, as the eval always has, so
  relevance is weak. A type starts using its own precedents automatically once the corpus holds at least
  `MIN_NATIVE_PRECEDENTS` (15) real ones.
- **No native signals or tuned thresholds.** Tenancy-specific wording (eviction notice, security deposit, arrears)
  is not yet detected as such; nothing about how cases are decided has changed.
- **The scope rules still apply.** Cases that ask for an injunction or stay (common in property disputes) still trip
  the existing out-of-scope keywords and escalate to a human. That is deliberate for now.
- **Employment is a thin slice.** Many employment disputes belong before labour authorities, not civil forums. Wage
  and gratuity statutes are deliberately NOT cited because I could not confirm which the Labour Codes now
  supersede. A lawyer must decide what the product may handle here.
- **Statute entries are unreviewed paraphrases** (see `docs/STATUTE_GROUNDING.md`) and only matter when
  `DIGINYAYA_STATUTE_GROUNDING=1`.
- The frontend needs no change: it renders types from `/api/dispute-types`. Labels are English-only until the UI
  strings are translated.

## Check it on real data
```
DIGINYAYA_EVAL_NATIVE_TYPES=1 python -m scripts.run_real_judgment_eval ...    # scores the 3 categories as native types
```
then compare against your existing run with `scripts/compare_eval_runs.py`. Every decision should be unchanged;
anything that differs is a bug in phase 1, not a finding.

## Phase 2: make them genuinely better (each step needs its own measured before/after)
1. **Split first.** `python -m scripts.split_eval_for_precedents --input data_cache/eval_judgments_with_signals.json`
   fixes a held-out evaluation set and a precedent-source set before any precedent is built, so the pipeline can
   never retrieve the judgment it is scored on. The split is a per-case hash, so it stays stable as the corpus
   grows. Do not `--force` it after precedents exist.
2. **Build native precedents from the precedent-source set only**, from real judgments (structured as the other
   precedents are: summary, principle, outcome, relief ratio, citation). This needs the eval record format and an
   LLM extraction step; send one redacted record and the script can be written against it. Have a lawyer spot-check a
   sample.
3. **Native signals and thresholds**, only where real data justifies them (tenancy possession is the one place
   the data already does).
4. **Evaluate on the held-out set** with the leakage audit (`scripts/audit_outcome_leakage.py`) and the narrative
   sensitivity test (`scripts/narrative_sensitivity.py`) run on it first.
