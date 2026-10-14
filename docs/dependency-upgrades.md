# Dependency upgrades — 2026-10-14

Security fixes were applied first, followed by routine updates, runtime alignment,
and compatible major migrations.

- Replaced python-jose with PyJWT 2.15.1, preserving explicit algorithm verification
  and exception handling. Added regression tests for token dates, signatures,
  unsigned tokens, and OAuth state token types.
- Updated backend production and development pins, including Redis 8.1.0,
  pgvector 0.5.1, and the matched OpenTelemetry SDK/instrumentation releases.
- Updated frontend dependency ranges and lockfile. Disabled Node native web storage
  for Vitest so jsdom supplies browser storage.
- Standardized project runtime selectors on Python 3.12 and Node 24 LTS.
  Updated the frontend container to Node 24 and nginx 1.30.5.
- Updated GitHub Actions versions and Terraform CI to 1.16.6.
- Migrated AWS provider constraints to 6.68 and Random to 3.9, retained provider
  lockfiles with Linux amd64 and macOS arm64 checksums, and replaced deprecated
  AWS region name attributes with region.

## Compatibility holds

- TypeScript remains 6.0.3: typescript-eslint 8.71.2 rejects TypeScript 7.
- SQLAlchemy remains on the latest 2.0 release (2.0.54): OpenTelemetry SQLAlchemy
  instrumentation 0.66b1 rejects SQLAlchemy 2.1.
- OpenAI upgraded to 2.54.0: version 3 uses httpx2, outside the current HTTPX tracing
  instrumentation. Revisit alongside tracing support rather than losing LLM spans.

## Validation

- Frontend lint, production build, and 71 tests passed under Node 24.18.0.
- Backend SQLite suite: 301 passed, four Postgres-only tests skipped.
- Backend Postgres/pgvector suite: 305 passed against an isolated local container.
- Backend lint, dependency compatibility check, and offline RAG evaluation passed.
- npm audit and pip-audit reported no known vulnerabilities in the checked trees.
- API and frontend Docker images built successfully.
- Both Terraform stacks validated; formatting passed. Validation used local
  Terraform 1.15.8; CI is configured for 1.16.6.
- Infracost reported no policy failures or diagnostics for the default configuration.

No cloud apply, deployment, production database upgrade, or live LLM request was
performed. A state-aware Terraform plan and hosted CI remain deployment checks.
