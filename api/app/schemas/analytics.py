from pydantic import BaseModel


class DailyMessageCount(BaseModel):
    date: str
    count: int


class DailyTokenUsage(BaseModel):
    date: str
    prompt_tokens: int
    completion_tokens: int


class ToolUsageCount(BaseModel):
    tool_name: str
    count: int


class AnalyticsTotals(BaseModel):
    conversations: int
    messages: int
    prompt_tokens: int
    completion_tokens: int


class AnalyticsSummary(BaseModel):
    messages_per_day: list[DailyMessageCount]
    tokens_per_day: list[DailyTokenUsage]
    tool_usage: list[ToolUsageCount]
    totals: AnalyticsTotals
