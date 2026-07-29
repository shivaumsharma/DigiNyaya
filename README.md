# DigiNyaya — AI-Native Civil Dispute Resolution

> Justice in 30 minutes, not 7 years.

DigiNyaya reimagines the civil court process from scratch. Instead of filing a case,
hiring a lawyer and waiting years, a citizen logs in, submits a dispute, and **five
coordinated AI agents** parse the claim, research precedent, analyse both sides,
mediate, and issue a binding resolution — end to end.

This repository is the **Phase 1 Hackathon MVP**: the _Consumer Dispute_ journey working
end-to-end with all five agents, demoable in under 5 minutes.

---

## What's inside

```
DigiNyaya/
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
│           ├── precedents.json  120 real Indian consumer-court precedents
│           └── loader.py        Corpus + dispute-type metadata
│   └── scripts/
│       ├── ingest_judgments.py Pulls real NCDRC/consumer-court judgments from the
│       │                       Indian Kanoon API, caches them, and uses the local LLM
│       │                       to extract structured fields into precedents.json
│       ├── eval_cases.py    Golden-case evaluation harness
│       └── smoke_http.py    End-to-end HTTP + SSE smoke test
└── frontend/                React (Vite) — the live demo UI
    └── src/
        ├── pages/           Landing, Disputes, NewCase, Respondent, Resolve
        ├── components/      Stepper, ResolutionDoc
        └── api.js           REST client + auth + SSE streaming helper
```

### The multi-agent architecture

| Agent                             | Role                                                                                                                                                                                                                                                                                     |
| --------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Orchestrator**                  | A hand-rolled **state machine**, not a linear script. It routes on Agent 1's confidence (Tier 1 autonomous vs. Tier 2 escalation), loops back to Research when coverage is thin, pauses for the mediation decision, then resumes. The routing decision actually changes the final order. |
| **Agent 1 · Ingestion**           | Parses the claim + evidence into structured facts and emits a **confidence** + recommended tier that steers routing.                                                                                                                                                                     |
| **Agent 2 · Precedent Research**  | **Semantic retrieval** over the corpus via embeddings (cosine), with transparent keyword fallback; reports coverage and method.                                                                                                                                                          |
| **Agent 3 · Argument Analysis**   | Neutral LLM summary + a **strength score** that feeds the mediation quantum; flags thin coverage to trigger the research loop.                                                                                                                                                           |
| **Agent 4 · Mediation**           | The LLM returns a **structured proposal** (outcome, relief ratio, window); a deterministic validator **clamps every number** to a precedent-derived band.                                                                                                                                |
| **Agent 5 · Resolution Drafting** | **Streams** the reasoned findings token-by-token; the operative order, amounts and deadline are deterministic, and citations are verified against what was actually retrieved.                                                                                                           |

Each agent reads and writes a single **typed `CaseContext` blackboard**. Work runs in a
**background job** (so a refresh never kills or duplicates a run) and every event is written
to an **append-only SQLite log** _and_ an in-memory bus. The UI subscribes to
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

| Variable                       | Default                  | Purpose                                                                                                                                                                                         |
| ------------------------------ | ------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `DIGINYAYA_USE_LLM`            | `1`                      | Set to `0` to force scripted mode                                                                                                                                                               |
| `DIGINYAYA_LLM_MODEL`          | `qwen2.5:7b-instruct`    | Any Ollama model tag                                                                                                                                                                            |
| `DIGINYAYA_OLLAMA_URL`         | `http://localhost:11434` | Ollama server URL                                                                                                                                                                               |
| `DIGINYAYA_INDIANKANOON_TOKEN` | —                        | Only needed to run `scripts/ingest_judgments.py` and pull more real judgments from the [Indian Kanoon API](https://api.indiankanoon.org/) (billed per call); not required to run the app itself |

> **Speed note:** on a CPU-only machine the 7B model takes ~10–15s per reasoning step
> (~40–70s total pipeline). For a snappier live demo, switch to the 1.5B model:
> `set DIGINYAYA_LLM_MODEL=qwen2.5:1.5b` (Windows) before launching uvicorn. The backend
> pre-warms the model on startup so the first demo call is fast.

### Document upload, OCR & discrepancy detection

Citizens can attach real PDF/JPEG/PNG evidence at filing time (`POST /api/cases/{id}/documents`,
multipart, up to `DIGINYAYA_MAX_UPLOAD_MB` per file — default 15MB). Extraction and a
discrepancy-check across a case's documents (conflicting dates/amounts, inconsistent names,
missing signatures) run as background jobs, the same decoupled-from-the-HTTP-request pattern
as the 5-agent pipeline (see `app/jobs.py`).

**Tesseract OCR setup (needed for scanned PDFs and photographed documents — native-text PDFs
extract directly via PyMuPDF and need no OCR engine at all):**

```bash
# Windows: install the UB-Mannheim Tesseract build from
# https://github.com/UB-Mannheim/tesseract/wiki, then confirm it's on PATH:
tesseract --version

# macOS
brew install tesseract tesseract-lang

# Linux (Debian/Ubuntu)
sudo apt install tesseract-ocr tesseract-ocr-hin tesseract-ocr-ben  # + other Indic packs as needed
```

Without Tesseract installed, native-text PDF uploads still work fully; scanned PDFs and
image uploads fail extraction gracefully (`extraction_status: failed` with an `error_message`,
never a crash) — matching how every other external dependency in this app degrades (see the
_AI engine_ section above).

| Variable                     | Default          | Purpose                                                                                          |
| ----------------------------- | ---------------- | ------------------------------------------------------------------------------------------------ |
| `TESSERACT_LANG`             | `eng`             | Tesseract language pack(s), e.g. `eng+hin+ben` — only packs actually installed can be listed here |
| `DIGINYAYA_STORAGE_PROVIDER` | `local`           | `local` is the only implemented backend today; `s3`/`gcs` raise `NotImplementedError` from `app/storage/factory.py` until implemented |
| `DIGINYAYA_STORAGE_ROOT`     | `backend/uploads` | Local filesystem root for uploaded files (dev-only; not committed — see `.gitignore`)             |
| `DIGINYAYA_MAX_UPLOAD_MB`    | `15`              | Per-file upload size cap                                                                          |

**Sarvam OCR/vision evaluation note:** Sarvam's own documentation references a "Sarvam
Vision"/Document Intelligence product ("turn PDFs, scans, and handwritten documents into
structured, searchable data"), which could plausibly replace Tesseract with better Indic-script
coverage. It is **not** implemented in `app/llm/providers/sarvam.py` today — the detailed API
reference (endpoint shape, pricing, exact Indic-language coverage) wasn't reachable during this
evaluation. This is a real, worth-revisiting follow-up, not a blocker: Tesseract is a fully
working default, and swapping it in later only requires a new implementation behind
`app/documents/extraction.py`'s `ocr_image()` contract, not a redesign.

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

1. **Log in** — the landing page is pre-filled (Ananya Sharma, Aadhaar `4821`). Click _Verify & continue_.
2. **Pick** _Consumer Dispute_.
3. On the claim form, click **"Load demo case"** — a ₹42,999 undelivered-laptop dispute with 3 evidence items auto-fills. Click _File claim & notify respondent_.
4. On the respondent screen, either click **"Load demo reply"** (seller offers store credit / ₹20,000) and submit, **or** click **"Skip — proceed uncontested"**.
5. **Watch the theatre**: the orchestrator boots and shows its **routing decision**, then Agents 1→4 run live, each surfacing real output (extracted facts + confidence, retrieved precedents + method, strength + contradictions). If coverage is thin you'll see it **loop back** to Research.
6. **Mediation proposal** appears with a validator-clamped quantum. Click **Accept** (settles) or **Decline** (Agent 5 issues an autonomous order).
7. **Binding resolution document** renders, with the findings **streamed token-by-token**, cited (verified) precedents and a compliance deadline. Download it.
8. **Refresh the page mid-flow** — the stream replays from the durable log and resumes exactly where it was. (Bonus: low-confidence/empty claims **escalate to Tier 2** with a human-sign-off order.)

> Then zoom out: _this is one dispute resolved in 4 minutes — there are 50 million waiting._

---

## API reference (selected)

| Method | Path                                 | Purpose                                                   |
| ------ | ------------------------------------ | --------------------------------------------------------- |
| `POST` | `/api/login`                         | Simulated Aadhaar login                                   |
| `GET`  | `/api/dispute-types`                 | Available categories + tier metadata                      |
| `GET`  | `/api/precedents`                    | Full precedent corpus (120 real consumer-court judgments) |
| `GET`  | `/api/sample-claim`                  | Pre-built demo claim + respondent reply                   |
| `POST` | `/api/cases`                         | File a new claim (auth required; sets owner)              |
| `POST` | `/api/cases/{id}/respond`            | Respondent files a reply                                  |
| `POST` | `/api/cases/{id}/skip-response`      | Proceed uncontested                                       |
| `POST` | `/api/cases/{id}/run`                | Start the agent pipeline as a background job (idempotent) |
| `POST` | `/api/cases/{id}/mediation`          | Accept / decline mediation; starts resolution job         |
| `GET`  | `/api/cases/{id}/events?after=<seq>` | **SSE** — replay from cursor + live stream (incl. tokens) |
| `GET`  | `/api/cases/{id}`                    | Full case view                                            |
| `GET`  | `/api/ai-status`                     | Which engine is active (local LLM vs scripted)            |

> All `/api/cases/...` endpoints require an `Authorization: Bearer <token>` header (the token
> is returned by `/api/login`) and enforce case ownership.

---

## Authentication (citizen accounts)

A standalone dual-path signup/login system (phone+OTP and email+password), independent from
the `/api/login` Aadhaar-demo flow above that still gates case filing — **the two are not yet
wired together**; a future task migrates case ownership from the Aadhaar-demo `citizen_id` to
the `user.id` this system produces. Lives in `backend/app/auth/`.

**Data layer:** SQLite (same `diginyaya.db` file `db.py` already uses) via SQLAlchemy +
Alembic migrations (`backend/alembic/`). The original spec called for Postgres' `citext` on
`email`; this app has no Postgres anywhere, so case-insensitive email uniqueness is done by
always storing/comparing a lowercased column instead.

### Endpoints

| Method | Path                          | Auth required | Purpose                                            |
| ------ | ----------------------------- | -------------- | --------------------------------------------------- |
| `POST` | `/auth/signup/email`          | —              | Create an account with email + password              |
| `POST` | `/auth/signup/phone/start`    | —              | Send a signup OTP to a phone number                  |
| `POST` | `/auth/signup/phone/verify`   | —              | Verify OTP, create the account                       |
| `POST` | `/auth/login/email`           | —              | Log in with email + password                          |
| `POST` | `/auth/login/phone/start`     | —              | Send a login OTP                                      |
| `POST` | `/auth/login/phone/verify`    | —              | Verify OTP, log in                                    |
| `POST` | `/auth/link/phone/start`      | yes            | Send an OTP to add a phone number to the current account |
| `POST` | `/auth/link/phone/verify`     | yes            | Verify OTP, link the phone (no duplicate user created) |
| `POST` | `/auth/refresh`                | reads cookie   | Rotate the refresh token, issue a new access token    |
| `POST` | `/auth/logout`                 | yes            | Revoke the refresh token server-side                  |
| `POST` | `/auth/password/reset/request` | —             | Enumeration-safe: always returns the same message     |
| `POST` | `/auth/password/reset/confirm` | —             | Consume the reset token, set a new password           |
| `GET`  | `/auth/verify-email?token=...` | —             | Consume the verification token                        |
| `GET`  | `/me`                          | yes            | Current user's profile                                |

`yes` = `Authorization: Bearer <access_token>` header required (15-minute JWT). The refresh
token is a 7-day opaque value in an **httpOnly** cookie scoped to `/auth`, rotated on every use
— reusing an already-rotated-out refresh token revokes every token descended from that login
(theft signal), not just the reused one.

### Env vars

| Variable                  | Default                 | Purpose                                                                                   |
| -------------------------- | ------------------------ | ------------------------------------------------------------------------------------------- |
| `DIGINYAYA_JWT_SECRET`     | random per-process       | Signs access tokens. Set a real value in production or sessions reset on every restart.     |
| `DIGINYAYA_ENV`            | `development`            | Set to `production` to reject non-HTTPS requests to any `/auth/*` or `/me` endpoint.        |
| `DIGINYAYA_DB`             | `backend/diginyaya.db`   | Same var `db.py` already reads — one shared SQLite file for cases *and* auth tables.        |
| `DIGINYAYA_FRONTEND_URL`   | `http://localhost:5173`  | Base URL used to build the email-verification and password-reset links.                    |

SMS and email are both **provider-stub interfaces** (`app/auth/sms.py`, `app/auth/mail.py`) —
in dev they log the OTP/link to the console instead of sending anything real. Swap
`get_sms_provider()`/`get_mail_provider()`'s return value for a real Twilio/MSG91/WhatsApp
Business API or SES/SendGrid/Postmark client later; nothing else changes.

### Migrations

```bash
cd backend
python -m alembic upgrade head      # apply migrations
python -m alembic revision --autogenerate -m "..."   # after changing app/auth/orm_models.py
```

### Tests

```bash
cd backend
python -m pytest tests/ -v
```

Covers password hashing (bcrypt cost 12), OTP hashing/rate-limiting (3 requests/15min, 5 verify
attempts), refresh-token rotation + reuse/theft detection, and enumeration-safe error responses
(login and password-reset return identical responses whether or not the account exists).

---

## Roadmap (beyond this MVP)

- **Phase 2** — All four Tier 1 case types (money recovery, contract breach, cheque bounce), real Aadhaar integration, mobile-first UI.
- **Phase 3** — Tier 2 AI-assisted virtual judge, High Court partnerships, vernacular languages.
- **Phase 4** — Tier 3 complex civil, criminal with mandatory human sign-off, government ODR integration.

The agents already use a real local LLM **and real semantic RAG** (embeddings + cosine, see
_AI engine_ above). Scaling to a larger corpus is a matter of swapping the in-process vector
store in `backend/app/rag/index.py` for a dedicated vector DB (e.g. Qdrant/pgvector) — the
orchestrator, agent contract and API stay identical.

---

_Demonstration prototype. Generated resolutions are illustrative and do not constitute a
court order or legal advice._
