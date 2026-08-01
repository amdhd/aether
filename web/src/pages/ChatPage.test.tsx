import { act, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as chatApi from '@/api/chat'
import { ChatHistoryNav } from '@/components/layout/ChatHistoryNav'
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
  // Call counts accumulate across tests in a file otherwise, which makes any
  // "was this endpoint called?" assertion depend on test order.
  beforeEach(() => {
    vi.clearAllMocks()
  })

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
    expect(chatApi.streamChatMessage).toHaveBeenCalledWith(
      1,
      'Hello Aether',
      expect.any(Object),
      null,
      expect.any(AbortSignal),
    )

    resolveStream()
  })

  it('stops an in-flight reply and says the partial answer was discarded', async () => {
    vi.mocked(chatApi.listConversations).mockResolvedValue(mockConversationsPage(mockConversations))
    vi.mocked(chatApi.getConversation).mockResolvedValue({ ...mockDetail, messages: [] })

    let capturedHandlers: chatApi.ChatStreamHandlers | undefined
    // Stand in for fetch: reject as soon as the caller's signal aborts.
    vi.mocked(chatApi.streamChatMessage).mockImplementation(
      (_id, _content, handlers, _file, signal) =>
        new Promise((_resolve, reject) => {
          capturedHandlers = handlers
          signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
        }),
    )

    renderWithProviders(<ChatPage />)
    await screen.findByRole('heading', { name: 'Trip planning' })

    const textbox = await screen.findByLabelText('Message')
    await userEvent.type(textbox, 'Hello Aether')
    await userEvent.click(screen.getByRole('button', { name: /send message/i }))
    act(() => capturedHandlers?.onToken?.('Half an answ'))
    await screen.findByText('Half an answ')

    // Send is replaced by Stop for the duration of the turn.
    expect(screen.queryByRole('button', { name: /send message/i })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /stop generating/i }))

    expect(await screen.findByText(/you stopped this reply/i)).toBeInTheDocument()
    // Stopping is deliberate, so it must not read as a failure or shove the
    // message back into a composer the user has moved on from.
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(await screen.findByRole('button', { name: /send message/i })).toBeInTheDocument()
    // Re-query: the composer remounts when the view swaps between the welcome
    // and transcript branches, so the handle from before the send is detached.
    expect(screen.getByLabelText('Message')).toHaveValue('')
  })

  it('leaves the composer usable in other chats while a reply streams', async () => {
    vi.mocked(chatApi.listConversations).mockResolvedValue(mockConversationsPage(mockConversations))
    vi.mocked(chatApi.getConversation).mockImplementation(async (id) => ({
      ...(mockConversations.find((c) => c.id === id) ?? mockConversations[0]),
      messages: id === 2 ? mockDetail.messages : [],
    }))

    let resolveStream: () => void = () => {}
    vi.mocked(chatApi.streamChatMessage).mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveStream = () => resolve()
        }),
    )

    renderWithProviders(
      <>
        <ChatHistoryNav />
        <ChatPage />
      </>,
    )
    await screen.findByRole('heading', { name: 'Trip planning' })

    const textbox = await screen.findByLabelText('Message')
    await userEvent.type(textbox, 'Hello Aether')
    await userEvent.click(screen.getByRole('button', { name: /send message/i }))

    await userEvent.click(screen.getByRole('button', { name: 'Daily standup' }))
    await screen.findByRole('heading', { name: 'Daily standup' })

    // The other chat's turn must not lock this one's composer...
    const otherTextbox = screen.getByLabelText('Message')
    expect(otherTextbox).toBeEnabled()
    await userEvent.type(otherTextbox, 'Draft while busy')
    expect(otherTextbox).toHaveValue('Draft while busy')

    // ...but the server only runs one turn per user, so sending has to wait —
    // with a reason on screen rather than a dead button.
    expect(screen.getByRole('button', { name: /send message/i })).toBeDisabled()
    expect(screen.getByText(/replying in another chat/i)).toBeInTheDocument()

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

  it('does not pile up empty conversations when New chat is clicked repeatedly', async () => {
    const emptyConversation: Conversation = {
      id: 3,
      title: 'New conversation',
      persona: 'productivity_coach',
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
    }
    vi.mocked(chatApi.listConversations).mockResolvedValue(mockConversationsPage([emptyConversation]))
    vi.mocked(chatApi.getConversation).mockResolvedValue({ ...emptyConversation, messages: [] })

    renderWithProviders(<ChatPage />)
    await screen.findByText(/choose a persona, then ask about/i)

    const newChat = screen.getByRole('button', { name: /new chat/i })
    await userEvent.click(newChat)
    await userEvent.click(newChat)
    await userEvent.click(newChat)

    // The chat you're already in is empty — there is nothing to make room for.
    expect(chatApi.createConversation).not.toHaveBeenCalled()
    expect(await screen.findByLabelText('Message')).toHaveFocus()
  })

  it('creates a conversation from New chat once the current one has messages', async () => {
    vi.mocked(chatApi.listConversations).mockResolvedValue(mockConversationsPage(mockConversations))
    vi.mocked(chatApi.getConversation).mockResolvedValue(mockDetail)
    vi.mocked(chatApi.createConversation).mockResolvedValue(mockConversations[0])

    renderWithProviders(<ChatPage />)
    await screen.findByText('Hi there')

    await userEvent.click(screen.getByRole('button', { name: /new chat/i }))

    await waitFor(() => expect(chatApi.createConversation).toHaveBeenCalledTimes(1))
  })

  it('hands the message back to the composer when the send fails', async () => {
    vi.mocked(chatApi.listConversations).mockResolvedValue(mockConversationsPage(mockConversations))
    vi.mocked(chatApi.getConversation).mockResolvedValue({ ...mockDetail, messages: [] })
    vi.mocked(chatApi.streamChatMessage).mockRejectedValue(new Error('Monthly limit reached'))

    renderWithProviders(<ChatPage />)
    await screen.findByRole('heading', { name: 'Trip planning' })

    const textbox = await screen.findByLabelText('Message')
    await userEvent.type(textbox, 'Hello Aether')
    await userEvent.click(screen.getByRole('button', { name: /send message/i }))

    expect(await screen.findByText('Monthly limit reached')).toBeInTheDocument()
    // Losing what they typed is the worst outcome of a failed turn.
    await waitFor(() => expect(textbox).toHaveValue('Hello Aether'))
  })

  it('leaves a failed turn’s error in the conversation it happened in', async () => {
    vi.mocked(chatApi.listConversations).mockResolvedValue(mockConversationsPage(mockConversations))
    vi.mocked(chatApi.getConversation).mockImplementation(async (id) => ({
      ...(mockConversations.find((c) => c.id === id) ?? mockConversations[0]),
      messages: id === 2 ? mockDetail.messages : [],
    }))
    vi.mocked(chatApi.streamChatMessage).mockRejectedValue(new Error('Monthly limit reached'))

    renderWithProviders(
      <>
        <ChatHistoryNav />
        <ChatPage />
      </>,
    )
    await screen.findByRole('heading', { name: 'Trip planning' })

    await userEvent.type(screen.getByLabelText('Message'), 'Hello Aether')
    await userEvent.click(screen.getByRole('button', { name: /send message/i }))
    expect(await screen.findByText('Monthly limit reached')).toBeInTheDocument()

    // Nothing failed in this chat, so nothing should be complaining in it.
    await userEvent.click(screen.getByRole('button', { name: 'Daily standup' }))
    await screen.findByRole('heading', { name: 'Daily standup' })
    await waitFor(() => expect(screen.queryByText('Monthly limit reached')).not.toBeInTheDocument())

    // It's still waiting where it belongs when they come back to deal with it.
    await userEvent.click(screen.getByRole('button', { name: 'Trip planning' }))
    expect(await screen.findByText('Monthly limit reached')).toBeInTheDocument()
  })

  it('keeps each conversation’s draft and attachment with that conversation', async () => {
    vi.mocked(chatApi.listConversations).mockResolvedValue(mockConversationsPage(mockConversations))
    vi.mocked(chatApi.getConversation).mockImplementation(async (id) => ({
      ...(mockConversations.find((c) => c.id === id) ?? mockConversations[0]),
      messages: id === 2 ? mockDetail.messages : [],
    }))

    const { container } = renderWithProviders(
      <>
        <ChatHistoryNav />
        <ChatPage />
      </>,
    )
    await screen.findByRole('heading', { name: 'Trip planning' })

    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement
    await userEvent.type(screen.getByLabelText('Message'), 'Draft for the trip')
    await userEvent.upload(fileInput, new File(['a,b\n1,2'], 'campaign.csv', { type: 'text/csv' }))
    expect(await screen.findByText('campaign.csv')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Daily standup' }))
    await screen.findByRole('heading', { name: 'Daily standup' })

    // A different chat starts clean. The attachment especially: a stray chip
    // here would upload the other conversation's file on the next send.
    expect(screen.getByLabelText('Message')).toHaveValue('')
    expect(screen.queryByText('campaign.csv')).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Trip planning' }))
    await screen.findByRole('heading', { name: 'Trip planning' })

    expect(screen.getByLabelText('Message')).toHaveValue('Draft for the trip')
    expect(screen.getByText('campaign.csv')).toBeInTheDocument()
  })

  it('keeps a streaming reply out of a conversation the user switches to', async () => {
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

    // Conversation 2 has a transcript of its own, so switching to it renders the
    // message list — the branch an in-flight turn could leak into.
    vi.mocked(chatApi.getConversation).mockImplementation(async (id) => ({
      ...(mockConversations.find((c) => c.id === id) ?? mockConversations[0]),
      messages: id === 2 ? mockDetail.messages : [],
    }))

    // The sidebar is what drives selection, so render it alongside the page the
    // way AppShell does rather than poking the URL directly.
    renderWithProviders(
      <>
        <ChatHistoryNav />
        <ChatPage />
      </>,
    )
    await screen.findByRole('heading', { name: 'Trip planning' })

    const textbox = await screen.findByLabelText('Message')
    await userEvent.type(textbox, 'Hello Aether')
    await userEvent.click(screen.getByRole('button', { name: /send message/i }))
    act(() => capturedHandlers?.onToken?.('Streaming answer'))
    expect(await screen.findByText('Streaming answer')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Daily standup' }))
    await screen.findByRole('heading', { name: 'Daily standup' })

    // The in-flight turn belongs to conversation 1 and must not be drawn here.
    await waitFor(() => expect(screen.queryByText('Streaming answer')).not.toBeInTheDocument())
    expect(screen.queryByText('Hello Aether')).not.toBeInTheDocument()

    resolveStream()
  })
})
