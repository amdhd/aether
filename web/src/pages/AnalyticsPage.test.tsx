import { screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import * as analyticsApi from '@/api/analytics'
import type { AnalyticsSummary } from '@/api/analytics'
import { renderWithProviders } from '@/test/utils'

import { AnalyticsPage } from './AnalyticsPage'

vi.mock('@/api/analytics')

const emptySummary: AnalyticsSummary = {
  messages_per_day: [],
  tokens_per_day: [],
  tool_usage: [],
  totals: { conversations: 0, messages: 0, prompt_tokens: 0, completion_tokens: 0 },
}

describe('AnalyticsPage', () => {
  it('shows an error message if analytics fail to load', async () => {
    vi.mocked(analyticsApi.getAnalyticsSummary).mockRejectedValue(new Error('network error'))

    renderWithProviders(<AnalyticsPage />)

    expect(await screen.findByText(/failed to load analytics/i)).toBeInTheDocument()
  })

  it('shows an empty state when there is no activity yet', async () => {
    vi.mocked(analyticsApi.getAnalyticsSummary).mockResolvedValue(emptySummary)

    renderWithProviders(<AnalyticsPage />)

    expect(await screen.findByText(/no activity yet/i)).toBeInTheDocument()
  })

  it('renders summary stats when there is activity', async () => {
    vi.mocked(analyticsApi.getAnalyticsSummary).mockResolvedValue({
      messages_per_day: [{ date: '2026-06-01', count: 5 }],
      tokens_per_day: [{ date: '2026-06-01', prompt_tokens: 100, completion_tokens: 50 }],
      tool_usage: [{ tool_name: 'create_task', count: 3 }],
      totals: { conversations: 2, messages: 10, prompt_tokens: 1000, completion_tokens: 500 },
    })

    renderWithProviders(<AnalyticsPage />)

    expect(await screen.findByText('Conversations')).toBeInTheDocument()
    expect(screen.getByText('2')).toBeInTheDocument()
    expect(screen.getByText('Messages sent')).toBeInTheDocument()
    expect(screen.getByText('10')).toBeInTheDocument()
  })
})
