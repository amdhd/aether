# Code Review Findings — bugs, inefficiencies, and suggested changes

> A full-repo review pass. Each finding was verified against the actual code
> (file:line references included). Severity: **High** = user-visible breakage
> or money/security exposure; **Medium** = wrong behavior in a realistic edge
> case or a scale cliff; **Low** = hygiene/cosmetic.
>
> Companion to [interview-prep.md](interview-prep.md) — this is the "what
> would you improve about this repo?" answer, with receipts.

---

## 1. Bugs & robustness

### B1. (Medium) Summarization/context-build failures truncate the SSE stream with no `error` event

**Where:** `api/app/agent/loop.py:153-155`

`maybe_summarize_history()` (a network call to DeepSeek) and `_build_context()`
(a DB query) run **outside** the `try/except` that guards the streaming call.
The whole point of that handler (`loop.py:167-236`) is that headers are
already sent once the generator runs, so failures must surface as an SSE
`error` event — but it only covers the stream itself.

**Why it matters:**
- DeepSeek 429/5xx/timeout during summarization → exception propagates out of
  the generator → the stream dies mid-flight with **no signal to the client**
  (a blank/frozen bubble).
- The user message is committed *before* summarization (`loop.py:142-151`), so
  the failed turn leaves a persisted user message with no reply. The user
  retries → duplicate messages accumulate.

**Suggested fix:** summarize is best-effort — wrap `maybe_summarize_history`
in its own `try/except` (log + skip on failure, the summary is a cache, not
correctness-critical). Wrap `_build_context` in the same handler that emits
the `error` event. Both are ~5 lines.

### B2. (Low) Concurrent registration with the same email returns a 500 instead of a 400

**Where:** `api/app/api/routes/auth.py:57-60`

The check-then-insert (`select` for existing email → insert) is not atomic.
Two concurrent registrations for the same address both pass the check; the
second insert hits the `users.email` unique constraint → unhandled
`IntegrityError` → 500.

**Suggested fix:** catch `IntegrityError` on commit and raise the same 400
"Email already registered". Rare, but a 500 on a public unauthenticated
endpoint also pollutes error-alert metrics.

### B3. (Medium) A run of large attachments can brick a conversation's context window

**Where:** `api/app/agent/memory.py:11` (`KEEP_RECENT_MESSAGES = 10`), with
`MAX_ATTACHMENT_BYTES = 200_000` in `api/app/services/attachments.py:17`

The recent-verbatim window is **count-based**, not size-based. The
summarization threshold (24k chars) only folds messages *older* than the
recent 10. Ten recent messages each carrying a ~200 KB parsed CSV (~50k
tokens each) build a context of ~500k tokens — far past the model's window.

**Why it matters — it self-compounds:** the turn fails with a provider 400
(cleanly surfaced as an `error` event, at least), but the user message is
committed first. Each retry adds another message and the conversation stays
bricked until 10 further messages push the big attachments out of the recent
window — except sending those messages keeps failing.

**Suggested fix:** make the window size-aware — either a total character
budget for `_build_context` (drop/fold attachment blocks beyond the budget),
or count attachment-carrying messages separately (e.g. keep at most 2-3
attachments verbatim). The attachment text is already bounded per-file; the
fix is bounding the *aggregate*.

### B4. (Low) Google token refresh has no locking — concurrent calendar calls race the refresh

**Where:** `api/app/services/google_oauth.py:128-153`

`get_valid_access_token` checks expiry, refreshes, and writes back — with no
mutex. Two calendar tool calls firing when the token is within its 60 s
leeway both refresh. Both tokens are valid (Google mints each), last write
wins. Benign today, but if a provider ever issues a *rotating* refresh token
(which invalidates predecessors), the race becomes a data-loss bug.

**Suggested fix:** hold a per-user asyncio lock around the
check-refresh-write, or serialize refreshes with the same pattern
`refreshAccessToken` uses on the frontend (single-flight promise).

---

## 2. Efficiency & scale

### E1. (Medium) Conversation detail returns the entire message history, unpaginated

**Where:** `api/app/api/routes/conversations.py:59-67` +
`api/app/schemas/conversation.py:43-44`

`GET /conversations/{id}` does `db.refresh(conversation, attribute_names=
["messages"])` → `ConversationDetail.messages` includes **every** message
(including `reasoning_content` and full `tool_calls` JSON) since creation.
Summarization bounds the *LLM* context but never prunes the *database* — a
power user's 2,000-message chat is a multi-MB JSON payload on every page
open, and the frontend refetches it after every turn (`ChatPage.tsx:509`).

**Suggested fix:** paginate messages (limit/offset or cursor), and/or let the
client load the detail once and append streamed turns locally instead of
refetching the whole history each time. The frontend already renders
optimistically — it just needs a delta endpoint (e.g. `?after_id=`).

### E2. (Low) Cost-cap and analytics queries scan a per-user log instead of seeking

**Where:** `api/app/models/usage_log.py:14` (index on `user_id` only),
queried by `core/cost_cap.py:43-56` and `api/routes/analytics.py`

Every chat turn runs `SUM(prompt_tokens), SUM(completion_tokens) WHERE
user_id = ? AND created_at >= month_start` — with only a `user_id` index,
Postgres walks that user's whole history. Fine at personal scale; it becomes
a real per-turn cost once `usage_logs` has millions of rows (one row per
turn, ~2-4 per conversation).

**Suggested fix:** composite index `(user_id, created_at)`, which also
serves the analytics day-bucketing. One Alembic migration.

### E3. (Low) A fresh `httpx.AsyncClient` per external call — no connection reuse

**Where:** `api/app/agent/tools.py` (`_fetch_weather_data:385`,
`_web_search:445`, `_calendar_*:518,558,588`) and
`api/app/services/google_oauth.py:37,53,119`

Each weather/web-search/calendar call constructs a new client — a new TLS
handshake and no keep-alive pooling across calls. `agent/client.py` already
does it right (module-level `lru_cache`d client with tuned timeouts).

**Suggested fix:** share module-level clients with per-call timeouts, like
the DeepSeek/OpenAI clients. Minor win given tool rate limits, but it's the
same pattern the rest of the codebase uses.

### E4. (Low) Eval harness reports could grow unbounded

**Where:** `api/app/eval/reports/` (only `latest.md` is committed; JSON
reports are timestamped per run)

Every run writes a full JSON report. Harmless locally, but in CI (`--check`
on every push) artifacts pile up unless the runner is configured to expire
them. Worth a one-line `.gitignore`-style retention note or artifact TTL.

---

## 3. Dependency & security hygiene

### H1. (Low-Medium) `python-jose` is unmaintained — migrate to PyJWT

**Where:** `api/requirements.txt:9`

python-jose 3.5.0 has no known open CVE as of this writing, but the project
is unmaintained (its own README points elsewhere). Every token in the system
flows through it; a future CVE will have no upstream patch. The API surface
used here is tiny (`jwt.encode/decode` with a pinned `algorithms` list), and
PyJWT is a near drop-in replacement with active maintenance.

**Suggested fix:** swap `jose` → `PyJWT` and re-run the auth test suite
(`tests/test_auth.py` covers the flows).

### H2. (Low) `_DUMMY_PASSWORD_HASH` is computed at import time

**Where:** `api/app/core/security.py:44`

One bcrypt hash (~100 ms) runs on every interpreter start and every test
collection. Trivial, but in test runs with many processes it's pure waste;
`functools.cache` + lazy init (like `crypto._fernet()`) is the house pattern.

### H3. (Note) EMF cost metrics depend on configured price lists being current

**Where:** `api/app/core/config.py:84-89`, consumed by
`core/metrics.py:56-68` and the cost cap

The monthly cap and the runaway-cost alarm are only as accurate as
`LLM_*_COST_PER_1M_TOKENS`. There's no drift check — if the provider changes
pricing, the cap silently over- or under-enforces. Already documented as
"configuration, not gospel" in config; a periodic price-table review (or a
CI note in the calendar) is the cheap mitigation.

---

## 4. Feature gaps (candidates for the next PR)

1. **Self-service password reset.** The only reset path is the admin CLI
   script (`scripts/reset_password.py`); a real app needs a reset-token email
   flow — the session-revocation logic it should reuse already exists
   (`refresh_tokens.revoke_all_for_user`).
2. **Email verification** on registration. Cheap to add (the User model
   already has email uniqueness); prevents account squatting of other
   people's addresses.
3. **Message pagination** — see E1; also unlocks "load older messages"
   scrolling UX.
4. **Redis-as-optional gap**: in-flight turn slots and rate limits silently
   become per-instance without `REDIS_URL` — a startup log line stating
   which backend is active would make the HA toggle's effect observable
   instead of config-only.
5. **pgvector index** when the note corpus grows (HNSW) — deliberately
   skipped today; document the crossover point so the next engineer knows
   when to add it.

---

## 5. Suggested implementation order (quick wins first)

| # | Fix | Effort | Value |
|---|---|---|---|
| 1 | B1 — guard summarization/context-build (error event) | ~15 min | fixes the only realistic mid-stream silence |
| 2 | E2 — composite index migration | ~30 min | removes a per-turn scan before it's a problem |
| 3 | H2 — lazy dummy hash | ~5 min | free |
| 4 | B2 — catch `IntegrityError` on register | ~10 min | no more 500s |
| 5 | E3 — shared httpx clients | ~30 min | matches existing house pattern |
| 6 | B3 — size-aware recent window | ~1 h | unbricks pathological chats |
| 7 | H1 — PyJWT swap | ~1 h + tests | dependency health |
| 8 | E1 — message pagination | ~half day | the real scale fix |
| 9 | B4 — token-refresh lock | ~30 min | insurance before rotating refresh tokens |

---

## 6. Investigated and verified *not* issues (checked during this review)

Kept here so nobody re-files them:

1. **`streamChatMessage` re-POSTs the whole FormData after a 401**
   (`web/src/api/chat.ts:141-151`). Checked: the server's `get_current_user`
   dependency rejects before the message is persisted, so a 401 can only
   occur pre-persistence — the retry cannot duplicate a message.
2. **Body-limit middleware added before CORS** (`api/app/main.py:26-28`).
   Deliberate: CORS ends up outer and decorates the 413, so browsers see the
   status instead of an opaque CORS failure.
3. **WAF `SizeRestrictions_BODY` set to Count** (Terraform layer 2).
   Deliberate: the rule's 8 KB default would 403 legitimate ~200 KB CSV
   attachments; enforcement lives in the app's body-limit middleware.
4. **Refresh-token replay revokes the family on *unknown* jti** too
   (`refresh_tokens.py:80-83`). Not over-aggressive: reaching that branch
   requires a signature-valid token, so it's a leaked/rotated token, not a
   typo'd cookie.

---

## 7. Deliberate trade-offs (documented in the repo — not re-listed as findings)

- Single NAT in demo mode / no CloudTrail, GuardDuty, Flow Logs, KMS CMKs,
  secrets rotation → README "Production roadmap" + `infra/terraform/.checkov.yaml`
  skip buckets.
- Keyword-fallback over-retrieval (eval FM-2) → accepted by design;
  semantic path's distance floor is the real control.
- In-memory rate-limit fallback when Redis is down → availability over
  strictness, logged at call time.
- Rotation's two-commit window (revoke committed before the successor is
  issued) → fail-secure: worst case the user re-logs-in; no window where two
  live tokens exist.
- Dockerfile `apt-get upgrade` over digest pinning → CVE remediation over
  byte-reproducibility (spelled out in the Dockerfile comment).
