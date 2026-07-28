import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import * as chatApi from '@/api/chat'
import { renderWithProviders } from '@/test/utils'
import type { Conversation, Page } from '@/types'

import { ChatHistoryNav } from './ChatHistoryNav'

vi.mock('@/api/chat', async (importOriginal) => ({
  ...(await importOriginal<typeof chatApi>()),
  listConversations: vi.fn(),
  deleteConversation: vi.fn(),
}))

function conversation(id: number, title: string): Conversation {
  return {
    id,
    title,
    persona: 'productivity_coach',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  }
}

function conversationsPage(items: Conversation[]): Page<Conversation> {
  return { items, total: items.length, limit: 50, offset: 0 }
}

describe('ChatHistoryNav', () => {
  it('shows only the five most recent chats', async () => {
    const items = [1, 2, 3, 4, 5, 6, 7].map((n) => conversation(n, `Chat ${n}`))
    vi.mocked(chatApi.listConversations).mockResolvedValue(conversationsPage(items))

    renderWithProviders(<ChatHistoryNav />, { route: '/chat' })

    expect(await screen.findByRole('button', { name: 'Chat 1' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Chat 5' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Chat 6' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Chat 7' })).not.toBeInTheDocument()
  })

  it('collapses and expands the list', async () => {
    vi.mocked(chatApi.listConversations).mockResolvedValue(
      conversationsPage([conversation(1, 'Trip planning')]),
    )

    renderWithProviders(<ChatHistoryNav />, { route: '/chat' })

    await screen.findByRole('button', { name: 'Trip planning' })
    await userEvent.click(screen.getByRole('button', { name: /recent chats/i }))
    expect(screen.queryByRole('button', { name: 'Trip planning' })).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /recent chats/i }))
    expect(await screen.findByRole('button', { name: 'Trip planning' })).toBeInTheDocument()
  })

  it('confirms before deleting a conversation', async () => {
    vi.mocked(chatApi.listConversations).mockResolvedValue(
      conversationsPage([conversation(1, 'Trip planning')]),
    )
    vi.mocked(chatApi.deleteConversation).mockResolvedValue(undefined)

    renderWithProviders(<ChatHistoryNav />, { route: '/chat' })

    await screen.findByRole('button', { name: 'Trip planning' })
    await userEvent.click(screen.getByRole('button', { name: /delete conversation trip planning/i }))

    expect(await screen.findByRole('heading', { name: /delete this conversation\?/i })).toBeInTheDocument()
    expect(chatApi.deleteConversation).not.toHaveBeenCalled()

    await userEvent.click(screen.getByRole('button', { name: /^delete$/i }))

    await waitFor(() => expect(chatApi.deleteConversation).toHaveBeenCalledWith(1))
  })
})
