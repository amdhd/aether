# Pre-Deploy Security & Reliability Review

> Scope: full repo (`api/`, `web/`, `infra/terraform/`, deploy configs) against the
> requested checklist — Redis/caching, transactions, concurrency, rate limiting,
> idempotency, API security. Every finding below was verified against the code
> at the referenced line, not inferred. Test suite state at review time:
> **179 passed, 3 skipped**.
>
> Companion to [code-review-findings.md](code-review-findings.md), which covers
> correctness bugs. This document covers deploy-readiness. Overlap is noted where
> it exists.

---

## Status table

| # | Checklist item | Status | Evidence |
|---|---|---|---|
| **6** | Cache invalidated on writes | 🔍 N/A | Only cache is `_weather_cache` (`agent/tools.py:376`); Redis is used for rate limits + concurrency slots only, never as a data cache. No cached user data to invalidate. |
| 6 | No sensitive data cached / TTL present | ✅ Good | Weather is public data, 1 h TTL (`tools.py:21,382`). Redis holds only request timestamps and an integer counter, both TTL'd (`rate_limit.py:119`, `inflight.py:54`). |
| 6 | Stampede / thundering-herd protection | ⚠️ Partial | `_fetch_weather_data` (`tools.py:379-392`) has no single-flight lock; N concurrent misses = N upstream fetches. Free public API, so cost impact is nil. |
| 6 | Redis pooling + error handling | ⚠️ Partial | Pool is redis-py's default (fine) and failures fall back to in-memory (`rate_limit.py:174-176`). **But no socket timeouts** (`rate_limit.py:106-108`) — a hung node blocks, and `except Exception` catches errors, not hangs. |
| **7** | Multi-step ops in transactions | ⚠️ Partial | No `db.begin()` block anywhere in the codebase. Two real split-transaction paths: refresh rotation (**T1**) and the agent turn (**T2**). |
| 7 | Proper isolation levels | ✅ Good | Postgres default READ COMMITTED, correct for this workload. The one read-then-act check (cost cap) is documented as approximate and bounded by the in-flight ceiling (`cost_cap.py:9-18`). |
| 7 | Deadlock handling / retry logic | ❌ Missing | No retry on serialization failure, no `IntegrityError` handling anywhere. Low real risk (no lock-ordering hazards found), but `register` 500s on a concurrent duplicate email — see B2 in code-review-findings.md. |
| 7 | Optimistic / pessimistic locking | ✅ Good | Where it actually matters — refresh-token claim — a conditional `UPDATE ... WHERE revoked = false` makes the DB the arbiter (`services/refresh_tokens.py:103-111`). Correct pattern, well reasoned. |
| **8** | Shared state protected | ⚠️ Partial | Single-threaded asyncio makes the in-memory maps safe. **C1**: in-flight slots leak when Redis flaps between acquire and release. |
| 8 | Idempotent ops where retries happen | ❌ Missing | See section 10. |
| 8 | No races in counters / status transitions | ⚠️ Partial | No inventory or payments. `token_version += 1` (`refresh_tokens.py:123`) is a Python read-modify-write — lost update possible, benign outcome. |
| **9** | Rate limiting on auth / sensitive / public endpoints | ⚠️ Partial | Auth ✅ (10/min/IP, `auth.py:54,72,103`), chat ✅ (20/min + cost cap), agent tools ✅ (`tools.py:441,499`). **All authenticated CRUD is unlimited** — and `POST/PUT /notes` bills OpenAI on every call (**R1**). |
| 9 | Progressive delays / lockout | ❌ Missing | Flat per-IP window only. No per-account counter, no lockout, no backoff. Rotating IPs ⇒ unlimited attempts against one account. |
| 9 | Enumeration protection | ⚠️ Partial | Login is timing-equalized against a dummy hash — genuinely well done (`core/security.py:40-50`). **But `POST /register` returns 400 "Email already registered"** (`auth.py:57-59`), a direct existence oracle. |
| 9 | API cost abuse (< $2 / action) | ⚠️ Partial | Chat is well bounded: 16 k-char message cap × ≤5 tool iterations ≈ **< $0.05/turn**, plus a $10/month/user cap and a 2-concurrent-turn ceiling. ✅ **Embeddings are outside that cap entirely** (**R1**). |
| **10** | Idempotency keys on critical writes | ❌ Missing | No `Idempotency-Key` handling anywhere in the repo (verified by grep). |
| 10 | Safe retries without side effects | ❌ Missing | Retrying `POST /conversations/{id}/messages` re-bills a full LLM turn and duplicates the user message; retrying `calendar_create_event` duplicates a real calendar event. |
| **11** | Input validation & sanitization | ⚠️ Partial | Pydantic on every JSON body, bounded `Query` params, 16 k message cap, 1 MB ASGI body gate (`core/body_limit.py`) — strong. Gaps: unbounded `Note.content`/`tags`/`Task.description`, and **LLM tool arguments are unvalidated** (**V1**). |
| 11 | Output encoding / injection prevention | ⚠️ Partial | SQL ✅ 100% parameterized ORM, only raw SQL is `text("SELECT 1")`. XSS ✅ `react-markdown` without `rehype-raw`, zero `dangerouslySetInnerHTML`. Attachments ✅ fenced (`attachments.py:28-63`). **Web-search and calendar results are unfenced** (**P1**). |
| 11 | CORS configured | ✅ Good | Explicit origin allowlist + credentials, no wildcard (`main.py:30-36`). |
| 11 | Security headers | ⚠️ Partial | Full set exists — but **only on CloudFront for the SPA** (`layer1_persistent/main.tf:236-260`). The API sends none, and the Vercel/Render path has no CSP at all. |
| 11 | No mass assignment | ✅ Good | Every write goes through a narrow schema + `model_dump(exclude_unset=True)`, `user_id` always server-set (`conversations.py:52`, `notes.py:44`, `tasks.py:45`). No `**request.json()` anywhere. |
| 11 | Error handling doesn't leak internals | ✅ Good | Default Starlette 500 body, no traceback; OpenAPI/docs disabled in production (`main.py:17-24`). Minor: `str(exc)` reaches the model context (`tools.py:624`). |

---

## Findings by severity

### 🔴 High

**R1 — Embedding spend is uncapped, unmetered, and unrate-limited**
`api/app/api/routes/notes.py:46,73` → `services/note_search.py:45-50` → `services/embeddings.py:35-57`

Every `POST /notes` and every `PUT /notes` that touches title or content fires an
OpenAI embeddings call. Three compounding problems:

1. **No rate limit.** Note endpoints have no limiter of any kind — only chat, auth,
   and agent tools do.
2. **Not counted by the cost cap.** `month_to_date_cost_usd` sums `UsageLog` only
   (`core/cost_cap.py:43-56`), and embeddings never write a `UsageLog` row — the
   `usage.record_embedding` call (`embeddings.py:56`) only lands somewhere when the
   eval harness has opened a meter. Chat spend is capped at $10/month; embedding
   spend is capped at nothing.
3. **No input bound.** `NoteCreate.content` is `str = ""` with no `max_length`
   (`schemas/note.py:8`), so the only ceiling is the 1 MB body limit — roughly
   250 k tokens, ~$0.005 per call at `text-embedding-3-small` rates.

Any authenticated account can loop note-writes and generate real spend that no
guard observes. That directly contradicts the cost-sensitivity requirement.

*Also, a correctness side-effect:* `text-embedding-3-small` caps input at 8191
tokens. A note past that gets a 400 from OpenAI, which `embed_text`'s bare
`except Exception` swallows (`embeddings.py:47-52`) — the embedding is silently
never stored, so semantic search quietly stops working for exactly the long notes
that need it most.

**Fix:** add `max_length` to `Note.content` (~20 k chars covers real use and sits
under the model's token limit), add a per-user write limiter on the notes routes,
and write a `UsageLog` row for embedding tokens so the monthly cap sees them.

---

**P1 — Tool output is fed back to the model unfenced (indirect prompt injection)**
`api/app/agent/tools.py:459-466` (web search), `tools.py:529-539` (calendar)

Attachment content is carefully fenced, with the close-tag lookalike stripped and
an explicit "data, never instructions" preamble (`services/attachments.py:49-63`).
That care is not applied to the other two attacker-reachable channels: Tavily
snippets and Google Calendar event summaries/descriptions go into the model
context as bare JSON.

The agent holds write-capable tools — `delete_task`, `create_task`,
`calendar_delete_event`, `calendar_create_event`. A web page the model searches
can carry text addressed to the model, and there is nothing structural stopping it
being read as instruction. Blast radius is bounded (all tools are scoped to the
calling user, none move money), but for a published portfolio demo this is the
most conspicuous design gap.

**Fix:** reuse the existing fencing helper. Wrap each tool result in a
`<tool_result name="...">` block with the same "data, not instructions" preamble,
and strip close-tag lookalikes from the snippet text.

---

**D1 — Render deploy is broken as configured** *(deploy-path blocker)*
`render.yaml:8` with `render.yaml:12-13`

`healthCheckPath: /docs` combined with `ENVIRONMENT: production`. Production
disables the docs routes entirely (`api/app/main.py:17-24`), so `/docs` returns
404 and Render's health check can never pass — the service never enters a healthy
state. `/health` and `/health/ready` exist for exactly this (`main.py:45-58`).

`render.yaml` also never sets `TRUST_PROXY_HEADERS`, so it defaults to `False`
(`core/config.py:120`). Behind Render's proxy `request.client.host` is the proxy,
not the client — so **every user shares one per-IP auth bucket**: 10 logins per
minute for the entire service, and per-attacker limiting doesn't exist. The AWS
path sets this correctly (`layer2_ephemeral/main.tf:43`); Render does not.

**Fix:** `healthCheckPath: /health`; add `TRUST_PROXY_HEADERS: "true"` and
`TRUSTED_PROXY_HOPS: "1"`.

---

### 🟠 Medium

**T2 — A crash mid tool-call permanently breaks a conversation**
`api/app/agent/loop.py:282-309`

The assistant message carrying `tool_calls` is committed at line 285. The matching
`tool` result messages are committed at line 309, after the tool loop. A crash,
deploy, or unhandled tool exception in between leaves an assistant `tool_calls`
message with no corresponding `tool` messages.

`_build_context` (`loop.py:76-82`) replays all history verbatim on the next turn,
so the provider receives an assistant tool-call with no result — a 400 on **every
subsequent turn in that conversation, permanently**. The user's only recovery is
deleting the conversation. Persisting both in one transaction closes it.

**V1 — Unvalidated LLM tool arguments truncate the stream**
`api/app/agent/tools.py:616-626`, called from `loop.py:297`

`call_tool` hands the model's raw JSON straight to handlers and catches only
`ValueError`/`KeyError`. Anything else escapes: `{"location": 123}` →
`AttributeError` on `.strip()` (`tools.py:396`); `{"max_results": []}` →
`TypeError` on `int()` (`tools.py:509`).

That call site sits **outside** the `try/except` that exists specifically to
convert mid-stream failures into an SSE `error` event (`loop.py:173-236`). So an
unhandled tool exception propagates out of the generator, the SSE stream truncates
with no signal, and the user sees a frozen bubble. Same failure class as B1 in
code-review-findings.md, different site.

**Fix:** broaden the `except` in `call_tool` to `Exception` (log + return a
structured error) and validate args against the tool schemas.

**C1 — In-flight chat slots leak when Redis flaps**
`api/app/core/inflight.py:48-67` vs `:75-88`

`acquire_turn_slot` falls back to the in-memory counter when Redis raises;
`release_turn_slot` tries Redis **first** and returns on success. If Redis is down
at acquire and healthy at release, the in-memory counter is incremented and never
decremented. After `CHAT_MAX_CONCURRENT_TURNS` (2) such events, that user is
permanently locked out of chat on that task until it restarts.

Separately, `INCR` then `EXPIRE` are two round-trips (`inflight.py:51-54`), not
one atomic op — a process death between them leaves a key with no TTL.

**Fix:** have `acquire_turn_slot` return which backend granted the slot and
release against that same backend; collapse INCR+EXPIRE into a Lua script.

**T1 — Refresh rotation spans two transactions**
`api/app/services/refresh_tokens.py:103-114`

The revoke commits at line 113; `issue_token_pair` then opens a *second*
transaction to insert the successor (`:44-49`). If that second commit fails, the
presented token is already revoked with no successor issued — the session is dead,
and the client's natural retry with the old token trips reuse detection and burns
the whole family. One transaction spanning both fixes it.

**S1 — No security headers on API responses**
`api/app/main.py:19-36`

The full header set exists only on the CloudFront distribution serving the SPA
(`layer1_persistent/main.tf:236-260`). The API behind the ALB sends no
`Strict-Transport-Security`, no `X-Content-Type-Options: nosniff`, and no
`Cache-Control: no-store` on `/auth/*` responses that carry access tokens. On the
Vercel/Render path the SPA gets no CSP at all — `web/vercel.json` has no `headers`
block.

**A1 — Registration is an account-existence oracle**
`api/app/api/routes/auth.py:57-59`

Login enumeration is defended thoroughly with a dummy-hash timing equalizer
(`core/security.py:40-50`), then `register` gives the same fact away directly:
400 "Email already registered" vs. 201. Rate-limited to 10/min/IP, which slows
but does not close it. Standard fix is to return 201 either way and disclose
through a verification email — a real change in product shape, so worth a
deliberate decision rather than a silent patch.

**A2 — No account lockout or progressive backoff**
`api/app/core/rate_limit.py:220-239`

Per-IP sliding window only. Nothing counts failures *per account*, so an attacker
with rotating IPs (or any botnet) gets unlimited attempts against a chosen email.
Bcrypt makes each guess expensive, which is real mitigation, but a per-account
failure counter with exponential backoff is the missing layer.

**I1 — Message send and calendar create are not idempotent**
`api/app/api/routes/conversations.py:96`, `api/app/agent/tools.py:542`

No idempotency keys anywhere in the repo. The money path is the worst case: the
chat endpoint streams, so a mid-turn network drop is precisely when a client
retries — and the retry re-bills a full LLM turn and appends a duplicate user
message. `calendar_create_event` duplicates a real event on the user's real
calendar; Google supports a client-supplied event `id` for exactly this.

(The OpenAI SDK's own `max_retries=2` in `agent/client.py:20` is safe — those
retries happen before the first streamed byte.)

**Q1 — No Redis socket timeouts**
`api/app/core/rate_limit.py:106-108`

`aioredis.from_url(url, decode_responses=True)` sets no `socket_timeout` or
`socket_connect_timeout`. The fallback-to-in-memory path (`:174-176`) catches
*exceptions*, not *hangs* — so a wedged ElastiCache node blocks the limiter call
rather than degrading. The limiter is a dependency on every auth and chat request,
so that stalls the request path instead of failing over. Two kwargs fix it.

---

### 🟡 Low

- **Unbounded field lengths** — `Note.content`, `Note.tags` (count and element
  size), `Task.description` have no `max_length` (`schemas/note.py:8,18-19`,
  `schemas/task.py:10`). Only the 1 MB body gate bounds them.
- **`ilike` wildcards unescaped** — user-supplied `%`/`_` in `q` act as wildcards
  (`routes/notes.py:30`, `services/note_search.py:62`). Not injection (fully
  parameterized), but a leading-`%` LIKE is a full table scan.
- **Weather tool has no rate limit** — `tools.py:395` is the one agent tool with no
  limiter. The upstream API is free and public, so cost is nil.
- **Weather cache stampede** — no single-flight lock around the 1 h cache
  (`tools.py:379-392`); also per-process, so it multiplies by task count.
- **`token_version` lost update** — `refresh_tokens.py:123` is a Python
  read-modify-write rather than `SET token_version = token_version + 1`. Two
  concurrent logouts can lose one increment; outcome is still "sessions
  invalidated", so harmless today.
- **`upsert_credential` read-then-insert** — `services/google_oauth.py:72-99`
  races against the `unique` constraint on `user_id`; the route's `except` clause
  (`routes/integrations.py:61`) doesn't catch `IntegrityError`, so a concurrent
  double-callback 500s.
- **Internal exception text reaches the model context** — `tools.py:624` returns
  `str(exc)`; `tools.py:457` and `:403` include upstream host details. No secrets
  leak (the Tavily key travels in the POST body, not the URL — checked), but
  internal wording surfaces to the user through the assistant.
- **No `FRONTEND_ORIGIN` production validator** — nothing stops it being set to
  `*`, which combined with `allow_credentials=True` would be a real hole. The
  existing `_require_real_secrets_in_production` validator (`core/config.py:156`)
  is the natural home for the check.

---

## What is genuinely well built

Worth stating plainly, because it shapes the verdict — these are not the defaults,
they are deliberate and correct:

- **Tenancy isolation is airtight.** Every single object read goes through
  `get_owned_or_404` or an explicit `user_id ==` predicate. I found no IDOR path.
  404-not-403 is used deliberately to avoid leaking id existence (`api/deps.py:79-85`).
- **Refresh-token rotation with family-based theft detection**, claimed via a
  conditional `UPDATE` so the database arbitrates the race
  (`services/refresh_tokens.py:95-111`). This is the part most implementations
  get wrong.
- **CSRF defense correctly reasoned** — the one cookie-authenticated endpoint
  requires a non-safelisted header, with the rate limiter ordered *before* the
  CSRF check so a header-less attacker can't hammer it for free (`auth.py:99-105`).
- **The body-size gate is raw ASGI**, ahead of routing and dependency solving, and
  checks both the declared `Content-Length` and the streamed count
  (`core/body_limit.py`).
- **Mass assignment is closed everywhere**, no exceptions.
- **The cost-cap / in-flight-ceiling pair** shows the author understood that a
  read-then-act budget check is only sound if parallelism is bounded, and
  documented exactly that (`core/cost_cap.py:14-18`).
- **Secrets hygiene** — `.env` files are gitignored and untracked (verified against
  `git ls-files`); OAuth tokens are Fernet-encrypted at rest; production refuses to
  boot on a default `SECRET_KEY` or a missing `ENCRYPTION_KEY`.
- **AWS infrastructure** — RDS encrypted and private, WAF with managed rule sets,
  secrets via Secrets Manager, and a Terraform validator that refuses to apply the
  API without TLS unless insecure HTTP is explicitly opted into
  (`layer2_ephemeral/variables.tf:128-134`).

---

## Prioritized action list

### Must fix before deploy

1. **D1** — `render.yaml`: `healthCheckPath: /health`, add `TRUST_PROXY_HEADERS`.
   *Hard blocker on that path; ~2 lines.*
2. **R1** — Bound `Note.content`, rate-limit note writes, and record embedding
   tokens in `UsageLog` so the monthly cap covers them. *Directly contradicts the
   cost-sensitivity requirement.*
3. **P1** — Fence web-search and calendar tool results with the existing
   attachment-fencing pattern. *Reuses code that already exists.*
4. **V1** — Broaden `call_tool`'s `except` to `Exception`. *One line; stops a
   malformed tool arg from silently killing a stream.*
5. **T2** — Commit the assistant tool-call message and its tool results in one
   transaction. *Prevents permanent, unrecoverable conversation corruption.*

### Should fix soon after

6. **S1** — `Strict-Transport-Security`, `X-Content-Type-Options`, and
   `Cache-Control: no-store` on `/auth/*`; add a `headers` block to `vercel.json`.
7. **C1** — Release the in-flight slot against the backend that granted it.
8. **Q1** — `socket_timeout` / `socket_connect_timeout` on the Redis client.
9. **T1** — Single transaction across refresh revoke + reissue.
10. **A2** — Per-account failure counter with progressive backoff on login.
11. **I1** — Idempotency key on `POST /messages`; client-supplied event `id` on
    `calendar_create_event`.

### Nice to have

12. **A1** — Decide deliberately whether registration should stop disclosing
    existence (needs email verification to do properly).
13. Escape `%`/`_` in `ilike` patterns; add the `FRONTEND_ORIGIN != "*"` production
    validator; `max_length` on remaining free-text fields; single-flight lock on the
    weather cache; `IntegrityError` handling on register and credential upsert.

---

## Readiness score

**72 / 100**

The security fundamentals that are hardest to retrofit — tenancy isolation, token
lifecycle, CSRF, secrets management, request-size bounding, infrastructure posture
— are done to a standard well above what this codebase's size would predict. The
score is held down by a category that is simply absent (idempotency), one that is
half-applied (prompt-injection fencing on two of three untrusted channels, security
headers on the SPA but not the API), a genuine uncapped-spend path, and two deploy
configuration bugs.

## Verdict

**No-Go for the Render path as currently configured** — D1 is a hard blocker; the
service cannot pass its own health check.

**Conditional Go for the AWS path** once items 1–5 land. None of the five is more
than a small, well-localized change, and four of them reuse patterns already
present elsewhere in this repo. Items 6–11 are appropriate as immediate
post-deploy follow-ups.

Remaining blockers: **D1, R1, P1, V1, T2.**
