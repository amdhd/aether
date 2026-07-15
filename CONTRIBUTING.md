# Contributing to Aether

Aether is an AI personal-assistant web app: a FastAPI backend, a React SPA, and
a DeepSeek-backed agent with tool-calling, semantic note search (pgvector), and
Google Calendar integration. This guide covers how the code is organized and the
conventions we hold changes to.

## Repository layout

```
api/    FastAPI backend (Python 3.12, async SQLAlchemy 2.0, Alembic)
web/    React 19 + Vite + TypeScript SPA
docs/   Design and deployment notes
```

### Backend (`api/app`)

| Package        | Owns                                                        |
|----------------|------------------------------------------------------------|
| `api/routes`   | FastAPI routers — thin wiring: validate, call one service, return |
| `services`     | Business logic (embeddings, note search, attachments, OAuth) |
| `agent`        | The agent loop, DeepSeek client, personas, and tool schemas |
| `models`       | SQLAlchemy ORM models                                       |
| `schemas`      | Pydantic request/response and domain models                |
| `core`         | Settings, logging, security, rate limiting                 |
| `db`           | Session/engine setup and pooling                            |
| `alembic`      | Database migrations                                         |

### Frontend (`web/src`)

`pages/` (route views) · `api/` (typed fetch clients) · `components/` (shared UI)
· `hooks/` · `store/` (Zustand) · `lib/` · `types/`.

## Principles

1. **Routes stay thin.** A route validates input, calls one service, and returns
   a response — no business logic, DB queries, or SDK calls inside a route.
2. **Keep the LLM and vector store swappable.** DeepSeek lives behind
   `agent/client.py`; changing providers should not ripple into routes or
   services. Vector search is isolated in the notes/embeddings services.
3. **Single responsibility.** A module has one reason to change. Split it if it
   grows a second.
4. **No premature abstraction.** Solve today's problem cleanly; generalize when a
   second real case appears.
5. **Boring and proven beats clever.** Prefer the solution the next maintainer
   understands at a glance.

## Conventions

- **Type hints** on all function signatures; Pydantic models for data crossing
  boundaries (API in/out).
- **Async for I/O** — DB, HTTP, and model calls must not block the event loop.
- **Explicit errors over silent failures.** Never `except: pass`.
- **No secrets in code** — everything through `core.config` / env vars. Keep
  `.env.example` complete.
- **Per-user ownership on every data endpoint** (no IDOR); every endpoint
  enforces authentication.
- Match the existing style of the file you're editing. Small, focused diffs, one
  concern per change.

## Testing

- Backend: `pytest` with `httpx.AsyncClient`. Mock external services (DeepSeek,
  Tavily, Google) — tests must be fast and deterministic.
- Frontend: Vitest + React Testing Library.
- Ship a test with every non-trivial change. When fixing a bug, write the failing
  test first, then fix it.
- CI (GitHub Actions) runs both suites, including a Postgres + pgvector leg for
  the vector-search paths.

## Local development

```bash
docker compose up          # api, db (pgvector), web
cd api && alembic upgrade head
```

Copy `.env.example` to `.env` and fill in the required keys (`DEEPSEEK_API_KEY`,
`SECRET_KEY`, `ENCRYPTION_KEY`, and the optional integration keys). See
`README.md` for the full setup walkthrough.

## Database changes

Model changes require an Alembic migration:

```bash
cd api && alembic revision --autogenerate -m "describe the change"
```

Review the generated migration by hand — autogenerate does not catch everything
(e.g. pgvector columns, enum value changes). Keep a single head.
