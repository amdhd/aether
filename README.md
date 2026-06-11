# Aether

An AI personal assistant web app: FastAPI + async SQLAlchemy backend, React +
TypeScript frontend, with a DeepSeek-powered tool-calling agent for managing
tasks, notes, and (eventually) your calendar.

> Status: Week 1 (project scaffold, auth, Task/Note CRUD) complete. See
> `AETHER_AI_AGENT_PROMPT.md` for the full project spec and milestone plan.

## Stack

- **Backend**: Python 3.12, FastAPI, SQLAlchemy 2.0 (async), Alembic, Postgres
  (SQLite for tests), Pydantic v2, JWT auth.
- **Frontend**: React 19, TypeScript, Vite, Tailwind CSS v4, shadcn/ui-style
  components, TanStack Query, Zustand.

## Getting started

1. Copy `.env.example` to `.env` and fill in any values you have (defaults
   work for local development without external API keys).
2. Start everything with Docker Compose:

   ```sh
   docker compose up --build
   ```

   - API: http://localhost:8000 (docs at `/docs`)
   - Frontend: http://localhost:5173

## Running tests

Backend:

```sh
cd api
pip install -r requirements-dev.txt
pytest
```

Frontend:

```sh
cd web
npm install
npm run lint
npm run build
npx vitest run
```
