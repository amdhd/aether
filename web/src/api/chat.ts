import { API_PREFIX, API_URL, ApiError, apiFetch, refreshAccessToken } from '@/lib/api'
import { useAuthStore } from '@/store/auth'
import type {
  Conversation,
  ConversationCreateInput,
  ConversationDetail,
  ConversationUpdateInput,
  Page,
} from '@/types'

export function listConversations(limit?: number, offset?: number) {
  const params = new URLSearchParams()
  if (limit !== undefined) params.set('limit', String(limit))
  if (offset !== undefined) params.set('offset', String(offset))
  const query = params.toString() ? `?${params.toString()}` : ''
  return apiFetch<Page<Conversation>>(`${API_PREFIX}/conversations${query}`)
}

export function createConversation(input: ConversationCreateInput = {}) {
  return apiFetch<Conversation>(`${API_PREFIX}/conversations`, {
    method: 'POST',
    body: input,
  })
}

export function getConversation(id: number) {
  return apiFetch<ConversationDetail>(`${API_PREFIX}/conversations/${id}`)
}

export function updateConversation(id: number, input: ConversationUpdateInput) {
  return apiFetch<Conversation>(`${API_PREFIX}/conversations/${id}`, {
    method: 'PUT',
    body: input,
  })
}

export function deleteConversation(id: number) {
  return apiFetch<void>(`${API_PREFIX}/conversations/${id}`, {
    method: 'DELETE',
  })
}

export interface ChatStreamHandlers {
  onToken?: (content: string) => void
  onReasoning?: (content: string) => void
  onToolCall?: (name: string) => void
  onDone?: (data: { conversation_title: string }) => void
  onError?: (message: string) => void
}

async function postChatMessage(conversationId: number, content: string, token: string | null): Promise<Response> {
  return fetch(`${API_URL}${API_PREFIX}/conversations/${conversationId}/messages`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({ content }),
  })
}

function dispatchEvent(event: string, data: string, handlers: ChatStreamHandlers): void {
  if (!data) return
  const parsed = JSON.parse(data)
  switch (event) {
    case 'token':
      handlers.onToken?.(parsed.content)
      break
    case 'reasoning':
      handlers.onReasoning?.(parsed.content)
      break
    case 'tool_call':
      handlers.onToolCall?.(parsed.name)
      break
    case 'done':
      handlers.onDone?.(parsed)
      break
    case 'error':
      handlers.onError?.(parsed.message)
      break
  }
}

export async function streamChatMessage(
  conversationId: number,
  content: string,
  handlers: ChatStreamHandlers,
): Promise<void> {
  let token = useAuthStore.getState().accessToken
  let res = await postChatMessage(conversationId, content, token)

  if (res.status === 401) {
    token = await refreshAccessToken()
    res = await postChatMessage(conversationId, content, token)
  }

  if (!res.ok || !res.body) {
    let message = `Request failed with status ${res.status}`
    try {
      const errorBody = await res.json()
      message = errorBody.detail ?? message
    } catch {
      // no JSON body
    }
    throw new ApiError(res.status, null, message)
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    let separatorIndex: number
    while ((separatorIndex = buffer.indexOf('\n\n')) !== -1) {
      const rawEvent = buffer.slice(0, separatorIndex)
      buffer = buffer.slice(separatorIndex + 2)

      let event = 'message'
      let data = ''
      for (const line of rawEvent.split('\n')) {
        if (line.startsWith('event:')) {
          event = line.slice('event:'.length).trim()
        } else if (line.startsWith('data:')) {
          data += line.slice('data:'.length).trim()
        }
      }
      dispatchEvent(event, data, handlers)
    }
  }
}
