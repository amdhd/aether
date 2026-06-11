
Build "Aether" — an AI personal assistant web app — as a portfolio project for a
junior full-stack developer. Prioritize clean architecture, security, tests, and
polish over raw feature count. Work in weekly milestones and run tests after each
milestone before moving to the next.

## Tech stack (fixed — do not substitute)
- Backend: Python 3.12, FastAPI, SQLAlchemy 2.0 (async) + Alembic migrations,
  PostgreSQL (SQLite only as a local fallback if Postgres isn't available),
  Pydantic v2 for schemas, httpx for outbound HTTP calls.
- Auth: JWT access + refresh tokens (passlib/bcrypt for hashing, python-jose for JWT).
- LLM: DeepSeek API via the official `openai` Python SDK pointed at
  `base_url="https://api.deepseek.com"`, model `deepseek-chat`, using its
  OpenAI-compatible `tools`/function-calling interface. Read the API key from
  `DEEPSEEK_API_KEY` env var.
- Frontend: React 18 + TypeScript + Vite, Tailwind CSS, shadcn/ui components,
  TanStack Query for server state, Zustand for client state, Recharts for charts.
- Streaming: Server-Sent Events (SSE) from FastAPI to React for token-by-token
  chat responses.
- Containerization: Docker Compose for local dev (api, db, web services).
- Testing: pytest + httpx AsyncClient for backend, Vitest + React Testing Library
  for frontend.
- CI: GitHub Actions workflow running backend and frontend tests on every push.

## Data model
- User: id, email (unique), password_hash, name, created_at
- Conversation: id, user_id, title, persona (enum), created_at, updated_at
- Message: id, conversation_id, role (user/assistant/tool), content, tool_calls
  (jsonb, nullable), tool_name (nullable), created_at
- Task: id, user_id, title, description, due_date, priority (low/medium/high),
  status (todo/doing/done), created_at, updated_at
- Note: id, user_id, title, content, tags (string array), created_at, updated_at
- GoogleCredential: id, user_id, access_token (encrypted at rest), refresh_token
  (encrypted at rest), token_expiry
- UsageLog: id, user_id, conversation_id, model, prompt_tokens, completion_tokens,
  tool_name (nullable), created_at

## Agent design
Implement an agent loop in the backend:
1. Load conversation history (apply summarization — see below).
2. Send messages + the full tool schema to `deepseek-chat` with `stream=True`.
3. If the response contains tool_calls, execute the matching Python function,
   append the tool result as a `tool` message, and call the model again.
4. Repeat until the model returns a plain assistant message; stream that
   message's tokens to the frontend via SSE as they arrive.
5. Log token usage and any tool calls to UsageLog.

### Tools to implement (as DeepSeek function/tool definitions)
- `create_task`, `list_tasks`, `update_task`, `delete_task` — operate on the
  Task table, scoped to the authenticated user.
- `create_note`, `list_notes`, `search_notes` — operate on the Note table,
  scoped to the authenticated user.
- `get_weather(location)` — call the OpenWeatherMap free-tier API
  (`OPENWEATHER_API_KEY` env var).
- `web_search(query)` — call the Tavily Search API (`TAVILY_API_KEY` env var),
  designed for LLM-facing search results.
- `calendar_list_events`, `calendar_create_event`, `calendar_delete_event` —
  Google Calendar v3 API, using OAuth2 (see below).

### Personas
Implement 3 selectable personas, each just a different system prompt + tone:
- "Productivity Coach" — terse, action-oriented, proactively suggests tasks.
- "Research Assistant" — thorough, cites sources from web_search results.
- "Casual Friend" — conversational, informal tone.
Persona is chosen per-conversation and stored on the Conversation row.

### Memory / context management
When a conversation's message history exceeds ~6000 tokens, summarize the
oldest messages into a running "memory" string stored on the Conversation
(via a separate, cheap DeepSeek call), and prepend that summary to future
context instead of the raw old messages. Keep the most recent ~10 messages
verbatim.

## Google Calendar OAuth
Implement a standard OAuth2 authorization-code flow: a "Connect Google
Calendar" button in the frontend settings page redirects to Google's consent
screen, the backend handles the callback, exchanges the code for tokens, and
stores encrypted access/refresh tokens in GoogleCredential. Refresh tokens
automatically when expired. Read `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`,
and `GOOGLE_REDIRECT_URI` from env vars — I will create the Google Cloud
project and OAuth consent screen myself and provide these values. If the user
hasn't connected Google Calendar, the calendar tools should return a clear
"not connected" message instead of erroring.

## Security requirements
- Every endpoint enforces authentication; every data-access endpoint enforces
  per-user ownership (no IDOR).
- Passwords hashed with bcrypt; JWTs short-lived with refresh token rotation.
- Encrypt OAuth tokens at rest (e.g. Fernet with a key from `ENCRYPTION_KEY`
  env var).
- Validate and sanitize all user input with Pydantic models.
- Rate-limit the chat endpoint per user to control LLM API cost.
- CORS configured explicitly for the frontend origin only.
- No secrets committed; provide a complete `.env.example`.

## Frontend / UX requirements
- Pages: Login/Register, Chat (main view with persona selector and streaming
  responses rendered as markdown), Tasks (kanban-style board: todo/doing/done),
  Notes (list + search), Settings (profile, Google Calendar connect/disconnect),
  Analytics dashboard (messages per day, tokens used, tool-call breakdown via
  Recharts).
- Consistent design system: define a Tailwind theme (colors, spacing, type
  scale) up front and use it throughout — no per-page improvised styles.
- Responsive (mobile/tablet/desktop), with loading/empty/error states for
  every async view.
- Accessible: semantic HTML, labeled inputs, keyboard-navigable chat and
  kanban board.

## Weekly milestones
- Week 1: Project scaffold (Docker Compose, FastAPI app, Postgres, Alembic,
  React+Vite+Tailwind shell), auth (register/login/refresh/logout), Task and
  Note CRUD APIs + basic frontend pages for them.
- Week 2: DeepSeek integration, agent tool-calling loop wired to the Task/Note
  tools, conversation persistence, chat UI with SSE streaming and markdown
  rendering.
- Week 3: Weather + web search tools, Google Calendar OAuth + calendar tools,
  persona selector, memory/summarization for long conversations.
- Week 4: Usage analytics dashboard, rate limiting, full test suites (pytest +
  Vitest) + GitHub Actions CI, polish pass (responsive/accessibility/empty
  states), README with setup instructions, screenshots, and a deployed demo
  (Railway/Render for API+DB, Vercel for frontend).

## Process
- After each weekly milestone, run the full test suite and report pass/fail
  before continuing.
- Ask me before introducing any new major dependency not listed above.
- Flag clearly which env vars/API keys/OAuth credentials I need to obtain and
  when, so I'm not blocking your progress.
```

---

## Credentials you'll need before/while building

- **DeepSeek API key** — `DEEPSEEK_API_KEY`, from platform.deepseek.com
- **Tavily API key** — `TAVILY_API_KEY`, from tavily.com (free tier for AI search)
- **OpenWeatherMap API key** — `OPENWEATHER_API_KEY`, from openweathermap.org (free tier)
- **Google OAuth client** — `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`,
  `GOOGLE_REDIRECT_URI`, from Google Cloud Console (enable the Calendar API,
  configure the OAuth consent screen, add a Web application OAuth client)
