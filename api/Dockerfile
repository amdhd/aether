# syntax=docker/dockerfile:1

# --- Builder: compile dependencies into wheels ---
# Pinned digest-free tag is fine here because the builder is discarded; the
# runtime stage below is what ships.
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Build-time compilers live only in this stage, never in the final image.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Production dependencies only — no ruff/pytest/aiosqlite from requirements-dev.
COPY requirements.txt ./
RUN pip wheel --wheel-dir /wheels -r requirements.txt


# --- Runtime: slim image with just the app + prebuilt wheels ---
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# libpq5 is the runtime shared lib asyncpg/psycopg need; curl is for HEALTHCHECK.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 curl \
    && rm -rf /var/lib/apt/lists/*

# Non-root user — Fargate and reviewers expect the container not to run as root.
RUN useradd --create-home --uid 10001 app
WORKDIR /app

COPY --from=builder /wheels /wheels
COPY requirements.txt ./
RUN pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt \
    && rm -rf /wheels

COPY --chown=app:app . .

USER app

EXPOSE 8000

# Liveness probe hits the static health endpoint (no DB dependency).
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
