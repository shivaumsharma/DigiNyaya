# DigiNyaya platform plan: from a civil prototype to a production system for the wider judiciary

Status: a plan to review, not a commitment. Items marked **(verify)** need a lawyer or a measurement before they are relied on.
Current-state facts come from the repository; targets are proposals.

## 1. Scope: what "all cases" should mean

"All cases in the judiciary" is not one product. Different matter types carry different risks, and the safe depth of AI
involvement is different for each. The plan therefore scopes every matter type by **capability level**, not by yes/no:

| Level | What the system does | Human role |
|---|---|---|
| **L0 Navigate** | Explains the process, the right forum, limitation periods, the documents needed, deadlines | None needed; information only |
| **L1 Prepare** | Intake, evidence checklist and bundle, drafts of notices and filings | The user (or lawyer) reviews and files |
| **L2 Resolve** | Mediation-style settlement proposal, citations, a signed settlement | A human reviewer counter-signs (current Tier 2) |
| **L3 Decide** | An autonomous outcome | Reserved for narrow, low-risk, consent-based types only (today: consumer disputes) |

**Rule: a matter type may not move up a level until it has passed its own evaluation gate** (section 5).

### Proposed matter map
| Wave | Matter types | Ceiling level | Why |
|---|---|---|---|
| 0 (exists) | Money recovery, contract breach, cheque bounce, consumer | L2 (consumer L3) | Built, evaluated on 23,841 judgments |
| 1 | Tenancy, property and neighbour, employment and labour, partnership, insurance, cheque-bounce (NI Act s.138) | L2 | Civil, money or possession, plenty of data in the corpus; preview types already registered |
| 2 | Motor accident claims, commercial disputes, execution, family mediation (consent-based) | L1 to L2 | Need domain rules (compensation tables, family-law sensitivity) |
| 3 | Criminal, constitutional and writ, tax, service, IP | **L0 to L1 only** | The system must not predict or recommend outcomes affecting liberty or fundamental rights; it can still explain process, forum, deadlines and prepare documents |

Reasons for the ceiling on Wave 3 (verify with counsel): liberty and rights matters, the rules on who may give legal advice or
appear, and the policies some High Courts have issued on AI use in judicial work.
For settlement enforceability, the [Mediation Act, 2023](https://www.drishtiias.com/current-affairs-news-analysis-editorials/news-editorials/27-09-2023/print/manual)
recognises online mediation and makes a mediated settlement enforceable like a decree, which supports L2 for consent-based civil
matters (verify details and what has commenced).

## 2. Where the system is today (from the code)
* One FastAPI service; background work runs on an in-process thread pool (`DIGINYAYA_JOB_WORKERS`, default 4); live events use an in-memory bus plus an append-only database log.
* SQLite in development, PostgreSQL in production; AWS via Terraform (Elastic Beanstalk, RDS, S3, CloudFront).
* Precedents are a 217-entry JSON file with embedding retrieval; statutes a 31-entry JSON (unreviewed).
* Dispute types are registered in `app/data/loader.py` with a `behaves_as` mapping; safety gate, evidence guidance and statutes hang off the type.
* Rate limits are database-backed (correct across processes); LLM calls sit behind circuit breakers; Sarvam is the production provider.
* Evaluation: real-judgment harness, outcome-leakage audit, leakage-free control, frozen contested held-out set, judge-vs-human agreement tooling (labels pending).

## 3. Target architecture
```
Clients: web (PWA), Android, WhatsApp, voice
        |
   API gateway / CDN (TLS, WAF, rate limits)
        |
   Stateless API pods (FastAPI)  -- auth, intake, case read/write, SSE
        |                    \
   Durable queue          Redis (cache, rate limits, SSE fan-out)
 (SQS or Redis streams)
        |
   Worker pools, autoscaled per queue:
     ocr/extraction | pipeline | resolution | translation/tts | evaluation
        |
   LLM gateway: provider abstraction, budgets, caching, retries, circuit breakers, fallback
        |
   PostgreSQL (Multi-AZ, partitioned event log) + pgvector or Qdrant | S3 (evidence, encrypted)
        |
   Observability: logs, metrics, traces, cost per case, eval dashboards
```
### Changes from today, in priority order
1. **Durable job queue and separate workers.** Today a restart or scale-out can lose in-flight work and the thread pool caps throughput. Move jobs to a queue with retries, idempotency keys and per-queue autoscaling. *(L)*
2. **Shared event bus.** Replace the in-memory bus with Redis pub/sub (or Postgres LISTEN/NOTIFY) so SSE works across many API pods; keep the replayable log. *(M)*
3. **PostgreSQL only; drop SQLite from production paths.** Partition the event log by month; add read replicas when needed; connection pooling. *(M)*
4. **Vector search in the database or Qdrant** instead of a JSON file, with per-matter corpora and versioning. *(M)*
5. **Matter modules.** Turn the dispute-type registry into a plugin contract: intake schema, evidence guidance, statutes, precedents, safety rules, maximum capability level, evaluation set, prompts. Adding a type then means adding a module, not editing the pipeline. *(L)*
6. **LLM gateway.** One place for provider choice, per-tenant budgets, response caching, quota handling and fallbacks; provider vendor concentration (Sarvam) is a business risk to manage. *(M)*
7. **Multi-tenancy** for B2B customers: tenant-scoped data, API keys, audit, usage metering. *(L)*
8. **Operations.** Infrastructure as code (exists), blue-green deploys, autoscaling, backups with restore drills, SLOs and alerting, load tests in CI (a `load_test.py` exists), secrets management, cost alerts. *(M)*

### Devices and reach at scale
* Mobile-first progressive web app first, with low-bandwidth mode, resumable uploads, local-language and voice input. Android and WhatsApp channels after the core is stable; WhatsApp is likely the highest-reach channel (verify provider terms and cost).
* Capacity planning is arrival rate times seconds of pipeline work per case. The real ceiling is LLM provider quota and cost per case, not CPU. Measure cost per case (`scripts/measure_eval_cost.py`) before setting price or capacity targets.

## 4. Security, privacy and governance (required for production)
* DPDP Act 2023: consent, purpose limitation, erasure vs evidentiary retention (an open policy question in the README), data export (exists), breach process. Keep data in India.
* Encryption at rest and in transit, evidence in S3 with per-case access control, tamper-evident audit log (exists), reviewer access logging.
* Human oversight by level (section 1), clear disclosure that outputs are recommendations, and an incident process for wrong outputs.
* A security review and penetration test before public launch.

## 5. Evaluation gate for every new matter type
A matter type may not reach L2 until it passes all of:
1. At least a few hundred real cases with the case ending recorded (ex parte, contested, settled), so default decrees cannot inflate results.
2. A frozen held-out contested test set (`scripts/freeze_heldout.py`).
3. Judge validated against human labels with a published agreement bound (`scripts/judge_human_agreement.py`).
4. Balanced accuracy and respondent-win recall reported with intervals, and a leakage-free control.
5. Escalation calibrated on validation data (`scripts/selective_guarantee.py`).
6. A domain lawyer's review of statutes and prompts.
Until then, the type ships at L0 or L1 only.

## 6. Phased roadmap (effort sizes; calendar time depends on team size, which is an open input)
| Phase | Goal | Main work | Exit criteria |
|---|---|---|---|
| **0 Foundations** | Make the current scope trustworthy and scalable | Human labels and judge validation; matter-module interface; queue and workers; shared event bus; Postgres only; observability; load test; security review | Judge agreement bound published; load test at target concurrency passes; no lost jobs on restart |
| **1 Production pilot** | Real users, current matter types | Mobile PWA polish, Telugu/Hindi first, paid or partner pilot, human reviewer ops, cost per case | Pilot cases resolved; measured cost per case, reply rate of respondents, reviewer time per case |
| **2 Breadth (Wave 1)** | More civil types | One matter module at a time through the section 5 gate | Each type has a passed gate or stays at L0/L1 |
| **3 Reach** | Channels and B2B | WhatsApp/voice, B2B API and multi-tenancy, partner integrations | Tenants onboarded; SLOs met |
| **4 Wave 2 and 3** | Complex civil, then L0/L1 navigators for criminal, writs, tax | Domain rules, lawyer review, navigator content only for Wave 3 | Legal sign-off per type |

## 7. Risks
* **Scope creep and solo bandwidth:** "all cases" is years of work; the gate and the wave order are the defence.
* **Legal and regulatory:** unauthorised-practice rules, court AI-use policies, enforceability; get counsel before Wave 1 launches.
* **Vendor and cost risk:** one LLM provider, per-case LLM cost; the gateway and caching mitigate it.
* **Data bias:** the corpus is almost all two states and mostly default decrees; expansion needs new sources per state and language.
* **Accuracy claims:** current validated skill is modest (balanced accuracy 58.6% on contested cases); sell the process, not outcome prediction.

## 8. Decisions taken (4 Nov 2026)
* No funding: avoid paid infrastructure where possible (a database-backed job queue before SQS or Redis; free-tier LLM and cloud; measure cost per case first).
* B2C first. A lawyer will be involved once there is something strong to present; until then every navigator guide is marked unreviewed.
* No state or language is excluded: breadth ships as navigator (L0/L1) matters first; resolution (L2) is still gated per matter.

## 9. Build log
| Date | Step | Status |
|---|---|---|
| 4 Nov | Matter registry with capability ceilings, policy enforced by tests (`app/matters`, `tests/test_matters.py`); 12 navigator guides; `GET /api/matters`, `GET /api/matters/{id}/guide` | Done |
| 4 Nov | Durable job queue on the application database (`app/jobqueue.py`): at-least-once delivery, retries with backoff, lease recovery after a crash, per-kind worker claims; in-process workers drain it at startup. `DIGINYAYA_DURABLE_JOBS=0` restores the old thread pool. Alembic migration `a3c5e7f9b1d2` | Done |
| 5 Nov | Workers as their own process: `python -m app.worker` (graceful SIGTERM drain, queue stats log), API switch `DIGINYAYA_RUN_WORKERS=0` | Done |
| 5 Nov | Cross-process job state: `job_queue.subject` and a UNIQUE `dedupe_key` give atomic "one phase per case" across every process; `jobs.is_running` and friends read the queue instead of in-process dicts | Done |
| 5 Nov | Shared event bus: `DbEventBus` over an `event_stream` table (adaptive polling, 10-minute retention); `DIGINYAYA_EVENT_BUS=memory` keeps the old in-process bus. Alembic `b4d6f8a0c2e3`. `docker-compose.yml` runs Postgres + API + worker (not run in CI) | Done |
| 5 Nov | Load test (`scripts/load_test_platform.py`, results in `docs/LOAD_TEST.md`): found and fixed a stale-status race, per-stream polling and thread-per-stream SSE (now one dispatcher + async streams), connection-pool exhaustion, and the per-process JWT key; 300-user burst completes with zero errors on both setups (scripted engine, no LLM) | Done |
| next | Re-run the load test with the Sarvam key (LLM latency, quota, cost per case); replace polling with Postgres LISTEN/NOTIFY or Redis if many API processes need it | Planned |
| 4 Nov | Frontend: public `/matters` browser (search, grouped by what you can file / roadmap / guides) and `/matters/:id` guide pages, header link, phone-width header fix, 11 vitest tests. UI strings are in `en.json` | Done |
| 6 Nov | Translated the matters UI strings and all 12 guides into the 10 other languages (hi, ta, te, kn, ml, mr, bn, gu, pa, od). Written directly, without the Sarvam key, so they are **unreviewed machine translations**: the API returns `language` and `translation_reviewed: false`, the guide page shows a notice, and a missing translation falls back to English with a notice. `GET /api/matters` and `/guide` take `?lang=`. Tests check structure parity with English, script, and the API (`tests/test_matter_translations.py`) | Done (needs native and legal review) |
| 6 Nov | **Tenancy module, step 1 (the part that needs no corpus or LLM key).** (a) A tenancy preparation guide (`guides.COMPANION_GUIDES`): served at `/matters/tenancy_dispute` while the filing type stays on the roadmap; no notice periods, limitation periods or amounts, because tenancy law is mostly state law; warns against lock-outs and utility cut-offs; English only until translated, unreviewed. (b) `scripts/matter_gate_report.py --matter <id>`: measures the two gate checks that can be measured (cases with endings and how many contested; contested test-set size), lists the commands for the four that cannot, never marks a manual check as met, and flags a registry that claims a check the data does not support. Thresholds (300 cases, 100 contested, 50 test) are proposals | Done |
| 6 Nov | **Employment module, step 1**, same shape as tenancy: preparation guide for unpaid pay claims (no wage statute cited because it is unconfirmed which Labour Codes now apply; says the forum depends on the claim and sends other workplace disputes to the labour guide), gate report works with `--matter employment_dispute`. One test now enforces the rules for every companion guide (no durations, amounts or named Acts; always sends the reader to confirm forum and time limit; stays unreviewed) | Done |
| 6 Nov | **Property module, step 1**: preparation guide (title and survey papers, dated photos, no self-help; says urgent injunctions are not handled by the tool and go to a human, which a test ties to the safety gate's real keyword list). `--matter property_dispute` works in the gate report. All three Wave-1 civil types so far have a guide; none has passed the gate | Done |
| 6 Nov | **Partnership module, step 1**: a new dispute type `partnership_dispute` (preview, inactive) built like the other Wave-1 types: behaves exactly as `contract_breach` (proved by the same 18-run equivalence test), own label, evidence guidance, sample claim, safety-gate registration, native eval category `partnership_business_disputes`, and a preparation guide (read the deed first, it may require arbitration; do not move firm money). It reuses the existing contract provisions: **no Partnership Act sections were added** because I could not confirm them here, a lawyer should choose them. Added to the precedent-source split defaults. Note: on the next gate report, `contract_breach` no longer counts the partnership category (it now belongs to the new type) | Done |
| next | **Tenancy, step 2 (needs your machine and the key).** Run `matter_gate_report` on the real corpus; source more tenancy cases if short (the AP/TG bucket has them); label a tenancy sheet for the judge; build native precedents from the precedent-source split only (docs/CIVIL_EXPANSION.md phase 2) | Planned |
| next | Native-speaker review of the guide translations (legal text), then set `translation_reviewed`; run `scripts/translate_ui_strings.py` / `verify_translations.py` with the Sarvam key as a second opinion | Planned |

## 10. Decisions still needed
1. Team size and timeline, and whether there is funding for cloud and LLM spend.
2. B2B first or B2C first (see the founder notes), which changes Phase 1 and 3.
3. Which states and languages are in the first production release.
4. Appetite for a lawyer or legal adviser on the team, which gates Waves 1 to 3.
