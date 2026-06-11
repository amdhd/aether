from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # App
    PROJECT_NAME: str = "Aether"
    API_V1_PREFIX: str = "/api/v1"

    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./aether.db"

    # Auth / JWT
    SECRET_KEY: str = "dev-secret-key-change-me"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # Encryption (Fernet key for OAuth tokens at rest)
    ENCRYPTION_KEY: str = ""

    # CORS
    FRONTEND_ORIGIN: str = "http://localhost:5173"

    # LLM (DeepSeek)
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com"
    DEEPSEEK_MODEL: str = "deepseek-chat"

    # Tools
    TAVILY_API_KEY: str = ""
    OPENWEATHER_API_KEY: str = ""

    # Google Calendar OAuth
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GOOGLE_REDIRECT_URI: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
