from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.router import api_router
from app.core.body_limit import MaxBodySizeMiddleware
from app.core.config import settings
from app.core.logging import configure_logging
from app.core.security_headers import SecurityHeadersMiddleware
from app.core.tracing import configure_tracing
from app.db.session import engine, get_db

configure_logging()

# Interactive API docs (/docs, /redoc, /openapi.json) are useful in dev but leak
# the full API surface publicly, so turn them off in production.
_docs_enabled = settings.ENVIRONMENT != "production"

app = FastAPI(
    title=settings.PROJECT_NAME,
    docs_url="/docs" if _docs_enabled else None,
    redoc_url="/redoc" if _docs_enabled else None,
    openapi_url="/openapi.json" if _docs_enabled else None,
)

# Added before CORS so that CORS ends up the outer layer and decorates the 413
# too — otherwise a browser sees an opaque CORS failure instead of the status.
app.add_middleware(MaxBodySizeMiddleware, max_bytes=settings.MAX_REQUEST_BYTES)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.FRONTEND_ORIGIN.split(",") if origin.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Added last, so it is the outermost layer and decorates every response —
# including the body limit's 413 and CORS's preflight replies, neither of which
# reaches a route handler.
#
# no-store covers /auth: those response bodies carry access tokens, and the
# refresh endpoints set the session cookie. Nothing declares them uncacheable
# otherwise, and "no explicit headers" is not the same as "no cache will store
# it". The CSP is suppressed wherever the docs are served, because Swagger UI is
# a real HTML page that loads its own scripts and styles.
app.add_middleware(
    SecurityHeadersMiddleware,
    no_store_prefixes=(f"{settings.API_V1_PREFIX}/auth",),
    send_csp=not _docs_enabled,
)

app.include_router(api_router, prefix=settings.API_V1_PREFIX)

# Auto-instrument HTTP → DB → LLM once routes are registered. No-op unless
# TRACING_ENABLED, so dev/tests are unaffected.
configure_tracing(app, engine)


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness: the process is up and serving. No dependencies checked, so the
    load balancer / orchestrator won't cycle the task on a transient DB blip."""
    return {"status": "ok"}


@app.get("/health/ready")
async def readiness(db: AsyncSession = Depends(get_db)) -> dict[str, str]:
    """Readiness: the task can actually serve traffic — verifies DB connectivity.
    Deploys/target groups can use this to gate a task into rotation."""
    await db.execute(text("SELECT 1"))
    return {"status": "ready"}
