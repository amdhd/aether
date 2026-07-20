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

    expect(await screen.findByText(/no conversations yet/i)).toBeInTheDocument()
    expect(screen.getByText(/start a new conversation/i)).toBeInTheDocument()
  })

  it('renders conversations and loads the active conversation messages', async () => {
    vi.mocked(chatApi.listConversations).mockResolvedValue(mockConversationsPage(mockConversations))
    vi.mocked(chatApi.getConversation).mockResolvedValue(mockDetail)

    renderWithProviders(<ChatPage />)

    expect(await screen.findByRole('button', { name: 'Trip planning' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Daily standup' })).toBeInTheDocument()
    expect(await screen.findByText('Hi there')).toBeInTheDocument()
    expect(screen.getByText('Hello! How can I help?')).toBeInTheDocument()
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

    await screen.findByRole('button', { name: 'Trip planning' })

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
    await screen.findByRole('button', { name: 'Trip planning' })

    const textbox = await screen.findByLabelText('Message')
    await userEvent.type(textbox, 'Hello Aether')
    await userEvent.click(screen.getByRole('button', { name: /send message/i }))

    // Drive a streamed token through the captured handler.
    act(() => capturedHandlers?.onToken?.('Streaming answer'))

    const streamed = await screen.findByText('Streaming answer')
    expect(streamed.closest('[aria-live="polite"]')).not.toBeNull()

    resolveStream()
  })

  it('confirms before deleting a conversation', async () => {
    vi.mocked(chatApi.listConversations).mockResolvedValue(mockConversationsPage(mockConversations))
    vi.mocked(chatApi.getConversation).mockResolvedValue(mockDetail)
    vi.mocked(chatApi.deleteConversation).mockResolvedValue(undefined)

    renderWithProviders(<ChatPage />)

    await screen.findByRole('button', { name: 'Trip planning' })
    await userEvent.click(screen.getByRole('button', { name: /delete conversation trip planning/i }))

    expect(await screen.findByRole('heading', { name: /delete this conversation\?/i })).toBeInTheDocument()
    expect(chatApi.deleteConversation).not.toHaveBeenCalled()

    await userEvent.click(screen.getByRole('button', { name: /^delete$/i }))

    await waitFor(() => expect(chatApi.deleteConversation).toHaveBeenCalledWith(1))
  })
})
