from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.router import api_router
from app.core.config import settings
from app.core.logging import configure_logging
from app.db.session import get_db

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

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.FRONTEND_ORIGIN.split(",") if origin.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix=settings.API_V1_PREFIX)


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
