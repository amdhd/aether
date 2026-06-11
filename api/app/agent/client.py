from functools import lru_cache

from openai import AsyncOpenAI

from app.core.config import settings


@lru_cache
def get_deepseek_client() -> AsyncOpenAI:
    return AsyncOpenAI(api_key=settings.DEEPSEEK_API_KEY, base_url=settings.DEEPSEEK_BASE_URL)
