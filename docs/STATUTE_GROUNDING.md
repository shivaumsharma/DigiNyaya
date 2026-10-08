# Statute grounding (optional, off by default)

**What it does.** When `DIGINYAYA_STATUTE_GROUNDING=1`, the Research agent also retrieves up to three statutory
provisions relevant to the dispute type (Consumer Protection Act 2019, Negotiable Instruments Act s.138-143, Indian
Contract Act, Limitation Act, Interest Act, CPC s.34, Sale of Goods Act s.16). The resolution's findings gain one
deterministic sentence naming them, and `ResolutionDoc.cited_statutes` / `ResearchResult.statutes` carry them in the
API. With the flag off (the default) nothing changes: same findings, same cited precedents, same relief amount.

**Why a flag.** NyayaRAG (2025) found statutes helped judgment prediction and explanation more than similar past
cases. Whether that holds for DigiNyaya is an empirical question, so the feature is built to be run as an ablation:
same cases, flag off vs on, compare the real-judgment metrics.

**Guardrails.**
- Retrieval is keyword overlap within the dispute type: deterministic, no embeddings, cannot invent a provision.
- If the model's findings mention a "Section N" that was not retrieved, that sentence is dropped; if nothing is left,
  the deterministic scripted findings are used instead.
- Grounding never touches the relief amount, deadline, or tier routing.

**Read before relying on it.**
- `backend/app/data/statutes.json` was written from general knowledge as short neutral paraphrases, **not statutory
  text, and has not been reviewed by a lawyer.** Clause numbers (e.g. CPA 2019 s.2(10), 2(11), 2(47)) and the
  paraphrases need checking against the current Acts before this is shown to real users. Every entry should get a
  reviewer's initials and date before the flag is turned on in production.
- The frontend does not display `cited_statutes` yet; it is API-only for now.
- Coverage is the four registered dispute types only. Tenancy, employment and property provisions are not included.

**Run the ablation** (from `backend/`, with the real-judgment eval set in place):
```
DIGINYAYA_STATUTE_GROUNDING=0 python -m scripts.run_real_judgment_eval ...   # baseline
DIGINYAYA_STATUTE_GROUNDING=1 python -m scripts.run_real_judgment_eval ...   # with statutes
```
then compare with `scripts/compare_eval_runs.py`.
