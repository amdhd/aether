import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
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

  it('offers a retry when analytics fail, rather than a dead sentence', async () => {
    const summary: AnalyticsSummary = {
      messages_per_day: [{ date: '2026-06-01', count: 5 }],
      tokens_per_day: [{ date: '2026-06-01', prompt_tokens: 100, completion_tokens: 50 }],
      tool_usage: [{ tool_name: 'create_task', count: 3 }],
      totals: { conversations: 2, messages: 10, prompt_tokens: 1000, completion_tokens: 500 },
    }
    vi.mocked(analyticsApi.getAnalyticsSummary)
      .mockRejectedValueOnce(new Error('network error'))
      .mockResolvedValueOnce(summary)

    renderWithProviders(<AnalyticsPage />)

    await screen.findByText(/failed to load analytics/i)
    await userEvent.click(screen.getByRole('button', { name: /try again/i }))

    // The recovery is the point: the retry has to actually re-fetch and render.
    expect(await screen.findByText('Messages sent')).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByText(/failed to load analytics/i)).not.toBeInTheDocument())
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

  it('says which period each block covers', async () => {
    vi.mocked(analyticsApi.getAnalyticsSummary).mockResolvedValue({
      messages_per_day: [{ date: '2026-06-01', count: 5 }],
      tokens_per_day: [{ date: '2026-06-01', prompt_tokens: 100, completion_tokens: 50 }],
      tool_usage: [{ tool_name: 'create_task', count: 3 }],
      totals: { conversations: 2, messages: 10, prompt_tokens: 1000, completion_tokens: 500 },
    })

    renderWithProviders(<AnalyticsPage />)

    // The totals are lifetime figures sitting above windowed charts; unlabelled
    // they read as part of the same 14 days.
    expect(await screen.findByRole('heading', { name: 'All time' })).toBeInTheDocument()
    // Every windowed block names the window, tool usage included — it used to be
    // an all-time tally presented alongside "Last 14 days".
    expect(screen.getAllByText(/last 14 days/i)).toHaveLength(3)
  })

  it('requests tool usage over the same window it labels', async () => {
    vi.mocked(analyticsApi.getAnalyticsSummary).mockResolvedValue(emptySummary)

    renderWithProviders(<AnalyticsPage />)

    await screen.findByText(/no activity yet/i)
    expect(analyticsApi.getAnalyticsSummary).toHaveBeenCalledWith(14)
  })

  it('labels tool usage in plain language rather than raw tool names', async () => {
    vi.mocked(analyticsApi.getAnalyticsSummary).mockResolvedValue({
      messages_per_day: [{ date: '2026-06-01', count: 5 }],
      tokens_per_day: [{ date: '2026-06-01', prompt_tokens: 100, completion_tokens: 50 }],
      tool_usage: [{ tool_name: 'list_tasks', count: 1 }],
      totals: { conversations: 2, messages: 10, prompt_tokens: 1000, completion_tokens: 500 },
    })

    renderWithProviders(<AnalyticsPage />)

    expect(await screen.findByText('What Aether did for you')).toBeInTheDocument()
    expect(screen.queryByText(/list_tasks/)).not.toBeInTheDocument()
  })
})
