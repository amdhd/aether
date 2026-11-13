# syntax=docker/dockerfile:1

# Base images are pinned by digest as well as tag: a tag can be re-pointed at
# different bytes, a digest cannot. Dependabot (docker ecosystem) proposes the
# digest bumps, so security rebuilds of the base still arrive — as reviewed PRs.

# --- Builder: compile dependencies into wheels ---
FROM python:3.15-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928 AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Build-time compilers live only in this stage, never in the final image.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Production dependencies only — no ruff/pytest/aiosqlite from requirements-dev.
# requirements.txt is the hashed lock (see `make lock`): every package,
# transitive ones included, must match a recorded hash or the build fails.
COPY requirements.txt ./
RUN pip wheel --require-hashes --wheel-dir /wheels -r requirements.txt


# --- Runtime: slim image with just the app + prebuilt wheels ---
FROM python:3.15-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# `apt-get upgrade` picks up Debian security updates published since the base
# image was last rebuilt. Without it the image inherits whatever was fixed
# upstream but not yet rolled into the python:3.12-slim tag, and the Trivy gate
# fails on CVEs in packages we never asked for — util-linux, most recently — with
# no change on our side to explain it. The digest pin above fixes which base we
# start from; this still applies fixes published since, at the cost of the layer
# not being byte-reproducible.
#
# libpq5 is the runtime shared lib asyncpg/psycopg need; curl is for HEALTHCHECK.
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends \
    libpq5 curl \
    && rm -rf /var/lib/apt/lists/*

# Amazon RDS root CAs, so the app can verify the database's certificate rather
# than accept whatever answers on 5432. The RDS URL asks for ssl=verify-full and
# asyncpg reads the CA bundle from PGSSLROOTCERT. The checksum pins this exact
# bundle; when AWS publishes a new one the build fails until it is bumped:
#   curl -s https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem | shasum -a 256
ADD --checksum=sha256:90a6f1a7978a1b1b096dca6c57511ca48d3b8cf052f8e704baf358f34ea2f21b \
    --chmod=0644 \
    https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem \
    /etc/ssl/certs/rds-global-bundle.pem
ENV PGSSLROOTCERT=/etc/ssl/certs/rds-global-bundle.pem

# Non-root user — Fargate and reviewers expect the container not to run as root.
RUN useradd --create-home --uid 10001 app
WORKDIR /app

# The builder already verified these against the lock's hashes; install exactly
# that set, offline, without letting pip resolve anything new.
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir --no-index --no-deps /wheels/*.whl \
    && rm -rf /wheels

COPY --chown=app:app . .

USER app

EXPOSE 8000

# Liveness probe hits the static health endpoint (no DB dependency).
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
