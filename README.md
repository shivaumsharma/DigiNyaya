# DigiNyaya — AI-Native Civil Dispute Resolution

> Justice in minutes, not years.

DigiNyaya reimagines the civil court process from scratch. Instead of filing a case,
hiring a lawyer and waiting years, a citizen signs up, submits a dispute, and **five
coordinated AI agents** parse the claim, research precedent, analyse both sides,
mediate, and issue a resolution — end to end. A safety gate keeps genuinely weak,
out-of-scope, or contested cases out of AI hands entirely and routes them to a human
reviewer instead.

This is a working prototype, not a finished product — this README describes the
actual current state of the code, not aspirational plans.

---

## Features

- Five-agent AI pipeline (Ingestion → Research → Analysis → Mediation → Resolution),
  coordinated by a hand-rolled orchestration state machine, not a linear script
- Real-time, token-streamed resolution drafting over Server-Sent Events, durably
  replayed from an append-only log so a page refresh never loses or duplicates a run
- Two-checkpoint safety gate that routes weak, out-of-scope, or contested cases to a
  human reviewer instead of letting the AI decide
- Deterministic relief-amount clamping against a 120+ real-precedent corpus — no
  hallucinated money, deadlines, or citations
- Multi-provider LLM layer: **Sarvam AI** in production, with automatic fallback to a
  local **Ollama** model or scripted logic if neither is reachable
- OCR and audio transcription of evidence via **Sarvam Document AI** and
  **Speech-to-Text** (with speaker diarization), Tesseract as a local OCR fallback
- Text-to-speech narration of mediation proposals and resolutions via **Sarvam
  Bulbul**, read aloud in the citizen's own filing language
- Real-time UI and content translation across 11 Indian languages via **Sarvam
  Mayura**
- A real account system: email+password and phone+OTP signup/login, JWT access
  tokens with rotating refresh tokens
- Role-gated human review workflow with a reviewer queue, case detail view, and
  decision audit trail
- Tamper-evident, SHA-256 hash-chained event log for every case, with a live
  reviewer-facing verification endpoint
- Circuit breakers around every direct Sarvam call site, so one product outage
  degrades gracefully instead of cascading
- DPDP Act 2023-aligned self-service data export
- A golden-case evaluation harness scored against real court judgments

---

## Screenshots

_Screenshots to be added — placeholders below, filenames expected under `screenshots/`._

| | |
| --- | --- |
| **Home** — dispute types, tiers, entry point | `screenshots/home.png` |
| **New case filing** — draft → upload → review → submit | `screenshots/new-case.png` |
| **Live multi-agent pipeline** — streamed agent-by-agent progress | `screenshots/pipeline.png` |
| **Mediation proposal** — structured LLM proposal + Listen narration | `screenshots/mediation.png` |
| **Resolution document** — final order, findings, citations | `screenshots/resolution.png` |
| **Reviewer queue & ops dashboard** — case volume, tier split, eval metrics | `screenshots/reviewer-dashboard.png` |

---

## Tech Stack

| Category | Technologies |
| --- | --- |
| Backend | Python, FastAPI, Uvicorn |
| Database / ORM | SQLite, SQLAlchemy, Alembic |
| AI / LLM | Sarvam AI (Sarvam-30B/105B chat, Document AI OCR, Speech-to-Text, Bulbul text-to-speech, Mayura translation), Ollama (local fallback + embeddings) |
| Retrieval | Custom semantic (cosine over embeddings) + keyword-fallback precedent search |
| Frontend | React, Vite |
| Auth | JWT access tokens, rotating refresh tokens, email + phone/OTP |
| Document processing | PyMuPDF (native-text PDFs), Tesseract OCR (fallback) |
| Testing | pytest, Vitest, React Testing Library |
| Deployment | Render (backend + frontend) |
| Version Control | Git, GitHub |

---

## Project Structure

```
DigiNyaya/
├── backend/                 FastAPI + multi-agent orchestrator (Python)
│   └── app/
│       ├── main.py          REST + SSE endpoints (case lifecycle, jobs, event stream)
│       ├── jobs.py          Background job runner (work decoupled from HTTP)
│       ├── db.py            SQLite persistence: cases + documents + append-only event log
│       ├── auth/            Real account system: signup/login (email+password, phone+OTP),
│       │                    JWT access + rotating refresh tokens, is_reviewer role
│       ├── core/
│       │   ├── context.py     Typed CaseContext "blackboard" + result models
│       │   ├── graph.py       Hand-rolled state machine (routing/loop/escalation)
│       │   ├── safety_gate.py Two-checkpoint escalation gate -- see below
│       │   ├── circuit_breaker.py  Shared breaker around every direct Sarvam call site
│       │   ├── versioning.py  /api/v1/... rewrite middleware
│       │   └── events.py      Event model + in-memory pub/sub bus
│       ├── llm/             Provider-agnostic LLM client (Sarvam live, Ollama local, scripted fallback)
│       ├── rag/             Semantic retrieval + keyword fallback + citation verification
│       ├── security/        Case-ownership (IDOR) checks, input sanitization
│       ├── documents/       PDF/image/audio evidence extraction (PyMuPDF; OCR via Sarvam Document AI, Tesseract fallback; audio via Sarvam Speech-to-Text)
│       ├── language/        Translation gateway (Sarvam Mayura) + text-to-speech narration (Sarvam Bulbul)
│       ├── routers/
│       │   ├── documents.py   Evidence upload, discrepancy check, pre-filing preliminary review
│       │   └── reviews.py     Human-review queue/detail/decision, audit-verify, ops/eval metrics
│       ├── agents/          The five agents (run(ctx) -> AgentResult) + NLP helpers
│       │   ├── base.py             Agent contract + prompt-injection fence
│       │   ├── ingestion.py         Agent 1 — parse, classify, confidence -> tier routing
│       │   ├── research.py          Agent 2 — semantic/keyword precedent retrieval
│       │   ├── analysis.py          Agent 3 — neutral summary + strength score + loop trigger
│       │   ├── mediation.py         Agent 4 — structured LLM proposal + deterministic quantum clamp
│       │   ├── resolution.py        Agent 5 — streamed findings + deterministic order
│       │   ├── discrepancy.py       Cross-document consistency check (dates/amounts/names/signatures)
│       │   └── preliminary_review.py  Pre-filing advisory check (NOT part of the 5-agent pipeline) --
│       │                             document relevance + authenticity heuristic, description-quality
│       │                             check, 0-100 winnability score
│       └── data/
│           ├── precedents.json  120+ real Indian consumer-court precedents
│           └── loader.py        Corpus + dispute-type metadata
│   └── scripts/
│       ├── ingest_judgments.py       Pulls real judgments from the Indian Kanoon API (billed per call)
│       ├── eval_cases.py             Golden-case evaluation harness (scripted, free, runs in CI)
│       ├── measure_eval_cost.py      Token-cost regression gate against real Sarvam calls (opt-in)
│       ├── judge_real_outcomes.py    Scores AI output against real court judgments
│       ├── source_free_judgments.py  Sources more real judgments for $0 via HuggingFace (no API billing)
│       ├── train_outcome_classifier.py  Predicts match-vs-not from case features (leave-one-out CV)
│       ├── error_analysis.py         Confusion-matrix-style breakdown of the real-judgment eval by category
│       ├── promote_reviewer.py       Grants/revokes is_reviewer on an existing account (CLI only)
│       ├── translate_ui_strings.py   Regenerates one locale file from en.json via Sarvam
│       ├── fill_missing_translations.py  Incrementally fills only the KEYS a locale file is missing
│       ├── load_test.py              Concurrency/latency load testing + real-pipeline usage generation
│       └── smoke_http.py             End-to-end HTTP + SSE smoke test
└── frontend/                React (Vite) — the live demo UI
    └── src/
        ├── pages/           Home, Disputes, NewCase, Respondent, Resolve, ReviewerQueue, ReviewerCaseDetail
        ├── components/      Stepper, ResolutionDoc, EvidenceDropzone, CaseStrengthPanel, ListenButton
        ├── auth/            Signup/login screens, protected-route guards
        ├── i18n/             English + 10 Indic-language dictionaries
        └── api.js           REST client + auth + SSE streaming helper
```

---

## Key Engineering Highlights

- **Hand-rolled orchestration state machine**, not a linear script — routes on
  Agent 1's confidence, loops back to Research when precedent coverage is thin,
  pauses for the mediation decision, and resumes correctly even across a page reload
- **Deterministic guardrails around every LLM output**: relief amounts are clamped to
  a precedent-derived band, dismissal is forced whenever the respondent's case is at
  least as strong as the claimant's regardless of what the model proposed, and
  citations are filtered to what was actually retrieved
- **SHA-256 hash-chained, tamper-evident event log** — altering, removing, or
  reordering a past event breaks the chain from that point forward, verifiable live
  via `GET /api/reviews/{id}/audit-verify`
- **Circuit breakers on every direct Sarvam call site** (chat, Document AI, Speech-to-
  Text, Bulbul TTS, Mayura translation) so one product outage degrades to that
  caller's existing fallback instead of cascading or hanging
- **Streamed, durable resolution drafting** — the reasoned findings stream token-by-
  token over SSE, backed by an append-only log that replays from a cursor on
  reconnect, so a mid-resolution refresh never loses progress
- **Rate-limited, cost-aware AI usage** on every LLM/Sarvam-cost-bearing endpoint,
  independently tunable per feature
- **Provider-agnostic LLM client** — swap between Sarvam, a local Ollama model, or
  fully scripted fallback logic with a single environment variable, with automatic
  cascading fallback if the configured provider is unreachable
- **Zero-duplication API versioning** — every `/api/...` route is also reachable at
  `/api/v1/...` via a single ASGI rewrite middleware (`app/core/versioning.py`)
  rather than declaring each route twice

---

## How it works

### The multi-agent architecture

| Agent | Role |
| --- | --- |
| **Orchestrator** | A hand-rolled **state machine**, not a linear script. Routes on Agent 1's confidence (Tier 1 autonomous vs. Tier 2 human-countersigned), loops back to Research when coverage is thin, pauses for the mediation decision, then resumes. |
| **Agent 1 · Ingestion** | Parses the claim + evidence into structured facts and emits a **confidence** + recommended tier that steers routing. |
| **Agent 2 · Precedent Research** | **Semantic retrieval** over the corpus via embeddings (cosine), with transparent keyword fallback; reports coverage and method. |
| **Agent 3 · Argument Analysis** | Neutral LLM summary + a **strength score** that feeds the mediation quantum; flags thin coverage to trigger the research loop. |
| **Agent 4 · Mediation** | The LLM returns a **structured proposal** (outcome, relief ratio, window); a deterministic validator **clamps every number** to a precedent-derived band, and separately **forces dismissal** whenever the respondent's case is at least as strong as the claimant's — regardless of what the LLM itself proposed. |
| **Agent 5 · Resolution Drafting** | **Streams** the reasoned findings token-by-token; the operative order, amounts and deadline are deterministic, and citations are verified against what was actually retrieved. |

Each agent reads and writes a single **typed `CaseContext` blackboard**. Work runs in a
**background job** (so a refresh never kills or duplicates a run) and every event is written
to an **append-only SQLite log** _and_ an in-memory bus. The UI subscribes to
`GET /api/cases/{id}/events?after=<cursor>`, which **replays from the durable log on refresh**
and then streams live — so the demo resumes correctly even if you reload mid-resolution.

### Tiers and dispute types

- **Tier 1 — fully autonomous**: `consumer_dispute` only.
- **Tier 2 — AI-drafted, requires human counter-signature**: `money_recovery`, `contract_breach`, `cheque_bounce`.
- Anything else, or anything matching a criminal/out-of-scope keyword, is rejected by the safety gate before any agent runs.

### Safety gate (`app/core/safety_gate.py`)

Two checkpoints, five conditions — a trigger at either one discards the AI's output entirely
and marks the case `escalated`, with the specific triggered reason(s) surfaced to the user:

- **Checkpoint A** (pre-filter, before any agent runs): criminal-matter keywords, unregistered/out-of-scope dispute type.
- **Checkpoint B** (post-check, runs *before* the slow resolution draft, not after): composite confidence below a hard floor, fewer than 2 precedents retrieved, Research/Analysis disagreeing on outcome direction.

### Case lifecycle

`draft` (claimant can upload evidence and get a non-binding AI **preliminary review** before
filing for real) → `awaiting_response` (respondent notified, 72-hour window) →
`ready`/`resolved`/`escalated`. A **manual "request human review"** action is available on any
case at any point, regardless of tier or AI confidence — Tier 1 cases otherwise never get
human eyes on them at all.

Leaving `draft` requires an explicit accuracy confirmation (`POST .../submit` with
`{"confirmed_accurate": true}`) — mirrors a real court e-filing "verification," enforced
server-side rather than just a disabled frontend button, and recorded on the case (plus an
audit-log event) with a timestamp.

### Human review workflow

`is_reviewer` is a narrow, deliberately non-self-service capability on the user account model
(no general admin role exists) — granted via `scripts/promote_reviewer.py`. Reviewers see a
queue (`GET /api/reviews/queue`) covering three distinct paths into review: safety-gate
escalation, a manual review request, or a Tier 2 resolution awaiting counter-signature. Frontend:
`/reviewer` (queue) and `/reviewer/:id` (detail + decision), nav-gated on the logged-in user's
`is_reviewer` flag.

### Pre-filing evidence review (advisory, not part of the 5-agent pipeline)

Before filing, a claimant can drag-and-drop evidence and get an instant, non-binding,
re-runnable AI read: whether each document actually supports the claim (vs. e.g. an unrelated
resume), a text-only authenticity heuristic (flags a *specific, nameable* internal
inconsistency like an invalid date or placeholder content — explicitly not forgery/image-tamper
detection), whether the claim description itself is detailed enough to have a real chance, and
a 0–100 winnability score with plain-language reasons. The respondent sees the same evidence
review on their reply page — but deliberately **not** the winnability score itself, since
showing "the AI already thinks this is weak" let a respondent coast on a low-effort reply
instead of engaging with the evidence.

### Security & robustness

- **Auth + ownership**: every case is owned by its filer's real account (`app/auth/`); every
  protected endpoint verifies the bearer token's `user.id` against the case owner.
- **Prompt-injection defense**: untrusted party text is fenced and the model is instructed to
  treat it as data, never instructions.
- **No hallucinated money or citations**: amounts/deadlines are deterministic; the relief
  quantum is clamped to the precedent band and further hard-gated on relative case strength;
  citations are filtered to retrieved precedents.
- **Persistence + audit trail**: cases and an append-only event log live in SQLite (see
  **Known issues** below for the production caveat on this). The event log is also
  **tamper-evident**, not just append-only by convention: each event is chained to the previous
  one via a SHA-256 hash (`app/db.py`'s `append_event`/`verify_case_events`), so altering,
  removing, or reordering a past event breaks the chain from that point forward in a way that's
  cheap to detect — a reviewer can check `GET /api/reviews/{id}/audit-verify` before relying on a
  case's history for a decision.
- **Eval harness**: `python -m scripts.eval_cases` runs golden cases and asserts invariants, free
  and scripted; `python -m scripts.measure_eval_cost` (opt-in, billed) measures real token usage
  against Sarvam.

---

## AI engine

**Sarvam AI** (`sarvam-105b-conversations` for fast classification/retrieval calls,
`sarvam-105b` for the heavier analysis/mediation/drafting reasoning) is the real, live provider
used in production. `sarvam-30b`, the original fast-tier model, was deprecated by Sarvam and is
now hard-rejected by their API — see `backend/app/llm/config.py`. **Ollama** (local, free, no API
key) is fully supported as a
dev/offline alternative — set `DIGINYAYA_LLM_PROVIDER=ollama` and pull a model:

```bash
ollama pull qwen2.5:7b-instruct     # best quality
ollama pull qwen2.5:1.5b            # ~3x faster on a CPU-only laptop
ollama pull nomic-embed-text        # enables real semantic precedent search either way --
                                     # Sarvam has no public embeddings endpoint, so embeddings
                                     # always come from Ollama regardless of the chat provider
```

If neither Sarvam nor Ollama is reachable, every agent **automatically falls back to scripted
logic** — the app never hard-crashes for lack of an LLM. A live badge in the UI shows which
engine is active.

**Config** (`backend/.env`, see `.env.example`):

| Variable | Default | Purpose |
| --- | --- | --- |
| `DIGINYAYA_LLM_PROVIDER` | `auto` | `sarvam` \| `ollama` \| `mock` \| `auto` (tries Sarvam, then Ollama, then scripted) |
| `SARVAM_API_KEY` | — | Required for the `sarvam` provider |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama server URL |
| `OLLAMA_CHAT_MODEL` | `qwen2.5:7b-instruct` | Any Ollama model tag |
| `DIGINYAYA_INDIANKANOON_TOKEN` | — | Only needed for `scripts/ingest_judgments.py` (billed per call); not required to run the app |

---

## Document upload, OCR & discrepancy detection

Citizens attach real PDF/JPEG/PNG/audio evidence (`POST /api/cases/{id}/documents`, multipart, up
to `DIGINYAYA_MAX_UPLOAD_MB` per file — default 15MB). Extraction, the pre-filing preliminary
review, and a cross-document discrepancy check (conflicting dates/amounts, inconsistent names,
missing signatures) all run as background jobs, decoupled from the HTTP request (`app/jobs.py`).

Native-text PDFs extract directly via PyMuPDF — no OCR engine needed. Scanned PDFs and
photographed documents OCR via **Sarvam Document AI** (cloud API, `doc_ai.digitise`) when
`SARVAM_API_KEY` is set — no local binary needed, and it natively covers all 22 Indian
languages. Tesseract is the fallback when no Sarvam key is configured, the document exceeds
Document AI's 10-page limit, or the API call fails:

```bash
# Windows: install the UB-Mannheim Tesseract build, then confirm it's on PATH:
tesseract --version

# macOS
brew install tesseract tesseract-lang

# Linux (Debian/Ubuntu)
sudo apt install tesseract-ocr tesseract-ocr-hin tesseract-ocr-ben
```

Without either engine configured, native-text PDF uploads still work fully; scanned
PDFs/images fail extraction gracefully (`extraction_status: failed`, never a crash).

**Audio evidence** (WAV/MP3/M4A/OGG-OPUS/WEBM — voice notes, recorded calls) is transcribed via
**Sarvam's Speech-to-Text Batch API** with speaker diarization, so a recorded conversation comes
back as `Speaker 0: ... / Speaker 1: ...` rather than one undifferentiated blob — the point being
evidence like "the vendor promised a refund on this call" is actually usable, not just a wall of
text. There is no local fallback for audio (unlike OCR's Tesseract fallback): if `SARVAM_API_KEY`
isn't set or the call fails, extraction reports `extraction_status: failed` rather than silently
producing empty text. See `app/documents/extraction.py`'s `_sarvam_transcribe()`.

> **Production note**: `backend/Dockerfile` correctly installs `tesseract-ocr`, but the live
> Render backend is currently deployed as a native Python buildpack, which never touches the
> Dockerfile, so Tesseract itself isn't available there. This no longer matters for OCR
> correctness: Sarvam Document AI is a cloud API call, not a local binary, so as long as
> `SARVAM_API_KEY` is set on Render (it already is, for the chat pipeline), scanned-image
> extraction works in production too. Tesseract only matters for the local-fallback path.

| Variable | Default | Purpose |
| --- | --- | --- |
| `SARVAM_API_KEY` | — | Also enables Sarvam Document AI OCR (primary engine); ₹0.5/page |
| `TESSERACT_LANG` | `eng` | Tesseract language pack(s) for the fallback engine, e.g. `eng+hin+ben` |
| `DIGINYAYA_STORAGE_PROVIDER` | `local` | Only `local` is implemented; `s3`/`gcs` raise `NotImplementedError` |
| `DIGINYAYA_STORAGE_ROOT` | `backend/uploads` | Local filesystem root for uploads (dev-only, not committed) |
| `DIGINYAYA_MAX_UPLOAD_MB` | `15` | Per-file upload size cap |

Evidence is also capped at **15 documents per case** (`app/routers/documents.py`'s `_MAX_EVIDENCE_PER_CASE`,
not currently an env var) — each document costs at least one LLM relevance-check call downstream
(pre-filing preliminary review, and again in Agent 1 when the real pipeline runs), so an unbounded
count is an unbounded cost/latency vector, not just a storage one.

**Resilience against a Sarvam outage**: every direct Sarvam call site (chat/JSON generation,
Document AI OCR, Speech-to-Text, Text-to-Speech, translation, language detection) is guarded by
its own `app/core/circuit_breaker.CircuitBreaker` instance. After 3 consecutive failures on one
endpoint, that breaker opens for 30s and calls fail fast (no network attempt) instead of paying
the full timeout/retry loop again — degrading to the same fallback behavior each caller already
has (Tesseract for OCR, untranslated passthrough text for translation, scripted agent behavior for
chat, a hidden play control for narration) just faster. Endpoints fail independently: a Document
AI outage doesn't open the Speech-to-Text or Text-to-Speech breakers. See `scripts/load_test.py`
(`light`/`pipeline` modes) for concurrency/latency load testing against a running backend.

---

## Audio narration (text-to-speech)

A claimant can listen to the mediation proposal and the final resolution order read aloud, in
whichever language the case is being viewed in, via **Sarvam's Bulbul model** (`app/language/tts.py`).
This is the counterpart to the Speech-to-Text audio-evidence support above — DigiNyaya's four other
Sarvam product lines (chat/JSON generation, Document AI, Speech-to-Text, translation) were already
in use; Bulbul was not, until this. The point isn't novelty for its own sake: a written legal
resolution is a real accessibility barrier for claimants with limited literacy, and reading it
back in their own filing language is a genuine access-to-justice improvement, not a gimmick.

- `GET /api/cases/{id}/mediation/audio` and `GET /api/cases/{id}/resolution/audio` — reuse the same
  ownership check and localization path as `GET /api/cases/{id}`, so what's narrated always matches
  what's on screen (an explicit `?lang=` override is honored the same way). Returns `audio/mpeg`
  bytes, or `404` if that stage hasn't happened yet, or `503` if Sarvam is unavailable.
- No cross-request audio chunk-stitching: text over Bulbul's practical single-clip limit
  (~1500 characters) is truncated at a sentence boundary rather than built out with chunking
  infrastructure for a case (a resolution long enough to need it) that essentially never happens
  in practice — resolution orders and mediation proposals are realistically a few sentences.
  Rate-limited the same way as every other LLM-cost-bearing endpoint (20 calls / 10 min / user).
- Frontend: a reusable `<ListenButton>` (`frontend/src/components/ListenButton.jsx`) lazily fetches
  audio on first click — nothing plays, and no credits are spent, until a citizen actually asks to
  listen — then toggles play/pause on the same clip.

---

## Installation

You need **Python 3.10+** and **Node 18+**. Open two terminals.

```bash
git clone https://github.com/shivaumsharma/DigiNyaya.git
cd DigiNyaya
```

### 1. Backend (port 8000)

```bash
cd backend
python -m venv venv
venv\Scripts\activate           # Windows
# source venv/bin/activate      # macOS / Linux
pip install -r requirements.txt
python -m alembic upgrade head
python -m uvicorn app.main:app --reload --port 8000
```

### 2. Frontend (port 5173)

```bash
cd frontend
npm install
npm run dev
```

Open **http://localhost:5173**. The Vite dev server proxies `/api`, `/auth`, and `/me` to
`http://127.0.0.1:8000` — nothing else to configure.

---

## Authentication

A real account system (`backend/app/auth/`) — email+password and phone+OTP signup/login, JWT
access tokens (15 min) + rotating opaque refresh tokens (7 days, httpOnly cookie). This **is**
the system that owns cases (`user.id` on every case) — there is no separate demo-login path.

### Endpoints

| Method | Path | Auth required | Purpose |
| --- | --- | --- | --- |
| `POST` | `/auth/signup/email` | — | Create an account with email + password |
| `POST` | `/auth/signup/phone/start` / `/verify` | — | Phone+OTP signup |
| `POST` | `/auth/login/email` | — | Log in with email + password |
| `POST` | `/auth/login/phone/start` / `/verify` | — | Phone+OTP login |
| `POST` | `/auth/link/phone/start` / `/verify` | yes | Add a phone number to the current account |
| `POST` | `/auth/refresh` | reads cookie | Rotate the refresh token, issue a new access token |
| `POST` | `/auth/logout` | yes | Revoke the refresh token server-side |
| `POST` | `/auth/password/reset/request` / `/confirm` | — | Enumeration-safe reset flow |
| `GET` | `/auth/verify-email?token=...` | — | Consume the verification token |
| `GET` | `/me` | yes | Current user's profile (includes `is_reviewer`) |

Reusing an already-rotated-out refresh token revokes every token descended from that login
(theft signal), not just the reused one.

### Env vars

| Variable | Default | Purpose |
| --- | --- | --- |
| `DIGINYAYA_JWT_SECRET` | random per-process | Signs access tokens — **set a real value in production**, or every backend restart invalidates every live session |
| `DIGINYAYA_ENV` | `development` | `production` rejects non-HTTPS requests to `/auth/*` and `/me` |
| `DIGINYAYA_DB` | `backend/diginyaya.db` | One shared SQLite file for cases *and* auth tables |
| `DIGINYAYA_FRONTEND_URL` | `http://localhost:5173` | Base URL for email-verification / password-reset links |

SMS and email are provider-stub interfaces (`app/auth/sms.py`, `app/auth/mail.py`) in local dev
by default; a real transactional email provider (**Resend**) is wired up for production — see
`DIGINYAYA_MAIL_FROM`/`RESEND_API_KEY` in `.env.example`.

### Migrations & tests

```bash
cd backend
python -m alembic upgrade head
python -m alembic revision --autogenerate -m "..."   # after changing app/auth/orm_models.py
python -m pytest -v
```

---

## API reference (selected)

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/dispute-types` | Available categories + tier metadata |
| `GET` | `/api/precedents` | Full precedent corpus |
| `GET` | `/api/sample-claim` | Pre-built demo claim + respondent reply |
| `POST` | `/api/cases` | File a new claim as a draft (auth required) |
| `POST` | `/api/cases/{id}/documents` | Upload evidence (multipart) |
| `POST` | `/api/cases/{id}/preliminary-review` | Non-binding pre-filing evidence + case-strength check |
| `POST` | `/api/cases/{id}/submit` | The real "file & notify respondent" transition out of `draft`; requires `{"confirmed_accurate": true}` |
| `POST` | `/api/cases/{id}/respond` | Respondent files a reply |
| `POST` | `/api/cases/{id}/skip-response` | Proceed uncontested |
| `POST` | `/api/cases/{id}/request-review` | Manually escalate to human review, any tier |
| `POST` | `/api/cases/{id}/run` | Start the agent pipeline as a background job |
| `POST` | `/api/cases/{id}/mediation` | Accept / decline mediation; starts resolution job |
| `GET` | `/api/cases/{id}/mediation/audio` | Mediation proposal read aloud (Sarvam Bulbul TTS) |
| `GET` | `/api/cases/{id}/resolution/audio` | Resolution order read aloud (Sarvam Bulbul TTS) |
| `GET` | `/api/cases/{id}/events?after=<seq>` | SSE — replay from cursor + live stream (incl. tokens) |
| `GET` | `/api/cases/{id}` | Full case view |
| `GET` | `/api/ai-status` | Which engine is active |
| `GET` | `/api/reviews/queue` | Cases awaiting human review (reviewer-only) |
| `GET` | `/api/reviews/ops-metrics` | Case volume, tier/status split, escalation rate (reviewer-only) |
| `GET` | `/api/reviews/eval-metrics` | Real-judgment eval summary: AI resolution vs. real court outcome (reviewer-only) |
| `GET` | `/api/reviews/{id}` | Full case detail for a reviewer |
| `GET` | `/api/reviews/{id}/audit-verify` | Verify a case's tamper-evident event hash chain (reviewer-only) |
| `POST` | `/api/reviews/{id}/decision` | Submit a reviewer decision |

All `/api/cases/...` endpoints require `Authorization: Bearer <access_token>` and enforce case
ownership; `/api/reviews/...` endpoints require the same header plus `is_reviewer`.

Every `/api/...` route above is also reachable at `/api/v1/...` (`app/core/versioning.py` rewrites
the prefix before routing, so this isn't duplicated route-by-route). The frontend calls the
unversioned `/api/...` paths and always gets current behavior; `/api/v1/...` exists as a stable
path for a future external consumer (mobile app, partner integration) to pin to before this
backend's first breaking API change.

`GET /api/me/data-export` is a self-service export of everything the current user owns (profile,
full case records, documents including extracted text, event log) — the DPDP Act 2023
access/portability principle. It's deliberately export-only: there's no matching self-service
delete. See **Known issues** below for why that's an open decision, not an oversight.

---

## Deployment

The application is deployment-ready and currently deployed on **Render**:

- **Backend** — FastAPI app deployed as a native Python buildpack (`python -m uvicorn app.main:app`).
- **Frontend** — the Vite build deployed as a static site.
- **AI provider** — Sarvam AI, reached over the network from the Render backend (no local model
  or GPU needed in production).
- **Email** — Resend, for verification/reset links and case notifications.

Deployment architecture includes: environment-driven configuration (no secrets in code), a
modular backend/frontend split deployable independently, and GitHub-integrated redeploys on push.

The most important current limitation — Render's free tier gives the backend an **ephemeral**
container disk, so the SQLite database is wiped on every redeploy and idle-timeout spin-down — is
tracked in **Known issues** below, along with the Postgres migration that resolves it.

---

## Known issues / technical debt

- **No persistent database in production.** `diginyaya.db` lives on Render's free-tier
  ephemeral container disk. Every redeploy — and the free tier's idle-timeout spin-down/wake
  cycle — wipes it clean: every account, case, and session, gone. Root cause of "repeated
  login prompts" on the live site. Needs a real migration (Supabase/Neon Postgres, or a paid
  Render persistent disk) before relying on the live deployment for anything durable.
- ~~Image OCR is broken in production~~ — fixed by switching OCR to Sarvam Document AI (a
  cloud API call, sidesteps the Render Tesseract-install issue entirely). See the Document
  upload section above.
- **`DIGINYAYA_JWT_SECRET`** — confirm it's set as a stable Render env var, or restarts
  invalidate every session on top of the DB-wipe problem above.
- **Backend-sourced content isn't localized.** Dispute-type names/descriptions
  (`app/data/loader.py`) are hardcoded English and never routed through the i18n pipeline —
  switching the language dropdown translates all frontend-owned copy but not this.
- **Eval dataset is small and costs real money to grow** (Indian Kanoon API is billed per
  call) — not something to casually expand.
- **No general admin role** — `is_reviewer` is the one deliberately narrow capability that
  exists, granted only via CLI script.
- **No data retention/erasure policy yet.** DPDP Act 2023 grants a right to erasure, but a
  case record also doubles as an evidentiary/audit record for an actual dispute (the
  tamper-evident event log, see above, exists specifically to keep that history intact) —
  deciding what's legally safe to purge vs. must be retained, and for how long, is a legal/
  product decision this codebase shouldn't make unilaterally. `GET /api/me/data-export` covers
  the access/portability principle today; a deletion flow is deliberately not built until that
  policy decision is made.

---

## Future Enhancements

- A real, persistent production database (Postgres) — the single highest-priority item; see
  **Known issues** above.
- All four dispute types reaching real production traffic.
- Tier 2 → Tier 3: complex civil and criminal matters with mandatory human sign-off.
- Government ODR integration, High Court partnerships.
- Batching Sarvam Document AI calls for PDFs over its 10-page limit (currently falls back to
  Tesseract instead of splitting into multiple ≤10-page jobs).
- Scaling precedent retrieval to a dedicated vector DB (Qdrant/pgvector) once the corpus
  outgrows the in-process store in `app/rag/index.py` — the orchestrator, agent contract and
  API stay identical either way.
- A self-service data deletion flow, once the legal retention-vs-erasure policy question
  (see **Known issues**) is resolved.
- A real SMS provider and cloud object storage (S3/GCS) for evidence uploads, replacing the
  current local-disk/console-log stubs.

---

## Author

**Shivaum Shekhar Sharma**
Computer Science Engineering (Data Science), Manipal Institute of Technology, Bengaluru

---

_Demonstration prototype. Generated resolutions are illustrative and do not constitute a
court order or legal advice._
