# Marketing Coach Persona + CSV Upload — Feature Plan

## Context

Aether has a first-class **persona** system (`api/app/agent/personas.py` + a `Persona` enum stored on each conversation as `VARCHAR(20)`). We're adding a **Marketing Coach** persona that analyzes advertising-campaign data and gives budget/optimization advice. Because Meta Ads can't be pulled directly, the user supplies data by **uploading a CSV/TSV file** (exported from Google Sheets / Excel / the ad platform) — not by pasting the whole table into the chat.

The persona itself is trivial (the system already supports personas). The real work is **file upload**, which is net-new surface: there is currently no upload endpoint and `Message` has no attachment column. The feature stays inside the existing conversations/messages module.

**Decisions:** prompt-only analysis (the model does the math; no deterministic calc tool for v1) · CSV/TSV only, stdlib parsing, no new deps · attachment stored on the message so follow-up questions keep the data in context.

---

## Part A — The persona (no migration)

- **`api/app/models/conversation.py`** — add `marketing_coach = "marketing_coach"` to the `Persona` enum. No Alembic migration needed (the column is `native_enum=False` `VARCHAR(20)`; `marketing_coach` = 15 chars).
- **`api/app/agent/personas.py`** — add `PERSONA_SYSTEM_PROMPTS[Persona.marketing_coach]`. The prompt directs the model to: when campaign data is attached, derive spend, impressions, clicks, conversions → **CTR, CPC, CPM, CPA, ROAS, conversion rate**; flag top/bottom performers and wasted spend; give concrete budget-reallocation advice; use the existing `web_search` (Tavily) tool for benchmarks; show its arithmetic; and be explicit about what the data can't tell it.
- **`web/src/types/index.ts`** — add `'marketing_coach'` to the `Persona` union.
- **`web/src/pages/ChatPage.tsx`** — add `marketing_coach: 'Marketing Coach'` to `PERSONA_LABELS` (the dropdown auto-renders it).
- **`api/tests/test_personas.py`** — assert the new persona resolves to a prompt.

## Part B — File upload (new surface, inside conversations module)

- **Parser — `api/app/services/attachments.py`**: `parse_tabular_file(filename, raw_bytes)` using stdlib `csv`. Accept `.csv`/`.tsv` only, sniff delimiter, cap size (~200 KB) and rows (~300, truncate with a note), return a normalized table string + a shape summary ("42 rows × 7 cols"). Clear errors for oversized / non-CSV.
- **DB migration**: add nullable `attachment_name VARCHAR(255)` + `attachment_content TEXT` to `messages` (`api/app/models/message.py` + one Alembic revision). Runs via the normal `alembic upgrade head` step.
- **Endpoint** — `POST /conversations/{id}/messages` (`api/app/api/routes/conversations.py`): switch from JSON body to multipart (`content: str = Form(...)`, `file: UploadFile | None = File(None)`). When a file is present, parse it and store the parsed table on the user `Message` (`attachment_content`), keeping the visible `content` = the user's typed prompt so raw numbers never clutter the chat bubble.
- **Agent context** — `api/app/agent/loop.py` `_build_context`: when a user message has `attachment_content`, append it to that message's content sent to the model (`{content}\n\n[Attached file: {name}]\n{table}`). Stored on the message, so it's available for follow-up questions across the conversation.
- **Schema** — `MessageRead` (`api/app/schemas/conversation.py`) gains optional `attachment_name` so the UI can show a "📎 filename" chip.
- **Frontend** — `ChatPage.tsx`: paperclip attach button (`accept=".csv,.tsv"`), filename chip with remove, switch the send call to `FormData` (keeping the SSE stream read intact).

---

## Verification

- **Unit**: parser (valid CSV, oversized, non-CSV, odd delimiter) + persona resolution test.
- **E2E (docker-compose)**: new conversation → pick **Marketing Coach** → attach a sample campaign CSV → confirm the streamed reply cites the actual numbers; a follow-up question still has the data in context.

## Caveats / future

- **Prompt-only math**: the model does the arithmetic, so ROAS/CPA can occasionally be off. A deterministic `analyze_campaign_data` agent tool is the clean upgrade later.
- **CSV/TSV only** (stdlib). Native `.xlsx` would need the `openpyxl` dependency — easy to add later. Google Sheets/Excel → "Download as CSV".
