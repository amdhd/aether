from functools import lru_cache

import httpx
from openai import AsyncOpenAI

from app.core.config import settings


@lru_cache
def get_deepseek_client() -> AsyncOpenAI:
    # Bound every LLM call: the SDK default is a 600s timeout, so a hung DeepSeek
    # socket would otherwise pin a worker/connection for ten minutes. A short
    # connect timeout fails fast on network trouble; the read timeout allows for
    # slow token generation on longer completions. max_retries handles transient
    # provider 429/5xx with the SDK's built-in exponential backoff.
    return AsyncOpenAI(
        api_key=settings.DEEPSEEK_API_KEY,
        base_url=settings.DEEPSEEK_BASE_URL,
        timeout=httpx.Timeout(60.0, connect=5.0),
        max_retries=2,
    )
