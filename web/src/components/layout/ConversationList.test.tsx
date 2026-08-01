import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { toast } from 'sonner'

import * as chatApi from '@/api/chat'
import { renderWithProviders } from '@/test/utils'
import type { Conversation, Page } from '@/types'

import { ConversationList } from './ConversationList'

vi.mock('@/api/chat', async (importOriginal) => ({
  ...(await importOriginal<typeof chatApi>()),
  listConversations: vi.fn(),
  deleteConversation: vi.fn(),
}))

// The <Toaster> lives in the app shell, not in these renders, so watch the call.
vi.mock('sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))

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

describe('ConversationList', () => {
  it('shows every conversation when no limit is set', async () => {
    const items = [1, 2, 3, 4, 5, 6, 7].map((n) => conversation(n, `Chat ${n}`))
    vi.mocked(chatApi.listConversations).mockResolvedValue(conversationsPage(items))

    // How the chat page's small-screen history renders it: the sidebar's
    // five-item peek makes no sense in a sheet opened to go find something.
    renderWithProviders(<ConversationList />, { route: '/chat' })

    expect(await screen.findByRole('button', { name: 'Chat 1' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Chat 7' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /show all/i })).not.toBeInTheDocument()
  })

  it('says so when a delete fails instead of leaving the dialog sitting there', async () => {
    vi.mocked(chatApi.listConversations).mockResolvedValue(
      conversationsPage([conversation(1, 'Trip planning')]),
    )
    vi.mocked(chatApi.deleteConversation).mockRejectedValue(new Error('network error'))

    renderWithProviders(<ConversationList />, { route: '/chat' })

    await screen.findByRole('button', { name: 'Trip planning' })
    await userEvent.click(screen.getByRole('button', { name: /delete conversation trip planning/i }))
    await userEvent.click(await screen.findByRole('button', { name: /^delete$/i }))

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith("Couldn't delete conversation. Please try again."),
    )
    // A dialog reset to its resting state is indistinguishable from one that
    // never registered the click.
    await waitFor(() =>
      expect(screen.queryByRole('heading', { name: /delete this conversation\?/i })).not.toBeInTheDocument(),
    )
    // The conversation survived, so it must still be listed.
    expect(screen.getByRole('button', { name: 'Trip planning' })).toBeInTheDocument()
  })

  it('reports the picked conversation to its container', async () => {
    vi.mocked(chatApi.listConversations).mockResolvedValue(
      conversationsPage([conversation(1, 'Trip planning')]),
    )
    const onSelect = vi.fn()

    renderWithProviders(<ConversationList onSelect={onSelect} />, { route: '/chat' })

    await userEvent.click(await screen.findByRole('button', { name: 'Trip planning' }))

    expect(onSelect).toHaveBeenCalledOnce()
  })
})
