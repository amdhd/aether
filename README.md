# Aether

An AI personal assistant web app: FastAPI + async SQLAlchemy backend, React +
TypeScript frontend, with a DeepSeek-powered tool-calling agent for managing
your tasks, notes, calendar, and more.

A full-stack project: streaming chat, a tool-calling agent, JWT auth, OAuth
integrations, background-free async I/O end to end, and a test suite + CI on
both the backend and frontend.

## Features

- **Chat** with a DeepSeek-powered assistant, streamed over SSE with markdown
  rendering and visible "thinking"/tool-call traces.
- **Personas** — switch the assistant's tone per conversation (productivity
  coach, research assistant, casual friend).
- **Tools** the assistant can call on your behalf: create/update/list tasks
  and notes, get the weather (data.gov.my), web search (Tavily), and manage
  your Google Calendar.
- **Memory** — long conversations are automatically summarized so context
  doesn't grow unbounded.
- **Tasks** — a kanban-style board (To do / Doing / Done) with priorities and
  due dates.
- **Notes** — notes with tags and **semantic search**: notes are embedded
  (OpenAI `text-embedding-3-small`) and stored in Postgres via **pgvector**, so
  the assistant retrieves by meaning, not just keywords. Falls back to a keyword
  scan when no embedding key is configured.
- **Analytics dashboard** — messages and token usage per day, tool-usage
  breakdown, and lifetime totals.
- **Auth** — short-lived JWT access tokens kept in memory, plus refresh tokens
  delivered as **HttpOnly cookies** (not readable by JS). Refresh tokens
  **rotate on every use** with **reuse detection**: replaying a rotated token
  revokes the whole token family.
- **Rate limiting** on chat and external-API tools (web search, calendar).
- **Prompt-injection guardrail** — the base system prompt marks tool, web, and
  note content as untrusted data: embedded directives are ignored, and
  unrequested destructive actions require explicit confirmation.
- **Encrypted credentials** — Google OAuth tokens are stored Fernet-encrypted
  at rest, and disconnecting revokes the grant at Google's endpoint (not just a
  local delete).
- **Structured logging** — the agent loop emits greppable `key=value` records
  for per-turn token/tool/latency summaries, tool calls, and stream failures.

## Stack

- **Backend**: Python 3.12, FastAPI, SQLAlchemy 2.0 (async), Alembic, Postgres
  (SQLite for tests), Pydantic v2, JWT auth.
- **Frontend**: React 19, TypeScript, Vite, Tailwind CSS v4, shadcn/ui-style
  components, TanStack Query, Recharts, Zustand.

## Architecture

```
web/ (React + Vite)                       api/ (FastAPI, async)
  ├─ pages/        chat, tasks, notes,       ├─ api/routes/   auth, conversations,
  │                analytics, settings       │                tasks, notes,
  ├─ api/          typed fetch clients        │                analytics, integrations
  └─ store/        Zustand auth + theme       ├─ agent/        loop, tools, personas,
                                              │                memory, client
        │  SSE stream (EventSource)           ├─ services/     embeddings, note search,
        │  Bearer access token                │                refresh tokens, google
        ▼                                      ├─ core/         security, crypto, config,
  POST /conversations/{id}/messages           │                rate_limit, logging
        │                                      └─ models/       SQLAlchemy 2.0 (async)
        ▼                                            │
  agent/loop.py  ──►  DeepSeek (tool-calling, thinking mode)
        │                    │
        │  ◄── tool_calls ───┘
        ▼
  agent/tools.py  ──►  DB (tasks/notes) · data.gov.my · Tavily · Google Calendar
```

The **agent loop** ([`api/app/agent/loop.py`](api/app/agent/loop.py)) is the
core: it builds conversation context (with automatic summarization of older
turns), calls DeepSeek with the tool schemas, and streams the response back to
the browser as Server-Sent Events. Distinct SSE event types — `reasoning`
(visible "thinking"), `token` (assistant text), tool-call traces, and `error` —
let the UI render each phase live. When the model requests a tool, the loop
dispatches to a handler, feeds the result back, and continues until the model
produces a final answer.

## Getting started

### Prerequisites

- Docker and Docker Compose
- (Optional, for running outside Docker) Python 3.12 and Node 20+

### 1. Configure environment variables

From the project root:

```sh
cp .env.example .env
```

Open `.env` and fill in the values you have. The app runs locally without any
external API keys — `DEEPSEEK_API_KEY`, `TAVILY_API_KEY`, and the
`GOOGLE_CLIENT_*` Google Calendar credentials only need to be set to use the
chat assistant, web search tool, and calendar integration respectively.

For `SECRET_KEY` and `ENCRYPTION_KEY`, generate real values rather than using
the placeholders:

```sh
python3 -c "import secrets; print(secrets.token_urlsafe(64))"          # SECRET_KEY
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # ENCRYPTION_KEY
```

> Note: `docker-compose.yml` reads this `.env` from the project **root** (not
> `api/.env`). The root `.env` file is gitignored.

### 2. Start everything with Docker Compose

```sh
docker compose up --build
```

- API: http://localhost:8000 (interactive docs at `/docs`)
- Frontend: http://localhost:5173

The first time you run this, apply database migrations:

```sh
docker compose exec api alembic upgrade head
```

Register an account at http://localhost:5173/register and start chatting.

## Screenshots

Add screenshots of the chat, tasks board, and analytics dashboard to
`docs/screenshots/` and reference them here, e.g.:

```md
![Chat view](docs/screenshots/chat.png)
![Tasks board](docs/screenshots/tasks.png)
![Analytics dashboard](docs/screenshots/analytics.png)
```

## Running tests

Backend:

```sh
cd api
pip install -r requirements-dev.txt
ruff check .
pytest
```

Frontend:

```sh
cd web
npm install
npm run lint
npm run build
npm run test
```

Both suites run automatically on every push/PR via GitHub Actions
(`.github/workflows/ci.yml`).

## Deployment

A production deployment typically looks like:

- **API + Postgres**: deploy `api/` (Docker image) plus a managed Postgres
  database to [Railway](https://railway.app) or [Render](https://render.com).
  Set the same environment variables as `.env.example`, pointing
  `DATABASE_URL` at the managed Postgres instance and `FRONTEND_ORIGIN` /
  `GOOGLE_REDIRECT_URI` at your deployed URLs. Run
  `alembic upgrade head` as a release/start command.
- **Frontend**: deploy `web/` to [Vercel](https://vercel.com) (or any static
  host) with `VITE_API_URL` set to your deployed API URL.

See `render.yaml` and `web/vercel.json` for ready-to-use starting
configurations for Render and Vercel.

### Google Calendar OAuth in production

The Google integration works out of the box for local development, where the
redirect URI points at `localhost`. To make it work on a deployed domain:

1. **Update the environment variables** to your production URLs:
   - `GOOGLE_REDIRECT_URI` → `https://<your-api-domain>/api/v1/integrations/google/callback`
   - `FRONTEND_ORIGIN` → `https://<your-frontend-domain>`
2. **Register the redirect URI in the Google Cloud Console**: under
   *APIs & Services → Credentials → your OAuth 2.0 Client ID*, add the exact
   production `GOOGLE_REDIRECT_URI` above to **Authorized redirect URIs**. It
   must match character-for-character or Google will reject the callback.
3. **Publish the OAuth consent screen** (or add testers): a new OAuth app
   starts in *Testing* mode and only allows accounts listed as test users.
   Either add the accounts you'll sign in with as test users, or submit the
   app for verification to allow any Google account.

The same applies to any other origins (`localhost:5173`) referenced in the
consent screen's *Authorized JavaScript origins*.
