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

## 8. Decisions needed
1. Team size and timeline, and whether there is funding for cloud and LLM spend.
2. B2B first or B2C first (see the founder notes), which changes Phase 1 and 3.
3. Which states and languages are in the first production release.
4. Appetite for a lawyer or legal adviser on the team, which gates Waves 1 to 3.
