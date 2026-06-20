# DigiNyaya — AI-Native Civil Dispute Resolution

> Justice in 30 minutes, not 7 years.

DigiNyaya reimagines the civil court process from scratch. Instead of filing a case,
hiring a lawyer and waiting years, a citizen logs in, submits a dispute, and **five
coordinated AI agents** parse the claim, research precedent, analyse both sides,
mediate, and issue a binding resolution — end to end.

This repository is the **Phase 1 Hackathon MVP**: the *Consumer Dispute* journey working
end-to-end with all five agents, demoable in under 5 minutes.

---

## What's inside

```
TESTING/
├── backend/                 FastAPI + multi-agent orchestrator (Python)
│   └── app/
│       ├── main.py          REST + SSE endpoints (auth, jobs, event stream)
│       ├── jobs.py          Background job runner (work decoupled from HTTP)
│       ├── db.py            SQLite persistence: cases + append-only event log
│       ├── store.py         Back-compat shim over db.py
│       ├── core/
│       │   ├── context.py     Typed CaseContext "blackboard" + result models
│       │   ├── graph.py       Hand-rolled state machine (routing/loop/escalation)
│       │   └── events.py      Event model + in-memory pub/sub bus
│       ├── llm/             Local LLM client (stream / JSON-mode / embeddings)
│       ├── rag/             Semantic retrieval + keyword fallback + citation check
│       ├── security/        Token auth, case-ownership (IDOR), input sanitization
│       ├── agents/          The five agents (run(ctx) -> AgentResult) + NLP helpers
│       │   ├── base.py         Agent contract + prompt-injection fence
│       │   ├── ingestion.py    Agent 1 — parse, classify, confidence -> routing
│       │   ├── research.py     Agent 2 — semantic/keyword precedent retrieval
│       │   ├── analysis.py     Agent 3 — neutral summary + strength + loop trigger
│       │   ├── mediation.py    Agent 4 — structured LLM proposal + quantum clamp
│       │   └── resolution.py   Agent 5 — streamed findings + deterministic order
│       └── data/
│           ├── precedents.json  15 Indian consumer-court precedents (seed corpus)
│           └── loader.py        Corpus + dispute-type metadata
│   └── scripts/
│       ├── eval_cases.py    Golden-case evaluation harness
│       └── smoke_http.py    End-to-end HTTP + SSE smoke test
└── frontend/                React (Vite) — the live demo UI
    └── src/
        ├── pages/           Landing, Disputes, NewCase, Respondent, Resolve
        ├── components/      Stepper, ResolutionDoc
        └── api.js           REST client + auth + SSE streaming helper
```

### The multi-agent architecture

| Agent | Role |
|-------|------|
| **Orchestrator** | A hand-rolled **state machine**, not a linear script. It routes on Agent 1's confidence (Tier 1 autonomous vs. Tier 2 escalation), loops back to Research when coverage is thin, pauses for the mediation decision, then resumes. The routing decision actually changes the final order. |
| **Agent 1 · Ingestion** | Parses the claim + evidence into structured facts and emits a **confidence** + recommended tier that steers routing. |
| **Agent 2 · Precedent Research** | **Semantic retrieval** over the corpus via embeddings (cosine), with transparent keyword fallback; reports coverage and method. |
| **Agent 3 · Argument Analysis** | Neutral LLM summary + a **strength score** that feeds the mediation quantum; flags thin coverage to trigger the research loop. |
| **Agent 4 · Mediation** | The LLM returns a **structured proposal** (outcome, relief ratio, window); a deterministic validator **clamps every number** to a precedent-derived band. |
| **Agent 5 · Resolution Drafting** | **Streams** the reasoned findings token-by-token; the operative order, amounts and deadline are deterministic, and citations are verified against what was actually retrieved. |

Each agent reads and writes a single **typed `CaseContext` blackboard**. Work runs in a
**background job** (so a refresh never kills or duplicates a run) and every event is written
to an **append-only SQLite log** *and* an in-memory bus. The UI subscribes to
`GET /events?after=<cursor>`, which **replays from the durable log on refresh** and then
streams live — so the demo resumes correctly even if you reload mid-resolution.

### Security & robustness

- **Auth + ownership:** every case is owned by its filer; protected endpoints verify a bearer
  token's `citizen_id` against the case owner (fixes IDOR — strangers get a generic 404).
- **Prompt-injection defense:** untrusted party text is fenced and the model is instructed to
  treat it as data, never instructions.
- **No hallucinated money or citations:** amounts/deadlines are deterministic; the relief
  quantum is clamped to the precedent band; citations are filtered to retrieved precedents.
- **Persistence + audit trail:** cases and an append-only event log live in SQLite.
- **Eval harness:** `python -m scripts.eval_cases` runs golden cases and asserts invariants.

### AI engine — real local LLM, free, no API key

DigiNyaya runs a **real open-source LLM locally via [Ollama](https://ollama.com)** — no API
key, no cost, no internet required. The reasoning agents (3, 4, 5) use the model to write
their natural-language output, while **all numbers, amounts, deadlines and precedent matches
stay deterministic** so the model can never hallucinate the money or the order.

If Ollama isn't running, every agent **automatically falls back to scripted logic** — so the
demo never breaks. A live badge in the UI shows which engine is active.

**Setup (one time):**

```bash
# install Ollama from https://ollama.com, then pull a model:
ollama pull qwen2.5:7b-instruct     # best quality (default)
# or, for ~3x faster responses on a CPU-only laptop:
ollama pull qwen2.5:1.5b
# (optional) enable real semantic precedent search:
ollama pull nomic-embed-text
```

> Without the embedding model the Research agent automatically uses keyword retrieval — the
> rest of the pipeline is unaffected.

**Config (optional env vars, set before starting the backend):**

| Variable | Default | Purpose |
|----------|---------|---------|
| `DIGINYAYA_USE_LLM` | `1` | Set to `0` to force scripted mode |
| `DIGINYAYA_LLM_MODEL` | `qwen2.5:7b-instruct` | Any Ollama model tag |
| `DIGINYAYA_OLLAMA_URL` | `http://localhost:11434` | Ollama server URL |

> **Speed note:** on a CPU-only machine the 7B model takes ~10–15s per reasoning step
> (~40–70s total pipeline). For a snappier live demo, switch to the 1.5B model:
> `set DIGINYAYA_LLM_MODEL=qwen2.5:1.5b` (Windows) before launching uvicorn. The backend
> pre-warms the model on startup so the first demo call is fast.

---

## Running it locally

You need **Python 3.10+** and **Node 18+**. Open two terminals.

### 1. Backend (port 8077)

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux
pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8077
```

### 2. Frontend (port 5173)

```bash
cd frontend
npm install
npm run dev
```

Open **http://localhost:5173**. The Vite dev server proxies `/api` to the backend, so
there's nothing else to configure.

---

## Live demo script (~4 minutes)

1. **Log in** — the landing page is pre-filled (Ananya Sharma, Aadhaar `4821`). Click *Verify & continue*.
2. **Pick** *Consumer Dispute*.
3. On the claim form, click **"Load demo case"** — a ₹42,999 undelivered-laptop dispute with 3 evidence items auto-fills. Click *File claim & notify respondent*.
4. On the respondent screen, either click **"Load demo reply"** (seller offers store credit / ₹20,000) and submit, **or** click **"Skip — proceed uncontested"**.
5. **Watch the theatre**: the orchestrator boots and shows its **routing decision**, then Agents 1→4 run live, each surfacing real output (extracted facts + confidence, retrieved precedents + method, strength + contradictions). If coverage is thin you'll see it **loop back** to Research.
6. **Mediation proposal** appears with a validator-clamped quantum. Click **Accept** (settles) or **Decline** (Agent 5 issues an autonomous order).
7. **Binding resolution document** renders, with the findings **streamed token-by-token**, cited (verified) precedents and a compliance deadline. Download it.
8. **Refresh the page mid-flow** — the stream replays from the durable log and resumes exactly where it was. (Bonus: low-confidence/empty claims **escalate to Tier 2** with a human-sign-off order.)

> Then zoom out: *this is one dispute resolved in 4 minutes — there are 50 million waiting.*

---

## API reference (selected)

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/api/login` | Simulated Aadhaar login |
| `GET`  | `/api/dispute-types` | Available categories + tier metadata |
| `GET`  | `/api/sample-claim` | Pre-built demo claim + respondent reply |
| `POST` | `/api/cases` | File a new claim (auth required; sets owner) |
| `POST` | `/api/cases/{id}/respond` | Respondent files a reply |
| `POST` | `/api/cases/{id}/skip-response` | Proceed uncontested |
| `POST` | `/api/cases/{id}/run` | Start the agent pipeline as a background job (idempotent) |
| `POST` | `/api/cases/{id}/mediation` | Accept / decline mediation; starts resolution job |
| `GET`  | `/api/cases/{id}/events?after=<seq>` | **SSE** — replay from cursor + live stream (incl. tokens) |
| `GET`  | `/api/cases/{id}` | Full case view |
| `GET`  | `/api/ai-status` | Which engine is active (local LLM vs scripted) |

> All `/api/cases/...` endpoints require an `Authorization: Bearer <token>` header (the token
> is returned by `/api/login`) and enforce case ownership.

---

## Roadmap (beyond this MVP)

- **Phase 2** — All four Tier 1 case types (money recovery, contract breach, cheque bounce), real Aadhaar integration, mobile-first UI.
- **Phase 3** — Tier 2 AI-assisted virtual judge, High Court partnerships, vernacular languages.
- **Phase 4** — Tier 3 complex civil, criminal with mandatory human sign-off, government ODR integration.

The agents already use a real local LLM **and real semantic RAG** (embeddings + cosine, see
*AI engine* above). Scaling to a larger corpus is a matter of swapping the in-process vector
store in `backend/app/rag/index.py` for a dedicated vector DB (e.g. Qdrant/pgvector) — the
orchestrator, agent contract and API stay identical.

---

*Demonstration prototype. Generated resolutions are illustrative and do not constitute a
court order or legal advice.*
