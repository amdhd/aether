# Aether — Interview Prep & Architecture Knowledge Base

> Purpose of this doc: give you a **one-layer-deeper** understanding of this repo —
> not just *what* each piece does, but *why* it was built that way and what the
> trade-offs are. Enough to defend every design decision in an interview and to
> judge whether an AI-suggested change is a good idea before approving it.
>
> Cross-references: [README](../README.md) (overview), [docs/architecture/](../architecture/)
> (ADRs), [infra/terraform/README.md](../infra/terraform/README.md) (deploy runbook).

---

## 1. Elevator pitch (30 seconds)

Aether is a full-stack **AI personal/marketing assistant**: a FastAPI + async
SQLAlchemy backend exposing a **streaming, tool-calling agent** (DeepSeek) over
SSE, a React 19 + TypeScript SPA, and a **production-grade AWS deployment
defined entirely in Terraform** (VPC → ECS Fargate → RDS PostgreSQL + pgvector,
CloudFront/WAF edge, Secrets Manager, CI/CD with cost/security gates). The
standout bits: **refresh-token rotation with theft detection**, **global
rate-limiting and LLM spend guards**, a **RAG pipeline with a real eval
harness**, and a two-layer Terraform design where the whole stack is one command
up / one command down with **~$0 idle cost**.

---

## 2. Stack map (know every box)

| Layer | Technology | Notes |
|---|---|---|
| Backend | Python 3.12, FastAPI 0.139, Uvicorn | Async end-to-end (no thread pool for I/O) |
| ORM / migrations | SQLAlchemy 2.0 (async), Alembic | `asyncpg` driver for Postgres; `aiosqlite` for tests/dev |
| Data | PostgreSQL 16 + **pgvector** | One store for relational *and* vector search |
| Cache / shared state | Redis (optional, `REDIS_URL`) | Global rate limits + in-flight turn counter |
| LLM | DeepSeek (OpenAI-compatible SDK), thinking mode | Swappable behind one client module |
| Embeddings | OpenAI `text-embedding-3-small` | Optional; keyword fallback when no key |
| Auth | JWT (HS256) + bcrypt, rotating refresh tokens | python-jose, bcrypt 5.x |
| Encryption at rest | Fernet (cryptography) | Google OAuth tokens |
| Observability | Structured logs, CloudWatch EMF, OpenTelemetry | OTLP → ADOT sidecar → AWS X-Ray |
| Frontend | React 19, TypeScript, Vite 8, Tailwind v4 | shadcn-style (Radix + cva) components |
| Frontend state | TanStack Query (server data), Zustand (auth/theme), local state (ephemera) | Clear ownership split |
| Charts | Recharts 3 | Analytics dashboard |
| Infra | Terraform (modules), 2 layers | Persistent base + ephemeral stack |
| AWS | VPC, ECS Fargate (Graviton), ALB, RDS, ElastiCache, S3+CloudFront, WAF, Secrets Manager, CloudWatch/SNS/Budgets | See §8 |
| CI/CD | GitHub Actions + OIDC | Infracost / Checkov / Trivy gates |
| Tests | pytest (async, ASGI transport), Vitest + Testing Library | SQLite *and* Postgres legs in CI |

---

## 3. Application architecture

### 3.1 Request lifecycle (the chat turn)

```
Browser (SPA)
  │  POST /api/v1/conversations/{id}/messages   (multipart: content + optional .csv/.tsv)
  │  Authorization: Bearer <access token>
  ▼
ALB → FastAPI
  │  1. MaxBodySizeMiddleware (raw ASGI)      — rejects >1 MB *before* body parsing
  │  2. CORSMiddleware                        — outer layer, decorates 413s too
  │  3. get_current_user                      — JWT verify + token_version check vs DB
  │  4. enforce_chat_rate_limit               — sliding window (Redis or in-memory)
  │  5. enforce_monthly_cost_cap              — SUM(usage_logs this month) × price
  │  6. parse_tabular_file                    — stdlib csv, 200 KB / 300 rows bounds
  │  7. acquire_turn_slot                     — per-user in-flight ceiling (cap's backstop)
  ▼
stream_agent_response (AsyncGenerator) → StreamingResponse (text/event-stream)
  │  owns its own DB session (request session is already closed)
  │  agent loop: build context → call DeepSeek (stream=True) → tool calls → repeat
  │  SSE events: reasoning | token | tool_call | done | error
  │  finally: release_turn_slot  ← covers client disconnect too
```

**Why the generator owns its own session** (`loop.py:104-114`): FastAPI tears
down `yield` dependencies before the streaming body runs. Reusing the request's
session would mean writing through a closed session — a leak that's invisible
on SQLite but deadlocks Postgres. The `finally` releases the in-flight slot on
*every* exit path, including the user clicking Stop.

**Why SSE and not WebSockets**: the chat is one-way server→client streaming
plus one POST request; no bidirectional channel needed. SSE rides plain HTTP,
so it inherits auth headers, ALB timeouts, and load balancing for free. The
cost: the client uses `fetch` + a manual SSE parser (not `EventSource`), because
`EventSource` can't do POST + multipart + Authorization headers.

**Error handling mid-stream** (`loop.py:167-236`): headers are already sent
when the generator runs, so an exception would just truncate the stream with
no signal. Hence every upstream failure is caught and turned into an explicit
`error` SSE event.

### 3.2 The agent loop (`api/app/agent/loop.py`)

1. Persist the user message (+ attachment, stored on the message row).
2. `maybe_summarize_history` — fold old turns into `memory_summary` (see §4.3).
3. Build context: system prompt (persona + base) + current UTC time + summary
   + unsummarized history.
4. Loop up to `MAX_TOOL_ITERATIONS = 5`:
   - Stream DeepSeek (`thinking: enabled`, `stream_options.include_usage`) — usage
     arrives on the **last** chunk.
   - Run both streams through `VendorRedactor` (§5.4).
   - If tool calls: persist assistant msg (with tool_calls JSON), dispatch each
     tool, persist tool-result messages, feed back, loop.
   - Else: persist final message, set conversation title if still default, emit
     `done`.
5. Every turn writes a `UsageLog` row (tokens) and emits an EMF metric line.

**Trade-off: MAX_TOOL_ITERATIONS bounds runaway loops** — a model that keeps
calling tools can otherwise loop forever (billing!). Same reason the in-flight
turn ceiling exists.

### 3.3 Tools (`api/app/agent/tools.py`)

12 tools: CRUD tasks, CRUD/list notes, `search_notes` (semantic), `get_weather`
(data.gov.my, cached 1h in-process), `web_search` (Tavily), 3 Google Calendar
tools. JSON schemas go to the model; results are serialized back into the
conversation as `tool` messages.

- **Rate limits on paid/external tools** (`web_search` 10/min, `calendar` 20/min)
  — a chat rate limit caps requests, not third-party API calls, and those cost
  money.
- **Errors are returned to the model as JSON, not raised** — the model can
  adapt ("Calendar isn't connected — tell the user to connect it in Settings").
- Tool execution scopes every query by `user_id` — the model can only touch the
  caller's data.

---

## 4. Auth system — the deepest part of the repo

### 4.1 Token design

- **Access token**: JWT, HS256, 15 min, claims `{sub, ver, type, iat, exp}`.
  Held **in memory only** in the SPA (Zustand) — never localStorage, so XSS
  can't read it.
- **Refresh token**: JWT, 7 days, extra claims `{jti, fam}`, delivered as an
  **HttpOnly, Secure, SameSite cookie** scoped to path `/api/v1/auth` (never
  sent with ordinary API calls). JS can't read it.
- **`ver` claim = `users.token_version`**: every token is bound to the user's
  current version. Bump the version → *every* outstanding access + refresh
  token for that user dies instantly (logout-everywhere, password reset).

### 4.2 Rotation with reuse detection (`services/refresh_tokens.py`)

Each login starts a **family**. Every refresh is a **rotation**: presented token
revoked, fresh one issued in the same family. A refresh token is therefore
single-use. **Reuse detection**: presenting an already-revoked token means it
leaked (legit client and attacker both hold a copy — the loser replays), so the
**whole family is revoked** and `token_version` bumps.

The subtle part — **the rotation race** (`refresh_tokens.py:95-111`): a naive
"check `revoked`, then set `revoked`" leaves a window where two concurrent
requests both read `revoked=false` and both mint a successor — exactly the
replay the feature exists to catch. Fix: the claim is a **conditional UPDATE**
(`WHERE jti=? AND revoked IS FALSE`); whoever's `rowcount == 0` lost, and the
database is the arbiter. *This is a classic "optimistic locking via atomic
statement" pattern — great interview material.*

### 4.3 CSRF on `/refresh` (`api/deps.py`)

Cookie-authenticated endpoints are CSRF-prone; Bearer-header endpoints are
immune (an attacker's page can't read the token to set the header). `/refresh`
is the one cookie-authenticated endpoint. SameSite=lax would normally cover
it, **but in production the SPA and API are on different domains**, forcing
SameSite=none for the cookie. The fix:

- The SPA sends `X-Requested-With: XMLHttpRequest` on refresh/logout.
- That header is **not on the CORS safelist**, so sending it turns a cross-site
  request into a **preflight**, which CORSMiddleware only answers for
  `FRONTEND_ORIGIN`. A `<form>` post or a plain cross-site fetch can't set it.
- Only **presence** is checked, never the value — being able to send the header
  at all is the signal.
- **Ordering with the rate limiter** (`auth.py:96-105`): the rate limit runs
  *before* the CSRF check, so an attacker can't hammer the endpoint for free by
  omitting the header. This ordering rationale is exactly the kind of detail
  interviewers probe for.

### 4.4 Password & enumeration defenses (`core/security.py`)

- **bcrypt, 72-byte ceiling handled explicitly**: bcrypt 4.x+ *raises* on
  >72 bytes instead of truncating. Registration rejects long passwords up
  front; `verify_password` returns False (with a dummy compare for timing) so
  an over-long candidate can't 500 an unauthenticated endpoint.
- **Timing-equalized login**: a nonexistent account still runs a bcrypt verify
  against a precomputed dummy hash, so user-enumeration by response timing is
  defeated.
- **Per-IP auth rate limiting** (login/register/refresh): sliding window, with
  careful `X-Forwarded-For` handling — behind one ALB, only the **last**
  `TRUSTED_PROXY_HOPS` entry is trusted, because the caller controls everything
  further left (spoofable = free rate-limit buckets).

---

## 5. Spend guards & security hardening

### 5.1 Three layers of abuse protection (know the difference!)

| Guard | Bounds | Why it's not enough alone |
|---|---|---|
| **Rate limiter** (`core/rate_limit.py`) | requests/minute (chat 20, tools, auth) | Does nothing about *polite* overuse of expensive turns |
| **Monthly cost cap** (`core/cost_cap.py`) | $/user/month, from `UsageLog` sums × configured prices | Tokens are recorded only when a turn *finishes* — N simultaneous requests all read the same total and all pass |
| **In-flight turn ceiling** (`core/inflight.py`) | concurrent turns per user (default 2) | The cap's backstop: bounds the overshoot to ~2 turns |

This layering — and the ordering problem it solves — is a first-class
interview answer. The slot is claimed **after** attachment validation (a
rejected upload can't strand a slot) and released in the streaming generator's
`finally` (covers disconnect), with a Redis TTL (600 s) reclaiming slots leaked
by a killed process.

### 5.2 Sliding-window limiter with two backends

- **In-memory** (default): per-process deques, swept every 5 min so idle keys
  don't accumulate.
- **Redis** (`REDIS_URL` set): one **sorted set per key**, scored by wall-clock
  ms. One pipeline: drop-out-of-window → add → count → refresh TTL. Over-limit
  calls **undo their own member** so a rejected request doesn't penalize the
  next caller. `Retry-After` computed from the oldest entry's expiry.
- **Degrades, never fails**: a Redis outage logs a warning and falls back to
  per-instance limiting. Availability over strictness — the right call for a
  demo-grade service, and worth saying out loud.

### 5.3 Body-size gate (`core/body_limit.py`)

FastAPI parses the body *before* dependencies run, so rate limit + cost cap sit
downstream of an unbounded upload. This raw ASGI middleware runs ahead of
routing: rejects a declared `Content-Length` without reading a byte, and counts
the streamed body for undeclared/lying lengths, cutting off at the limit (413)
instead of spooling the rest to disk. Placed **inside** CORS so the 413 carries
CORS headers (otherwise the browser sees an opaque error).

### 5.4 Prompt-injection & model-output defenses

- **System prompt** marks tool output, web results, notes, and attachment
  blocks as *untrusted data, not instructions*; destructive actions not
  explicitly requested require confirmation; the assistant refuses to discuss
  its own model/vendor identity.
- **Attachment fencing** (`services/attachments.py`): filename sanitized (newlines,
  quotes, `<>`, control bytes stripped — it's echoed into a prompt!), content
  wrapped in `<attached_file>` tags with an explicit "data, never instructions"
  note; any `</attached_file>` lookalike in the file text is stripped so the
  file can't close its own fence and speak with the user's authority.
- **VendorRedactor** (`agent/redaction.py`): deterministic regex backstop that
  strips vendor/model names from streamed output — *deliberately not a security
  control* (timing fingerprints remain), just brand hygiene. The interesting
  engineering: a name can straddle a chunk boundary ("Deep" + "Seek"), so the
  redactor holds back the longest proper prefix of any vendor name and flushes
  it at stream end.
- **Fernet-encrypted Google tokens at rest**; disconnect calls Google's revoke
  endpoint (not just a local delete).

---

## 6. RAG: note search + eval harness

### 6.1 Retrieval (`services/note_search.py`)

- Notes are embedded whole (no chunking — a note is the retrieval unit) with
  `text-embedding-3-small` (1536-d) and stored in a **pgvector** column
  (`Vector(1536)`; JSON variant on SQLite so tests work keyless).
- Query: embed → cosine distance, drop results past
  `NOTE_SEARCH_MAX_DISTANCE` (0.6) so the agent isn't fed near-random notes.
- **Fallbacks all the way down**: no Postgres → keyword scan; no OpenAI key →
  keyword scan (tokenized, stopword-filtered, so a natural-language question
  actually matches); semantic search returns nothing → keyword scan.

### 6.2 Eval harness (`app/eval/`)

RAGAS-equivalent metrics (**faithfulness, context precision, answer
relevancy**) + retrieval recall, re-implemented against the app's own
DeepSeek judge instead of pulling the RAGAS/LangChain dependency tree. Why:
comparable numbers, and every formula is **defensible line by line** in a
review.

- Drives the **real retriever** — `search_notes` — not a mock.
- Every knob that moves a score is a CLI flag (`--k`, `--concurrency`,
  `--judge-model` separate from `--gen-model` so you can attribute score moves);
  each run stamps its config into the report.
- Samples carry per-phase token/cost/latency so a +0.02 faithfulness for 3×
  tokens is visible as a bad trade.
- **CI gate**: `--offline --check` (keyless deterministic heuristics) fails the
  build below conservative floors, and warns when run at an untuned config.
- The concurrency bound is per *LLM call* (semaphore), not per sample, because
  judging one sample fans out into many calls.

---

## 7. Memory & context management

- **Rolling summarization** (`agent/memory.py`): when unsummarized history
  exceeds ~24k chars (≈6k tokens, counting reasoning + tool args, not just
  visible text), the oldest part is folded into `memory_summary` by a separate
  DeepSeek call (thinking disabled), keeping the newest 10 messages verbatim.
- **The tool-pair boundary rule**: never cut between an assistant
  `tool_calls` message and its `tool` results — the API rejects a `tool`
  message with no preceding call. The split point advances until it's clean.
- The summarization call's own token usage is logged to `UsageLog` so analytics
  and the cost cap reflect *true* spend.
- `reasoning_content` is stored and **echoed back** for prior assistant turns —
  DeepSeek's thinking mode requires it or returns 400. (Vendor-specific quirk,
  documented in `loop.py:53-57`.)

---

## 8. AWS architecture (Terraform, two layers)

### 8.1 The two-layer split — and why it exists

| | **Layer 1 — `layer1_persistent`** | **Layer 2 — `layer2_ephemeral`** |
|---|---|---|
| Contents | ECR repo (scan-on-push, keep last 10), private S3 bucket + CloudFront + OAC, Secrets Manager (`aether/app`), ACM cert + Route 53 (optional), AWS Budgets alert | VPC, security groups, RDS, ALB, ECS Fargate, WAF, SNS alarms, CloudWatch dashboard, Redis (HA only) |
| Cost | ~$0.50/mo, survives `make down` | **Hourly billed** — destroyed by `make down` |
| Lifecycle | `make base-up` once | `make up` → demo → `make down` |

**Why**: the point of the project is to prove production-grade IaC *without
paying for it*. Everything that bills hourly (RDS, NAT, ALB, Fargate) lives in
layer 2 and disappears; everything that's near-free but slow to recreate
(ECR, S3, secrets, certs, budget alert) lives in layer 1. Layer 2 reads layer
1's outputs via `terraform_remote_state`; separate S3 remote state with
S3-native locking (`use_lockfile` — no DynamoDB needed on modern Terraform).
`make down` then `verify-clean` checks for orphaned Elastic IPs/ENIs (a
destroy that strands a NAT EIP would bill silently forever).

### 8.2 Networking (VPC module)

- `10.0.0.0/16`, 2 AZs, **three /20 tiers per AZ** via `cidrsubnet`: public
  (ALB + NAT), app (Fargate), db (RDS/Redis). No auto-assigned public IPs
  anywhere; compute and DB are private.
- IGW; **NAT: 1 in demo, 1 per AZ in HA** (`nat_count = high_availability ?
  az_count : 1`) — the demo's single NAT is an acknowledged SPOF. Outbound
  LLM/Tavily/Google calls egress through NAT.
- **PrivateLink everywhere free or cheap**: S3 *Gateway* endpoint (image
  layers don't touch NAT), plus *Interface* endpoints for ECR api/dkr, Secrets
  Manager, and CloudWatch Logs in the app subnets — image pulls, secret
  fetches, and log shipping stay on the AWS backbone. Default SG locked to
  deny-all.
- **Security group chain is reference-based, not CIDR-based**: ALB (443 from
  world) → API (8000 from ALB SG *only*) → RDS (5432 from API SG) / Redis
  (6379 from API SG). `create_before_destroy` throughout. No CIDR leakage —
  adding a service is "reference the SG", not "open a range".

### 8.3 Compute (ECS module)

- Fargate on **Graviton/ARM64** (cheaper), `awsvpc`, 512 CPU / 1024 MiB
  defaults, port 8000.
- **Secrets never in the image**: `valueFrom` injects from Secrets Manager —
  the whole `DATABASE_URL` secret plus 8 app keys by ARN + `json-key::` syntax.
- **Two least-privilege IAM roles**: execution role = base policy + inline
  `secretsmanager:GetSecretValue` scoped to the *exact* secret ARNs; task role
  is **empty by design** (the app calls no AWS APIs) and only gains
  `AWSXRayDaemonWriteAccess` when tracing is enabled. "Why does the task role
  have nothing?" is a great question to be asked — least privilege means not
  giving the workload permissions it doesn't need.
- **Rolling deploy with auto-rollback**: ECS deployment **circuit breaker**
  (`enable=true, rollback=true`) — if the new task def fails health checks
  repeatedly, ECS rolls back automatically. ALB idle timeout is **300 s**
  specifically because SSE chat streams are long-lived.
- **HA only**: target-tracking autoscaling (max 4, CPU 60%), Container
  Insights, and an **ADOT (AWS OTel Collector) sidecar** (non-essential
  container) receiving OTLP gRPC on loopback and forwarding to X-Ray — the app
  just points `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317`.
- HTTP listener → HTTPS redirect when a custom domain is set; without one the
  ALB is HTTP-only and the deploy refuses to call that "production-ready"
  (insecure-HTTP apply is blocked unless explicitly overridden) — the Secure
  cookie and mixed-content reasoning is why.

### 8.4 Data (RDS module)

- Postgres **16** (pgvector is built into RDS 16 — no separate extension
  install beyond `CREATE EXTENSION vector` in the migration), `db.t4g.small`,
  20 GB gp3, encrypted at rest, no public access.
- **Demo vs HA are different products**: demo = single-AZ, `skip_final_snapshot`,
  retention 0, `apply_immediately`; HA = Multi-AZ with automated failover,
  `deletion_protection=true`, 7-day backups + final snapshot.
- Password is a Terraform `random_password` stored in a layer-2 secret — the
  cred never appears in code or state as plaintext you typed.

### 8.5 Redis (HA only)

ElastiCache Redis 7.1: `cache.t4g.micro`, one replica, Multi-AZ with automatic
failover, encryption at rest + in transit, injected as `rediss://` →
`REDIS_URL`. One variable flips the rate limiter and in-flight counter from
per-process to global — this is *why* the app code has pluggable backends
(§5.2).

### 8.6 Edge & WAF

- **CloudFront + OAC** (origin access control, not legacy OAI) on a private
  S3 bucket with a source-ARN-conditioned bucket policy. Custom response
  headers policy: **CSP** (`script-src 'self'`, `connect-src 'self' <api>`,
  `style-src` allows `unsafe-inline` for React inline styles — the one
  deliberate relaxation), HSTS, `X-Frame-Options: DENY`, strict-origin
  referrer. `PriceClass_100` (cheap edge locations). 403/404 → `index.html`
  for SPA routing.
- **WAFv2 regional on the ALB**: rate-based rule (1000 req/5 min/IP, block),
  CommonRuleSet + KnownBadInputs. The subtle one: `SizeRestrictions_BODY` is
  overridden to **Count** because its 8 KB limit would 403 legitimate ~200 KB
  CSV attachments — the app's own body-limit middleware (§5.3) is the real
  gate. This override is exactly the kind of thing an interviewer might
  question; the answer is "we moved the enforcement to the layer that
  understands our traffic".

### 8.7 The `high_availability` boolean

A single variable re-shapes the whole stack: per-AZ NAT, ECS autoscaling,
Multi-AZ RDS + backups + deletion protection, and a Multi-AZ Redis replication
group. The demo shape is deliberately cost-optimized; the HA shape is
production-grade. Everything else (VPC, SGs, ALB, WAF) is identical — which is
the point: the *architecture* is production, only the *sizing* toggles.

### 8.8 Cost control as a feature

AWS Budgets alert ($10/mo default, 80% actual / 100% forecasted), SNS alarms
from CloudWatch, a CloudWatch dashboard (ALB/ECS/RDS **plus LLM
token/cost/latency from the EMF metrics**), a runaway-LLM-cost alarm, the
ephemeral apply/destroy model, `verify-clean` orphan checks, and Infracost in
CI. Cost is a first-class concern of the project, not an afterthought — that's
the framing to use when presenting it to a cloud role.

---

## 9. CI/CD & testing

### 9.1 CI (`ci.yml`) — three independent lanes

- **Backend**: ruff lint + pytest with two DB legs — SQLite (fast, keyless)
  *and* a `pgvector/pgvector:pg16` service container (the only way to exercise
  the pgvector semantic-search path and Postgres-only SQL). A throwaway Fernet
  key is generated in-run for the crypto tests.
- **RAG eval gate**: `python -m app.eval.run --offline --check` — the eval
  harness (§6.2) runs keyless/deterministically and fails the build if any
  metric drops below its floor. Eval is a *gate*, not an artifact.
- **Frontend**: lint + build + Vitest on Node 24.

### 9.2 Deploy (`deploy.yml`) — gated pipeline with OIDC

- **OIDC**: `configure-aws-credentials` with `id-token: write` — GitHub
  Actions assumes the deploy role via web identity, **no static AWS keys**.
  Deploy only runs on push/dispatch and only if `vars.AWS_DEPLOY_ROLE_ARN` is
  set.
- **Three security/cost gates before any deploy**:
  1. **Checkov** (IaC static analysis) — `soft_fail: false`, SARIF uploaded
     to the Security tab. ~40 documented skips in five labeled buckets
     (ephemeral demo trade-offs, cost trade-offs like no KMS CMKs, custom-domain
     dependent, CloudFront-WAF-location, accepted). The *point* of the skips
     file: every skip is a written decision with a reason, not an accident.
     Critically, the security-sensitive basics are **not** skipped: default SG
     lockdown, no public IPs, S3 versioning, WAF managed rules.
  2. **Infracost** — prices the layer-2 Terraform; on PRs, diffs against the
     base branch and **fails if the monthly increase > $10**. (Pinned CLI
     version, retry logic; skipped quietly when the API key is absent.)
  3. **Trivy** — scans the built image (`HIGH,CRITICAL, --ignore-unfixed`,
     exit 1 on hit). This gate is why the Dockerfile runs `apt-get upgrade` in
     the runtime stage: base-image CVE drift (e.g. util-linux) would otherwise
     fail CI with no change on our side.
- **Zero-downtime-ish rolling deploy**: build ARM64 image → push to ECR under
  the **git SHA tag** (PRs never push) → describe the current task def → swap
  the image, strip read-only fields, register → run the migration as a
  **one-off Fargate task** (`alembic upgrade head`, wait, check exit code) →
  `update-service` → wait stable (circuit breaker catches bad deploys and
  rolls back).
- `terraform.yml`: fmt check, `validate` on both stacks, TFLint.

### 9.3 Test strategy notes worth saying in an interview

- Backend tests use **ASGITransport** (in-process HTTP) with dependency
  overrides for DB — including overriding the *session factory* so the
  streaming endpoint's self-owned session hits the test DB, not prod.
- Tests must be backend-portable: SQLite needs `PRAGMA foreign_keys=ON`
  (off by default!) and `StaticPool`; Postgres needs `NullPool` + engine
  dispose because asyncpg connections are event-loop-bound and
  pytest-asyncio gives every test a fresh loop. These two bullets alone
  demonstrate real experience with async DB testing.
- Rate-limit and in-flight state is reset per test — leaked state 429s the
  next test.

---

## 10. Key trade-offs & ADR summaries (memorize these)

| Decision | Chose | Rejected | Why / when the other wins |
|---|---|---|---|
| Database | **RDS PostgreSQL + pgvector** | DynamoDB | Vector search + `GROUP BY` analytics + FK/transactions for token rotation; DynamoDB needs a second system for vectors and pre-planned indexes. RDS bills hourly when idle → mitigated by ephemeral apply/destroy. DynamoDB wins for a simple append-only conversation log. |
| Compute | **ECS Fargate** (Graviton) | Lambda / App Runner | Long-lived SSE streams + warm connection pool; Lambda's response-streaming limits/cold starts don't fit; container runs identically locally and on any host. Serverless wins for bursty non-streaming APIs. |
| LLM | **External DeepSeek** | Bedrock | Provider independence, OpenAI-compatible SDK, portability (app must also run outside AWS). Costs: NAT egress, API keys, data leaves AWS. Bedrock wins for compliance-sensitive AWS-committed deployments; the client module makes the swap localized. |
| Cache | **Redis optional** | in-memory only | Global limits need a shared store once autoscaling runs >1 task; in-memory is correct for single instance and is the safe degradation path. |
| Streaming | **SSE** | WebSockets | One-way streaming over plain HTTP; WS adds a protocol for nothing. Cost: custom fetch parser. |

---

## 11. Live walkthrough script ("walk me through this repo")

**The principle: don't tour folders — trace one request through the stack.**
Interviewers want a story, not a file listing. Open each file at the right
moment, say what it does in one sentence, then drop one sharp detail.

The arc (10–12 minutes): README diagram → frontend → backend → data/tests →
infra/CI → close.

### Phase 1 — Orientation (30 sec)

**Open:** `README.md` → the mermaid diagram.

> "Aether is an AI marketing assistant: React SPA, FastAPI backend with a
> streaming tool-calling agent, deployed on AWS entirely as Terraform. This
> diagram is the cloud topology — S3+CloudFront edge, ALB, ECS Fargate, RDS
> with pgvector, all private-subnet. The unusual bit: the stack is designed to
> be ephemeral — `make up` / `make down` — so idle cost is ~$0. Let me trace
> one chat message through the whole thing."

### Phase 2 — Frontend (3 min)

**Open:** `web/src/main.tsx` → `web/src/router.tsx`

> "Entry point; React 19 + TanStack Query + Zustand. Routes are lazy-loaded,
> wrapped in `ProtectedRoute` — nothing renders until auth bootstraps."
>
> **Sharp detail:** the access token lives **in memory only** (Zustand store) —
> never localStorage — so XSS can't exfiltrate it.

**Open:** `web/src/lib/api.ts`

> "Every API call goes through `apiFetch`: attaches `Authorization: Bearer`,
> sends `credentials: 'include'` for the refresh cookie, and on a 401 it
> silently refreshes once and retries."
>
> **Sharp detail:** `refreshAccessToken()` is deduplicated — a module-level
> promise so 5 parallel 401s cause exactly **one** refresh (single-flight
> pattern). And the refresh request sends `X-Requested-With` — that header is
> the CSRF defense; the server side is coming in a minute.

**Open:** `web/src/api/chat.ts`

> "The chat call. It's `fetch` + `ReadableStream` with a **manual SSE
> parser** — not `EventSource` — because we need POST, multipart file upload,
> and an Authorization header, none of which EventSource supports."
>
> **Sharp detail:** each `token` / `reasoning` / `tool_call` / `done` /
> `error` event is parsed from the stream buffer and rendered live — thinking
> trace, tool chips, markdown — and **Stop** is just
> `AbortController.abort()`, which drops the connection; the server releases
> its resources in a `finally` (shown below).

### Phase 3 — Backend (4 min) — the core of the walkthrough

**Open:** `api/app/main.py`

> "FastAPI app factory. Notice the middleware order — the body-size limiter is
> added before CORS so CORS ends up outer and decorates the 413 too. And API
> docs are disabled in production so the API surface doesn't leak publicly."
>
> **Sharp detail:** `/health` (liveness, no DB) vs `/health/ready` (runs
> `SELECT 1`) — the split keeps the load balancer from killing tasks on a
> transient DB blip.

**Open:** `api/app/api/routes/conversations.py` → `send_message` (~line 96)

> "Here's the chat endpoint. Look at the dependency chain — it **is** the
> security architecture: `get_current_user` → chat rate limit → monthly cost
> cap → attachment parsing → in-flight turn slot → then the streaming
> response."
>
> **Sharp detail:** the turn slot is claimed **after** attachment validation
> — a rejected upload can't strand a slot — and it's released in the
> generator's `finally`.

**Open:** `api/app/agent/loop.py` → `stream_agent_response` (~line 89)

> "The streaming generator. It opens its **own** DB session because FastAPI
> tears down the request's session before the stream runs — reusing it leaks
> connections idle-in-transaction, invisible on SQLite but deadlocks
> Postgres. The `finally` releases the turn slot on every exit path,
> including the client disconnecting."
>
> "Then `_run_agent`: build context → stream DeepSeek → if tool calls,
> persist them, execute each, feed results back — up to 5 iterations."
>
> **Sharp detail:** if the upstream call fails mid-stream, headers are
> already sent — so the exception becomes an explicit SSE `error` event
> instead of a truncated stream.

**Open:** `api/app/agent/memory.py` (only if they're engaged — otherwise skip)

> One line: "Context is bounded by rolling summarization — and the split
> point never lands between a tool-call message and its result, because the
> provider rejects that with a 400."

**Open:** `api/app/agent/tools.py` → `TOOL_SCHEMAS`

> "12 tools — task/note CRUD, semantic note search, weather, Tavily web
> search, Google Calendar. Handlers scope every query by `user_id` — the
> model can only touch the caller's data — and paid external tools have
> their own rate limits."

### Phase 4 — Auth (if asked, or if time allows — your strongest material)

**Open:** `api/app/services/refresh_tokens.py` → `api/app/api/deps.py`

> "Auth: 15-min JWTs + 7-day rotating refresh cookies with theft detection.
> Each refresh revokes the presented token and mints a successor in the same
> family — replaying a revoked token means it leaked, so the whole family
> burns."
>
> **Sharp detail (your best line of the walkthrough):** the revocation is a
> **conditional UPDATE** — `WHERE jti=? AND revoked=false`, `rowcount == 0`
> means replay — so the database is the arbiter and two racing refreshes
> can't both mint successors.
>
> In `deps.py`: "`/refresh` is the one cookie-authenticated endpoint, so it
> requires `X-Requested-With` — a header outside the CORS safelist. Sending
> it forces a preflight only our own origin can pass, which is what makes it
> a CSRF defense."

### Phase 5 — Data + tests (2 min)

**Open:** `api/app/models/note.py` → `api/app/db/session.py`

> "SQLAlchemy 2.0 typed models. Note the pgvector column with a JSON variant
> on SQLite — the whole test suite runs keyless on SQLite, and the same suite
> runs on real Postgres in CI."
>
> `session.py`: "Connection pooling tuned so N Fargate tasks fit under RDS
> max_connections, `pool_pre_ping` survives failovers, and SQLite gets
> `PRAGMA foreign_keys=ON` because it's off by default."

**Open:** `api/tests/conftest.py` — one line:

> "Tests run in-process over ASGITransport with dependency overrides —
> including the session factory, so the streaming endpoint's own session hits
> the test DB."

### Phase 6 — Infra + CI (2 min)

**Open:** `infra/terraform/` — show the two layers side by side

> "Two Terraform stacks: layer 1 is persistent and near-free — ECR,
> S3+CloudFront, Secrets Manager, budget alert. Layer 2 is hourly-billed —
> VPC, ALB, ECS, RDS, Redis — and gets destroyed after each demo. That's the
> whole cost model."
>
> **Sharp detail:** secrets are injected via ECS `valueFrom` with a
> least-privilege execution role scoped to exact secret ARNs — and the task
> role is **empty by design**, because the app calls no AWS APIs.

**Open:** `.github/workflows/deploy.yml`

> "CI has three gates before any deploy: Checkov for IaC, Infracost which
> fails PRs that raise the bill over $10/mo, Trivy for container CVEs.
> Deploys run migrations as a one-off Fargate task first, then flip the
> service — with ECS circuit-breaker auto-rollback as the safety net."

### Phase 7 — Close (30 sec)

> "The two things I'm proudest of: the auth system — rotation with reuse
> detection is something most apps get wrong — and the spend-guard design,
> where rate limits, cost caps, and the in-flight ceiling are one system
> rather than three features."

### The "one sharp detail per file" cheat sheet

| File | One-liner | The detail that makes you sound senior |
|---|---|---|
| `web/src/lib/api.ts` | typed fetch client, 401→refresh→retry | single-flight refresh promise |
| `web/src/api/chat.ts` | SSE over fetch | manual parser because EventSource can't POST/multipart/auth; Stop = abort |
| `web/src/store/auth.ts` | token in memory only | XSS can't read memory the way it reads localStorage |
| `api/app/main.py` | app factory | middleware order (413 gets CORS headers); docs off in prod; liveness vs readiness |
| `api/app/api/routes/conversations.py` | chat endpoint | the dependency chain *is* the guard architecture |
| `api/app/agent/loop.py` | the agent loop | own-session-per-stream; errors become SSE events; slot released in `finally` |
| `api/app/agent/tools.py` | 12 tool schemas + handlers | `user_id` scoping; rate limits on paid tools |
| `api/app/services/refresh_tokens.py` | rotation + reuse detection | conditional UPDATE makes the DB the arbiter |
| `api/app/api/deps.py` | auth dependencies | CSRF header = forced preflight; only presence checked |
| `api/app/models/note.py` | pgvector column | SQLite JSON variant keeps tests keyless |
| `api/app/db/session.py` | engine + pool | pool sized to RDS max_connections; FK pragma |
| `infra/terraform/layer2_ephemeral/` | hourly-billed stack | `high_availability` boolean reshapes the whole stack |
| `.github/workflows/deploy.yml` | gated deploy | OIDC, Checkov/Infracost/Trivy, migrate-as-one-off-task |

**Practice tip:** run the walkthrough 3 times out loud. By the third run you
won't be reading the notes — you'll be telling the story, and the "sharp
details" come out naturally at the right moments.

---

## 12. Mock interview — questions & model answers

*(Q&A below — use for self-quizzing; the answers are condensed, say them in
your own words.)*

### Screening level (know cold)

**Q1. Walk me through the architecture of this project in 2 minutes.**
> Start at the user: React SPA on S3 behind CloudFront (OAC + WAF) → API calls
> to an ALB (ACM, HTTPS, WAF) → FastAPI on ECS Fargate in private subnets →
> RDS PostgreSQL + pgvector for relational + vector, Redis for shared
> rate-limiting state → egress to DeepSeek/Tavily/Google via NAT. Chat is an
> agent loop: the API streams SSE to the browser while the loop calls the LLM
> with tool schemas and executes tools against the DB. Auth is short-lived
> JWTs + rotating HttpOnly refresh cookies with reuse detection. Infra is
> Terraform in two layers so the whole thing tears down to ~$0.

**Q2. What's the tech stack and why did you pick it?**
> Python 3.12 / FastAPI / async SQLAlchemy 2.0 / Postgres on the backend —
> async end-to-end because the workload is I/O-bound (LLM streaming, DB, HTTP
> to third parties). React 19 + TypeScript + Vite + TanStack Query on the
> front. Terraform on AWS. Reasons for the big calls are the ADRs: Postgres
> over DynamoDB because one store does relational + vectors + transactions;
> Fargate over Lambda because SSE streams and connection pools; DeepSeek over
> Bedrock for provider independence and portability.

### Auth deep-dives (most likely follow-ups)

**Q3. How does your auth work end to end?**
> Login returns a 15-minute JWT access token in the body and sets a 7-day
> refresh token as an HttpOnly cookie scoped to `/api/v1/auth`. The SPA holds
> the access token in memory only. On 401 it calls `/refresh` with
> credentials: include — that endpoint rotates the refresh token: revokes the
> presented one and mints a successor in the same family. Replaying a revoked
> token revokes the whole family. Logout and password reset revoke all tokens
> and bump `token_version`, which invalidates even unexpired access tokens.

**Q4. Why rotation + reuse detection instead of a stateless refresh token?**
> A stateless refresh token can't be revoked server-side without a store.
> Rotation gives single-use tokens and — crucially — theft detection: if both
> the attacker and the user hold a copy, one of them replays a revoked token,
> and that event revokes the whole family. The trade-off is a DB table and a
> write per refresh.

**Q5. What race conditions exist in token rotation and how did you handle it?**
> Two concurrent refreshes presenting the same token could both pass a
> read-then-write check. I made the revocation a conditional UPDATE —
> `UPDATE refresh_tokens SET revoked=true WHERE jti=? AND revoked=false` — and
> treated `rowcount == 0` as replay. The database becomes the arbiter; only
> one request can move the row from live to revoked.

**Q6. Why does `/refresh` require a custom header? What's the threat?**
> CSRF. The refresh token is in a cookie, and cookies ride along on cross-site
> POSTs (especially since the SPA and API are on different domains, forcing
> SameSite=none). The `X-Requested-With` header is outside the CORS safelist,
> so a cross-site request that tries to send it becomes a preflight only our
> own origin can pass. Bearer-header endpoints don't need this — an attacker
> can't read the token to forge the header.

**Q7. How do you prevent account enumeration?**
> Timing-equalized login (dummy bcrypt verify for nonexistent accounts) and
> identical error messages. Plus per-IP rate limiting on login/register.

**Q8. Why is the access token in memory and not localStorage?**
> localStorage is readable by any JS running on the origin, so XSS exfiltrates
> it. Memory storage means XSS can't read the token — though it could still
> make authenticated requests from within the page; that's a residual risk we
> accept. The refresh cookie is HttpOnly for the same reason.

### LLM/agent deep-dives

**Q9. Explain the agent loop. What bounds it?**
> Build context (system prompt + summary + recent history) → stream the LLM
> with tool schemas → if it emits tool calls, persist them, execute each
> handler, persist results as tool messages, feed back, repeat — up to 5
> iterations. Bounds: max iterations (runaway tool loops), message length,
> attachment size, per-user concurrency, per-minute rate, monthly cost cap.

**Q10. How do you keep the context window from growing unbounded?**
> Rolling summarization: past ~24k chars of unsummarized history, the oldest
> messages are folded into a stored summary by a separate LLM call, keeping
> the last 10 verbatim. The split never lands between a tool-call message and
> its results, because the API rejects a tool message with no preceding call.

**Q11. How do you stop users from costing you money?**
> Three layers: sliding-window rate limit (per minute), a monthly cost cap
> computed from logged token usage × configured prices, and a per-user
> in-flight turn ceiling. The last exists because usage is only logged when a
> turn finishes — N parallel requests would otherwise all pass the same
> pre-turn check. Each layer bounds a different axis: rate, aggregate spend,
> and the parallel overshoot of the spend check.

**Q12. How do you rate-limit across multiple API instances?**
> The limiter has two backends behind one API: in-memory deques per process,
> or Redis sorted sets (one per key, scored by timestamp) when `REDIS_URL` is
> set. Redis is required for limits to hold once autoscaling runs more than
> one task. If Redis is down, it logs and falls back to per-instance limiting
> rather than failing requests.

**Q13. How does semantic note search work, and what happens without Postgres or an OpenAI key?**
> Notes are embedded whole with text-embedding-3-small and stored in a
> pgvector column; a query is embedded and ranked by cosine distance with a
> relevance floor. Without Postgres or the key it degrades to a tokenized,
> stopword-filtered keyword scan — the feature still works, just less smart.

**Q14. How do you know your RAG works?**
> A reproducible eval harness driving the real retriever: faithfulness,
> context precision, answer relevancy (RAGAS definitions re-implemented) plus
> retrieval recall, against a golden dataset. It runs keyless and offline in
> CI as a regression gate; configs are stamped into reports so sweeps are
> comparable, and per-sample token/cost/latency makes score-vs-cost trades
> visible.

**Q15. What prompt-injection defenses do you have?**
> Defense in depth with honest limits: system-prompt rules (tool/web/note/
> attachment content is data, not instructions; confirm unsolicited
> destructive actions), input fencing for attachments (sanitized filename,
> fence tags, strip close-fence lookalikes), and a deterministic output
> redactor for vendor-name leakage. I'd be clear these are mitigations, not a
> guarantee — prompt-injection isn't fully solvable at the prompt layer.

### Backend engineering

**Q16. Why does the streaming endpoint open its own DB session?**
> FastAPI tears down yield dependencies before the streaming body runs. The
> generator outlives the request session, so it opens and owns its own
> session for the lifetime of the stream — closed on completion or client
> disconnect. Reusing a closed session leaks connections idle-in-transaction,
> which deadlocks Postgres.

**Q17. What happens when the user clicks Stop mid-stream?**
> The client aborts the fetch; Starlette calls `aclose()` on the generator;
> the `finally` releases the user's in-flight turn slot and closes the DB
> session. The partial assistant message may or may not be committed depending
> on where the abort landed — the client shows a neutral "wasn't saved" notice.

**Q18. How does the analytics dashboard stay cheap?**
> Aggregation happens in the database — `GROUP BY` UTC date in SQL — instead
> of streaming rows into Python. Dialect-specific date truncation (Postgres
> timestamptz pinned to UTC vs SQLite strftime). The window on the charts and
> the tool breakdown share the same `days` bound; lifetime totals are labeled
> separately.

### AWS / infrastructure

**Q-AWS1. Why is your Terraform split into two layers?**
> The persistent layer holds near-free, slow-to-recreate things — ECR, the S3
> bucket + CloudFront, Secrets Manager, certs, budget alert — and survives
> teardown. The ephemeral layer holds everything hourly-billed — VPC, NAT,
> ALB, ECS, RDS, Redis. The demo model is `make up` → demo → `make down`, so
> idle cost is ~$0. `verify-clean` catches orphaned EIPs/ENIs that would bill
> silently after a destroy.

**Q-AWS2. Walk me through how a request reaches your API.**
> HTTPS to the ALB (ACM cert, WAF inspecting, TLS 1.2+ policy, HTTP→HTTPS
> redirect), ALB forwards to Fargate tasks in private app subnets (SG chain:
> ALB SG → API SG). The task read its secrets from Secrets Manager via
> `valueFrom` at startup and pulled its image from ECR through PrivateLink
> endpoints. It queries RDS (relational + pgvector) and Redis (if HA) in
> private DB subnets. Outbound LLM calls egress through NAT. Logs/metrics
> flow to CloudWatch.

**Q-AWS3. How do you handle secrets?**
> Never in the image or env files — Secrets Manager, injected via ECS
> `valueFrom` (whole-secret for `DATABASE_URL`, ARN + `json-key::` for the
> rest). Two least-privilege IAM roles: the execution role can read only the
> exact secret ARNs; the task role is empty by default because the app calls
> no AWS APIs. GitHub Actions authenticates via OIDC, so there are no static
> AWS keys anywhere in the pipeline.

**Q-AWS4. What happens when a deploy is bad?**
> Two safety nets: ECS deployment circuit breaker with auto-rollback — if the
> new revision repeatedly fails health checks, ECS rolls back; and the deploy
> pipeline runs migrations as a one-off task *before* flipping the service,
> so a broken migration never gets mixed with a new code version. Plus three
> pre-deploy gates: Checkov, Infracost, Trivy.

**Q-AWS5. How do you keep costs down in this architecture?**
> Four answers: (1) ephemeral apply/destroy — RDS/NAT/ALB don't bill when the
> demo's down; (2) the `high_availability` toggle keeps the demo single-AZ,
> one NAT, one task, no Redis; (3) Infracost in CI fails PRs that raise the
> monthly bill >$10; (4) app-level spend guards (rate limits, cost cap) and
> AWS Budgets + a runaway-LLM-cost alarm bound the *usage* side.

**Q-AWS6. Why Postgres with pgvector instead of a dedicated vector DB?**
> One system of record: the same tables hold relational data and embeddings,
> so a note search is one SQL query with cosine distance, transactions keep
> consistency, and there's no second service to run, sync, or pay for.
> Specialized vector DBs win at millions-of-vectors scale; for a personal
> assistant's note corpus, RDS is plenty and removes a moving part.

**Q-AWS7. What trade-offs did you consciously accept?**
> Documented, not hidden: single NAT in demo (SPOF), no CloudTrail/GuardDuty/
> Flow Logs yet (in the roadmap), KMS default keys instead of CMKs, CloudFront
> without its own WAF, secrets rotation pending, ALB DNS changes per deploy
> (needs a custom domain for a stable URL). Each Checkov skip is filed under a
> labeled bucket in `.checkov.yaml` — the skips file is the trade-off ledger.

### Behavioral / judgment

**Q19. What would you do differently at real production scale?**
> Named in the repo's own roadmap: CloudTrail, VPC Flow Logs, GuardDuty,
> access logs, secrets rotation, KMS, cross-region DR, WAF scoped to
> CloudFront. I'd also move from ephemeral to always-on with autoscaling
> already defined via `high_availability=true`, add a proper LLM gateway with
> retries/circuit breakers, and separate the eval harness into scheduled jobs.

**Q20. A reviewer says "why not Lambda + DynamoDB like every reference chatbot?"**
> Cite ADR-0001/0002: our data is relational + vector (one Postgres does
> both), the analytics are SQL aggregations, and token rotation needs
> transactions; the API streams SSE long-lives with a warm pool — Fargate fits,
> Lambda's response streaming and cold starts don't. And the ephemeral
> apply/destroy model removes RDS's idle-cost disadvantage, which was the main
> argument for serverless.

---

## 13. Red flags to check before approving an AI suggestion

Quick checklist when an AI proposes a change to this repo:

1. **Does it touch token rotation?** Verify the conditional-UPDATE claim still
   exists — any read-then-write "simplification" reintroduces the race.
2. **Does it add an endpoint that reads cookies?** It needs the CSRF header
   dependency (and rate limiting *before* it).
3. **Does it change the streaming generator?** Slot release and session close
   must stay in `finally`/`aclose` paths; no new early `return` without them.
4. **Does it touch secrets/config?** `ENVIRONMENT=production` gates real
   secrets — check the validator still fires; nothing secret in the image
   (Dockerfile two-stage build, Secrets Manager `valueFrom`).
5. **Does it add a new LLM call?** It must log to `UsageLog` (cost cap +
   analytics depend on it) and think about rate limiting.
6. **Does it change rate limit / cost cap / inflight interplay?** They're a
   system, not three features — a "simpler" cap without the concurrency
   ceiling silently doesn't enforce.
7. **Does it add a body parameter or file upload?** Check against
   `MAX_REQUEST_BYTES` (1 MB ceiling) and the WAF body-size rule.
8. **Does it touch SQLite-vs-Postgres differences?** Native enum off, FK
   pragma, pgvector JSON variant, naive vs tz-aware datetimes — the test suite
   runs on both; a Postgres-only construct will fail the SQLite leg.
9. **Does it touch `X-Forwarded-For` / proxy logic?** `TRUSTED_PROXY_HOPS`
   must match the actual edge topology or rate limiting becomes spoofable.

---

## 14. One layer deeper — subsystem deep dives

The previous sections tell you what everything is. This section is the
**second layer**: the exact mechanics, the edge cases, what breaks if a piece
changes, and the questions an interviewer asks when they *know* the repo well.

### 14.1 Auth — the details beyond the happy path

**Password reset actually ends sessions** (`scripts/reset_password.py`):
resetting a password is the recovery path for a *possibly compromised*
account, so replacing the hash is only half the job. `revoke_all_for_user`
revokes every refresh token **and bumps `token_version`** in the same commit
as the new hash — without the bump, a stolen refresh token survives the reset
and rotates itself indefinitely, and the one action the user believes locks
the intruder out leaves them logged in.

**The bcrypt 72-byte ceiling was a real 500 bug** (`schemas/user.py:18`): the
schema originally advertised `max_length=128` — fine for ASCII, but a
password-manager passphrase with accented/emoji characters is >72 *bytes*
while ≤128 *chars*, and bcrypt 5.x raises instead of truncating. Registration
500'd. Now: the schema validates **bytes** (`password_exceeds_max_bytes`), the
login path returns False with a dummy compare instead of raising, and the
error message tells the user why. The 72-byte limit is part of the credential
contract, not an implementation detail.

**OAuth state token = CSRF defense on an unauthenticated endpoint**
(`security.py:95-107`): the Google callback is a browser redirect with no
auth header, so the `state` param is a signed JWT (`type: oauth_state`, 5-min
expiry) binding the callback to the user who initiated the flow. An attacker
can't forge it without `SECRET_KEY`, so they can't plant *their* Google
account on *your* Aether account (login-CSRF).

**Logout semantics**: revokes all refresh tokens + bumps version + clears the
cookie. Logout is a Bearer-authenticated endpoint, so it doesn't need the
CSRF header.

**Deeper Qs**: "What happens if a user changes their password while another
device is logged in?" (old access tokens die within 15 min, refresh fails
with `token_version` mismatch, everything else revoked instantly). "Why is
the OAuth state a JWT and not a random string in the DB?" (stateless, no
session table, expiry built in — but note it *does* share SECRET_KEY, so a
key compromise forges states).

### 14.2 The LLM client & streaming protocol

**Timeout design** (`agent/client.py`): the OpenAI SDK default is a **600 s**
timeout — a hung DeepSeek socket would pin a worker for ten minutes. The
client uses 5 s connect / 60 s read with `max_retries=2` (SDK exponential
backoff for transient 429/5xx). The 60 s read is the interesting one: it
allows slow token generation but bounds a silent stall.

**Thinking-mode requirements are vendor-specific** (`loop.py:53-57`):
deepseek-v4-flash runs with `thinking: enabled` and *requires*
`reasoning_content` to be echoed back for prior assistant turns — omit it and
the API returns 400. That's why `reasoning_content` is a persisted column and
why `_message_to_api` re-attaches it. The summarization call disables
thinking (it just needs prose).

**Usage arrives on the last chunk**: `stream_options={"include_usage": True}`
means token counts are only known when the stream ends — which is exactly why
the cost cap (§14.5) can't be a post-turn check and needs the in-flight
ceiling.

**What breaks if**: someone "cleans up" the `reasoning_content` echo-back →
every multi-turn conversation 400s. Someone raises the read timeout
generously → hung sockets pin the event loop. Someone removes `max_retries`
→ provider blips become user-facing errors.

### 14.3 Streaming & session lifecycle — the disconnect matrix

Walk every exit path of `stream_agent_response`:

| Exit path | What the client sees | What the server does |
|---|---|---|
| Normal completion | `done` event | commit assistant msg + usage, release slot, close session |
| Provider error mid-stream | `error` SSE event (headers already sent) | log, release slot, close session |
| Client clicks Stop | `AbortError` (fetch abort) | Starlette calls `aclose()` → `finally` releases slot + closes session; partial writes: user msg committed, assistant msg not (only committed at turn end) |
| Client disconnects (network) | nothing | same as Stop |
| Conversation deleted mid-request | `error` event ("no longer exists") | clean return, no orphan writes |

**The subtle one**: the user message is committed *before* the first token, so
a stopped reply leaves a user message with no assistant reply — the frontend
refetches after every turn (`finally` in `handleSend`) and reconciles by
showing "You stopped this reply… it wasn't saved" (`ChatPage.tsx:489-494`).

**Deeper Qs**: "What happens to the database session if the client disconnects
mid-tool-call?" (the tool result may or may not be committed depending on
where the abort lands — the design accepts this: no partial *assistant*
message, clean slot release, next turn is consistent). "Why not a background
task that survives disconnect?" (you'd pay for tokens nobody sees and queue
completions against a stale conversation — dropping is the right call for a
chat app).

### 14.4 Rate limiting — the exact Redis mechanics

**Sorted-set pipeline, one round trip** (`rate_limit.py:110-133`): per key
`rl:chat:{uid}` etc., members are `"{now_ms}-{uuid}"` (unique per request,
scored by wall-clock ms). One MULTI/EXEC: `ZREMRANGEBYSCORE` (drop
out-of-window) → `ZADD` → `ZCARD` → `PEXPIRE` (key TTL = window, so idle keys
vanish). Over the limit: **remove your own member** (a rejected request must
not penalize the next caller) and compute `Retry-After` from the oldest
remaining entry.

**Proxy-hop math** (`_client_ip`): `X-Forwarded-For` is *appended* by each
proxy, so the caller controls everything left of what your own edge added.
Only the last `TRUSTED_PROXY_HOPS` entries were written by your infra; the
first of those is the real client. A chain shorter than the hop count means
the request didn't come through the expected path → fall back to the socket
peer. Get the hop count wrong and either you trust spoofable input (hop
count too high) or you rate-limit the ALB's IP (too low → everyone shares
one bucket).

**Memory hygiene**: the in-memory deques are swept every 300 s — idle keys
whose window is fully expired are deleted, so a long-lived process doesn't
leak one deque per user/IP forever.

**Deeper Qs**: "Why member uniqueness via uuid and not a counter?" (sorted
sets need unique members; same-ms collisions would silently merge requests).
"What's the failure mode when Redis is down *and* back up again?" (fallback
per-instance for the outage, global resumes automatically — limits were
looser during the blip, which is the documented availability trade-off).

### 14.5 Cost cap & in-flight ceiling — why the third layer exists

The chain of reasoning (from `inflight.py` docstring, verbatim-worthy):

1. Rate limits cap requests/minute — nothing caps *spend per month*.
2. The cost cap reads month-to-date spend **before** the turn; but a turn's
   tokens land in `UsageLog` only when it **finishes**.
3. So N simultaneous requests all read the same total, all pass, all run.
   Opening tabs was enough to overshoot the cap by a multiple.
4. The in-flight ceiling (default 2) bounds the overshoot to ~2 turns — same
   order as the cap's documented pre-turn tolerance.

**Redis slot mechanics**: `INCR` + `EXPIRE` refreshed on every claim (the
window that matters is "since the last turn started", not "since the first").
Over the limit: `DECR` to undo the claim. Release `DECR`s and clamps at 0 —
a stray release must never push the counter negative, or the next TTL window
hands out extra slots. TTL 600 s reclaims slots leaked by a killed process.

**The cap is a UTC calendar month**, priced by the *configured* per-1M rates
— accuracy follows directly from those numbers being current. Cap ≤ 0
disables it (self-hosters paying their own bill).

**Deeper Qs**: "What's the worst-case overshoot in dollars?" (≈
`CHAT_MAX_CONCURRENT_TURNS` × the biggest single turn — bounded by
`MAX_MESSAGE_CHARS`/attachment limits on input and the model's own output
limits). "Why pre-turn and not post-turn?" (post-turn means refunding or
charging overage; pre-turn + bounded parallelism keeps the error surface to
one 429).

### 14.6 RAG eval — the metric math and the bugs it actually caught

**Faithfulness**: extract atomic claims from the answer (one judge call
prompt), then one entailment check per claim against the retrieved context.
Score = supported/total. Undefined (→ excluded, not zero) when the answer has
no extractable claims.

**Context precision** (`metrics.py:49-73`): rank-weighted average precision —
`sum(Precision@k at ranks holding relevant items) / total_relevant`. Rewards
ranking useful notes *first*; nothing retrieved → undefined; retrieved but
none useful → 0.0.

**Answer relevancy**: reverse-generate N questions from the answer (one judge
call that *also* returns a noncommittal verdict — RAGAS defines it that way;
asking twice pays double and lets the two replies disagree), embed them, mean
cosine vs the question embedding. Noncommittal ("the notes don't say") → 0:
maximally faithful but not *relevant*.

**The failure-mode log is the artifact that proves the harness works**
(`eval/FAILURE_MODES.md`):

- **FM-1**: first run, recall 0.00 — the keyword fallback matched the *entire
  query* as one `LIKE` pattern, so a natural-language sentence never matched
  any note. Fixed by tokenization + stopwords; recall 0.00 → 1.00, guarded by
  `tests/test_rag.py`.
- **FM-2**: token-OR matching over-retrieves (context precision ~0.72, not
  1.0) — **accepted by design**: the keyword path is a degraded fallback; the
  semantic path's distance floor is the real precision control.
- **FM-3**: unanswerable samples (flight *time*, Wi-Fi *password*) — retrieval
  returns topically-adjacent notes missing the specific fact; faithfulness on
  these samples is the hallucination tripwire. The offline backend *cannot*
  detect this (extractive generator copies context text → trivially "supported") — documented so offline numbers aren't misread.
- **FM-4**: offline answer-relevancy ~0.35 is a measurement artifact
  (bag-of-words cosine), not a defect.

**Attribution under concurrency** (`core/usage.py`): a `contextvars`-based
meter — tasks spawned inside a meter inherit it, siblings don't bleed into
each other, recording outside any meter is a no-op. The eval harness opens one
meter per sample so its dozen+ judge calls attribute to the right row even
under `asyncio.gather`.

**Deeper Qs**: "Why re-implement RAGAS instead of importing it?" (drops the
LangChain/dataset tree, formulas are defensible line-by-line in review, and
swapping the real package later consumes the same row shape). "Why is the
CI gate offline instead of running the LLM backend?" (deterministic, keyless,
no cost — floors tuned to the offline backend; the README says to add an
LLM profile once real numbers are wired in).

### 14.7 Embeddings & semantic search — the degradation ladder

Every layer degrades one rung at a time, in order (`note_search.py`,
`embeddings.py`):

```
Postgres + key  →  cosine distance + relevance floor (0.6)        ← production
Postgres, no key → keyword scan (embeddings disabled)
SQLite + key     → keyword scan (no pgvector type)
SQLite, no key   → keyword scan                                   ← CI/dev
semantic finds nothing → keyword scan (floor dropped everything)
```

A transient embedding *failure* never blocks the note write — it logs a
warning and skips the vector (search falls back). The warning matters: a dead
`OPENAI_API_KEY` would otherwise silently disable semantic search forever.

**Deeper Qs**: "Why no pgvector index (IVFFlat/HNSW)?" (a personal note
corpus is thousands of rows — a sequential scan is microseconds and indexes
add write overhead + tuning; the answer is 'scale doesn't justify it yet').
"Why embed the whole note and not chunks?" (a note is the retrieval unit in
this product; chunking belongs in `EvalConfig` if it ever arrives).

### 14.8 Frontend — the streaming state machine (`ChatPage.tsx`)

The interesting engineering is **state scoping**: every piece of ephemeral
state is keyed by conversation id so nothing leaks across chats:

- `drafts[convId]`, `attachments[convId]` — switching chats never carries a
  draft (or worse, an attachment) into the wrong conversation; coming back
  finds what you were writing.
- `streamingFor` — which conversation the in-flight turn belongs to; the
  optimistic user bubble + live assistant bubble render *only* there. A turn
  running in another chat shows a notice instead of a dead button ("Aether is
  replying in another chat…" — the client tracks one stream though the server
  allows 2 concurrent turns).
- `notice` carries its conversation id — a failed turn in one chat doesn't
  surface over the next chat you open.
- `pinned` scroll state per id; scroll-pin threshold 100 px so a trackpad
  nudge doesn't detach the view; jumping up to re-read reveals a
  "jump to latest" button.
- Persona pick is **optimistic with delayed clear**: the highlight is held
  until the refetch lands, so the picker doesn't flash back to the old
  persona mid-flight.

**Stop vs error paths** (`handleSend` catch): `signal.aborted` → neutral
"stopped" notice, nothing restored (the server persisted the user message
before the first token). Real error → red banner **and the draft is restored**
into the chat it was written for — and never over something typed there since
(`current || content`).

**IME-safe Enter** (`handleKeyDown`): Enter fires send only when
`!event.nativeEvent.isComposing` — otherwise CJK candidate selection would
send half-composed messages.

**Deeper Qs**: "Why optimistic UI for the persona but not for messages?"
(messages come back from the server as the source of truth via the refetch;
persona is a tiny field whose round-trip latency would visibly lag the UI).
"Why `Record<number, File>` for attachments in memory and not a store?"
(File objects aren't serializable to Zustand/localStorage, and the draft
belongs to a single screen's lifecycle).

### 14.9 Deploy pipeline — the exact mechanics (`deploy.yml`)

**The gate order matters**: Checkov (SARIF → Security tab, `soft_fail:
false`, every skip documented in `.checkov.yaml`) → Infracost (baseline at
the PR's base SHA, diff vs head, `diffTotalMonthlyCost > $10` fails, comment
updated in place) → Trivy (`HIGH,CRITICAL`, `--ignore-unfixed`, exit 1). The
image is **built and scanned on every PR but pushed only on the deploy path**
under the git-SHA tag — PRs never write to ECR.

**Zero-downtime-ish rolling update**: describe the current task def → `jq`
swap `containerDefinitions[0].image`, strip the read-only fields
(`taskDefinitionArn`, `revision`, `status`, `requiresAttributes`, …) →
register → **migrations as a one-off task** reusing the *service's own*
network config (subnets/SGs pulled from `describe-services`, no public IP) →
`wait tasks-stopped` + assert exit code 0 → `update-service` → `wait
services-stable`. The ECS circuit breaker auto-rolls back if the new revision
keeps failing health checks.

**Two war stories baked into the workflow comments**: the Infracost action's
semver-*range* default (`0.10.x`) pages through GitHub's releases list — it
twice reddened the gate (504, then API timeout) — so the version is pinned
exact (`0.10.45`) with one retry. And Trivy is installed directly from the
release script rather than a version-tagged action, to avoid action drift.
arm64 image on an amd64 runner needs QEMU (`setup-qemu-action`).

**Idempotent-by-default**: the deploy steps activate only once
`vars.AWS_DEPLOY_ROLE_ARN` is set — the workflow stays green on main until
OIDC is wired up, so a fresh fork doesn't turn red. OIDC (`id-token: write`)
means no static AWS keys anywhere.

**Deeper Qs**: "Why migrate in a one-off task instead of on container start?"
(runs exactly once, fails the deploy *before* the service flips, and
concurrent task starts can't race the migration). "What if the migration
task crashes without exiting?" (`wait tasks-stopped` would hang — the CI
timeout is the backstop; in practice the container exits on crash and the
exit-code check catches it). "Why Infracost as a merge-blocking gate instead
of an advisory comment?" (for a portfolio repo the gate *is* the feature —
it demonstrates cost governance; the platform version with PR reporting is a
separate showcase).

### 14.10 Dockerfile & config — the small decisions with big consequences

- **Two-stage build**: wheels compiled in the builder (compilers stay out of
  the final image), installed `--no-index --find-links=/wheels` in the
  runtime stage. `requirements-dev.txt` (pytest/ruff/aiosqlite) never enters
  the image.
- **`apt-get upgrade` in the runtime stage is deliberate**: the Trivy gate
  fails on CVEs in packages the app never asked for (util-linux, most
  recently) as upstream fixes outrun the `python:3.12-slim` tag. Pinning a
  digest would make the drift *visible* but not *fixed*; upgrading resolves
  it, at the cost of the layer not being byte-reproducible. This is a real
  supply-chain trade-off, spelled out in the Dockerfile comment.
- **Non-root user** (uid 10001) — Fargate and reviewers expect it.
- **Config validation as a safety net** (`config.py:156-163`): the settings
  model **refuses to boot in production** with the dev placeholder
  `SECRET_KEY` or an empty `ENCRYPTION_KEY`. Local dev/CI stay permissive;
  prod can't accidentally ship without secrets.
- **The asyncpg driver rewrite** (`config.py:23-33`): managed-Postgres
  providers hand out `postgres://` URLs; a field validator rewrites them to
  `postgresql+asyncpg://` so Render/Railway URLs work unedited — a small
  validator that prevents a classic "works locally, fails on Render" bug.

**Deeper Qs**: "Why not multi-stage with a pinned digest?" (digest pinning
makes CVE drift visible but doesn't fix it; the upgrade strategy trades
reproducibility for a passing security gate — say both halves). "Why Fernet
and not KMS/SSM for the Google tokens?" (portable across providers — the
same code runs on Render/Railway/any Docker host; KMS would be the AWS-native
upgrade).
