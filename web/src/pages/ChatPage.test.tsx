import { act, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import * as chatApi from '@/api/chat'
import { renderWithProviders } from '@/test/utils'
import type { Conversation, ConversationDetail, Page } from '@/types'

import { ChatPage } from './ChatPage'

vi.mock('@/api/chat')

const mockConversations: Conversation[] = [
  {
    id: 1,
    title: 'Trip planning',
    persona: 'research_assistant',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  },
  {
    id: 2,
    title: 'Daily standup',
    persona: 'productivity_coach',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  },
]

function mockConversationsPage(items: Conversation[]): Page<Conversation> {
  return { items, total: items.length, limit: 50, offset: 0 }
}

const mockDetail: ConversationDetail = {
  ...mockConversations[0],
  messages: [
    {
      id: 1,
      role: 'user',
      content: 'Hi there',
      reasoning_content: null,
      tool_calls: null,
      tool_name: null,
      attachment_name: null,
      created_at: '2026-01-01T00:00:00Z',
    },
    {
      id: 2,
      role: 'assistant',
      content: 'Hello! How can I help?',
      reasoning_content: null,
      tool_calls: null,
      tool_name: null,
      attachment_name: null,
      created_at: '2026-01-01T00:00:00Z',
    },
  ],
}

describe('ChatPage', () => {
  it('shows an empty state when there are no conversations', async () => {
    vi.mocked(chatApi.listConversations).mockResolvedValue(mockConversationsPage([]))

    renderWithProviders(<ChatPage />)

    expect(await screen.findByText(/start chatting with aether/i)).toBeInTheDocument()
    expect(screen.getByText(/choose a persona to start a new conversation/i)).toBeInTheDocument()
  })

  it('renders conversations and loads the active conversation messages', async () => {
    vi.mocked(chatApi.listConversations).mockResolvedValue(mockConversationsPage(mockConversations))
    vi.mocked(chatApi.getConversation).mockResolvedValue(mockDetail)

    renderWithProviders(<ChatPage />)

    // The header names the active conversation and loads its messages. The
    // history list itself lives in the sidebar (see ChatHistoryNav).
    expect(await screen.findByRole('heading', { name: 'Trip planning' })).toBeInTheDocument()
    expect(await screen.findByText('Hi there')).toBeInTheDocument()
    expect(screen.getByText('Hello! How can I help?')).toBeInTheDocument()
  })

  it('switches persona from the empty-conversation picker', async () => {
    const emptyConversation: Conversation = {
      id: 3,
      title: 'New conversation',
      persona: 'productivity_coach',
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
    }
    vi.mocked(chatApi.listConversations).mockResolvedValue(mockConversationsPage([emptyConversation]))
    vi.mocked(chatApi.getConversation).mockResolvedValue({ ...emptyConversation, messages: [] })
    vi.mocked(chatApi.updateConversation).mockImplementation(async (_id, input) => {
      const updated = { ...emptyConversation, ...input } as Conversation
      vi.mocked(chatApi.getConversation).mockResolvedValue({ ...updated, messages: [] })
      vi.mocked(chatApi.listConversations).mockResolvedValue(mockConversationsPage([updated]))
      return updated
    })

    renderWithProviders(<ChatPage />)

    await screen.findByText(/choose a persona, then ask about/i)
    // Re-query each time: the picker remounts as the conversation query settles.
    const casual = () => screen.getByRole('button', { name: /^casual$/i })
    await waitFor(() => expect(casual()).toHaveAttribute('aria-pressed', 'false'))

    await userEvent.click(casual())

    await waitFor(() =>
      expect(chatApi.updateConversation).toHaveBeenCalledWith(3, { persona: 'casual_friend' }),
    )
    await waitFor(() => expect(casual()).toHaveAttribute('aria-pressed', 'true'))
  })

  it('surfaces an error instead of a dead persona picker when the detail fetch fails', async () => {
    vi.mocked(chatApi.listConversations).mockResolvedValue(mockConversationsPage(mockConversations))
    vi.mocked(chatApi.getConversation).mockRejectedValue(new Error('boom'))

    renderWithProviders(<ChatPage />)

    expect(await screen.findByText(/couldn’t load this conversation/i)).toBeInTheDocument()
    // The welcome screen must not stand in for a failed load — its picker has
    // no conversation behind it, so every click would be silently dropped.
    expect(screen.queryByRole('button', { name: /^casual$/i })).not.toBeInTheDocument()
  })

  it('sends a message and shows it optimistically while streaming', async () => {
    vi.mocked(chatApi.listConversations).mockResolvedValue(mockConversationsPage(mockConversations))
    vi.mocked(chatApi.getConversation).mockResolvedValue({ ...mockDetail, messages: [] })

    let resolveStream: () => void = () => {}
    vi.mocked(chatApi.streamChatMessage).mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveStream = () => resolve()
        }),
    )

    renderWithProviders(<ChatPage />)

    await screen.findByRole('heading', { name: 'Trip planning' })

    const textbox = await screen.findByLabelText('Message')
    await userEvent.type(textbox, 'Hello Aether')
    await userEvent.click(screen.getByRole('button', { name: /send message/i }))

    expect(await screen.findByText('Hello Aether')).toBeInTheDocument()
    expect(chatApi.streamChatMessage).toHaveBeenCalledWith(1, 'Hello Aether', expect.any(Object), null)

    resolveStream()
  })

  it('exposes streamed assistant output in a polite live region', async () => {
    vi.mocked(chatApi.listConversations).mockResolvedValue(mockConversationsPage(mockConversations))
    vi.mocked(chatApi.getConversation).mockResolvedValue({ ...mockDetail, messages: [] })

    let capturedHandlers: chatApi.ChatStreamHandlers | undefined
    let resolveStream: () => void = () => {}
    vi.mocked(chatApi.streamChatMessage).mockImplementation(
      (_id, _content, handlers) =>
        new Promise((resolve) => {
          capturedHandlers = handlers
          resolveStream = () => resolve()
        }),
    )

    renderWithProviders(<ChatPage />)
    await screen.findByRole('heading', { name: 'Trip planning' })

    const textbox = await screen.findByLabelText('Message')
    await userEvent.type(textbox, 'Hello Aether')
    await userEvent.click(screen.getByRole('button', { name: /send message/i }))

    // Drive a streamed token through the captured handler.
    act(() => capturedHandlers?.onToken?.('Streaming answer'))

    const streamed = await screen.findByText('Streaming answer')
    expect(streamed.closest('[aria-live="polite"]')).not.toBeNull()

    resolveStream()
  })

})
