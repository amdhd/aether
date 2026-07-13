from functools import lru_cache

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

INSECURE_SECRET_KEY = "dev-secret-key-change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # App
    PROJECT_NAME: str = "Aether"
    API_V1_PREFIX: str = "/api/v1"
    # Set to "production" in deployed environments to enable strict checks
    # (e.g. rejecting default/missing secrets) that would otherwise break
    # local dev and CI, where these values are intentionally left unset.
    ENVIRONMENT: str = "development"

    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./aether.db"

    @field_validator("DATABASE_URL")
    @classmethod
    def _use_asyncpg_driver(cls, value: str) -> str:
        # Managed Postgres providers (Render, Heroku, etc.) hand out plain
        # postgres:// / postgresql:// URLs, but SQLAlchemy's async engine
        # needs the asyncpg driver scheme.
        if value.startswith("postgres://"):
            return "postgresql+asyncpg://" + value[len("postgres://") :]
        if value.startswith("postgresql://"):
            return "postgresql+asyncpg://" + value[len("postgresql://") :]
        return value

    # Auth / JWT
    SECRET_KEY: str = INSECURE_SECRET_KEY
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # Refresh-token cookie. The refresh token is delivered as an HttpOnly cookie
    # (never readable by JS) instead of a response body, so XSS can't exfiltrate
    # it. In production the frontend and API often live on different domains
    # (e.g. Vercel + Render), which makes this a cross-site cookie: set
    # REFRESH_COOKIE_SAMESITE=none and REFRESH_COOKIE_SECURE=true there.
    REFRESH_COOKIE_NAME: str = "refresh_token"
    REFRESH_COOKIE_SECURE: bool = False
    REFRESH_COOKIE_SAMESITE: str = "lax"  # "lax" for same-site dev, "none" cross-site prod
    REFRESH_COOKIE_DOMAIN: str = ""  # e.g. ".example.com" to share across subdomains

    # Encryption (Fernet key for OAuth tokens at rest)
    ENCRYPTION_KEY: str = ""

    # CORS
    FRONTEND_ORIGIN: str = "http://localhost:5173"

    # LLM (DeepSeek)
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com"
    DEEPSEEK_MODEL: str = "deepseek-v4-flash"

    # Rate limiting
    CHAT_RATE_LIMIT_PER_MINUTE: int = 20
    WEB_SEARCH_RATE_LIMIT_PER_MINUTE: int = 10
    CALENDAR_RATE_LIMIT_PER_MINUTE: int = 20
    AUTH_RATE_LIMIT_PER_MINUTE: int = 10
    # Behind a trusted reverse proxy (Render, Vercel, nginx) the socket peer is
    # the proxy, so per-IP auth rate limiting must read the client IP from the
    # X-Forwarded-For header instead. Only enable this when a proxy you control
    # sets that header — otherwise clients can spoof it. Left off in local dev.
    TRUST_PROXY_HEADERS: bool = False

    # Tools
    TAVILY_API_KEY: str = ""

    # Embeddings (semantic note search / RAG). Optional: when OPENAI_API_KEY is
    # unset, note embeddings are skipped and semantic search falls back to a
    # keyword scan, so local dev and CI keep working without a key.
    OPENAI_API_KEY: str = ""
    EMBEDDING_MODEL: str = "text-embedding-3-small"
    EMBEDDING_DIMENSIONS: int = 1536
    # Relevance floor for semantic note search: notes whose cosine distance to
    # the query exceeds this are treated as unrelated and dropped, so the agent
    # isn't fed near-random notes when nothing actually matches. Range 0..2;
    # ~0.6 keeps clearly-related notes for text-embedding-3-small. Tunable.
    NOTE_SEARCH_MAX_DISTANCE: float = 0.6

    # Google Calendar OAuth
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GOOGLE_REDIRECT_URI: str = ""
    GOOGLE_OAUTH_SCOPES: str = "https://www.googleapis.com/auth/calendar"
    OAUTH_STATE_EXPIRE_MINUTES: int = 5

    @model_validator(mode="after")
    def _require_real_secrets_in_production(self) -> "Settings":
        if self.ENVIRONMENT == "production":
            if self.SECRET_KEY == INSECURE_SECRET_KEY:
                raise ValueError("SECRET_KEY must be set to a real secret when ENVIRONMENT=production")
            if not self.ENCRYPTION_KEY:
                raise ValueError("ENCRYPTION_KEY must be set when ENVIRONMENT=production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
