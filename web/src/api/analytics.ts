import { API_PREFIX, apiFetch } from '@/lib/api'

export interface DailyMessageCount {
  date: string
  count: number
}

export interface DailyTokenUsage {
  date: string
  prompt_tokens: number
  completion_tokens: number
}

export interface ToolUsageCount {
  tool_name: string
  count: number
}

export interface AnalyticsTotals {
  conversations: number
  messages: number
  prompt_tokens: number
  completion_tokens: number
}

export interface AnalyticsSummary {
  messages_per_day: DailyMessageCount[]
  tokens_per_day: DailyTokenUsage[]
  tool_usage: ToolUsageCount[]
  totals: AnalyticsTotals
}

export function getAnalyticsSummary(days = 14) {
  return apiFetch<AnalyticsSummary>(`${API_PREFIX}/analytics/summary?days=${days}`)
}
