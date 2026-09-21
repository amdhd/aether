import { API_PREFIX, API_URL, ApiError, apiFetch, refreshAccessToken } from '@/lib/api'
import { useAuthStore } from '@/store/auth'
import type {
  Conversation,
  ConversationCreateInput,
  ConversationDetail,
  ConversationUpdateInput,
  Page,
} from '@/types'

// Shared so the chat page and the sidebar history hit the same query key and
// therefore the same cached request.
export const CONVERSATIONS_PAGE_SIZE = 50
// Active conversation id, carried in the URL so the sidebar can drive the page.
export const CONVERSATION_PARAM = 'c'

// Mirror the server's limits so the composer can refuse a message before the
// upload rather than after it. Kept in sync with MAX_MESSAGE_CHARS in
// api/app/schemas/conversation.py and MAX_ATTACHMENT_BYTES in
// api/app/services/attachments.py — the server still enforces both.
export const MAX_MESSAGE_CHARS = 16000
export const MAX_ATTACHMENT_BYTES = 200_000

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
  /**
   * The send was a duplicate the server refused, so no reply is coming on this
   * stream — the turn it belongs to already ran. Distinct from `onDone` because
   * the caller has to reload the conversation to see that turn's result rather
   * than keep whatever it streamed locally.
   */
  onReplay?: (data: { message: string; conversation_title: string }) => void
}

/**
 * One key per send attempt, reused by every retry of that same attempt.
 *
 * That is the whole contract: the server spends a key once, so reusing one
 * across two *different* messages would silently drop the second, and minting a
 * fresh one per retry would defeat the point and re-bill the turn.
 */
function newIdempotencyKey(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return crypto.randomUUID()
  // Older Safari and any non-secure context lack randomUUID. Uniqueness per
  // user is all that is required — the server scopes keys by account.
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`
}

async function postChatMessage(
  conversationId: number,
  content: string,
  file: File | null,
  token: string | null,
  idempotencyKey: string,
  signal?: AbortSignal,
): Promise<Response> {
  const form = new FormData()
  form.append('content', content)
  if (file) form.append('file', file)
  const headers: Record<string, string> = { 'Idempotency-Key': idempotencyKey }
  if (token) headers.Authorization = `Bearer ${token}`
  // Let the browser set the multipart Content-Type (with boundary) itself.
  return fetch(`${API_URL}${API_PREFIX}/conversations/${conversationId}/messages`, {
    method: 'POST',
    headers,
    body: form,
    signal,
  })
}

function dispatchEvent(event: string, data: string, handlers: ChatStreamHandlers): void {
  if (!data) return
  // A single malformed frame (truncated/garbled SSE payload) must not throw out
  // of the read loop and abort an otherwise-healthy stream — skip it instead.
  let parsed: {
    content?: string
    name?: string
    message?: string
    conversation_title?: string
  }
  try {
    parsed = JSON.parse(data)
  } catch {
    return
  }
  switch (event) {
    case 'token':
      handlers.onToken?.(parsed.content ?? '')
      break
    case 'reasoning':
      handlers.onReasoning?.(parsed.content ?? '')
      break
    case 'tool_call':
      handlers.onToolCall?.(parsed.name ?? '')
      break
    case 'done':
      handlers.onDone?.({ conversation_title: parsed.conversation_title ?? '' })
      break
    case 'replay':
      handlers.onReplay?.({
        message: parsed.message ?? '',
        conversation_title: parsed.conversation_title ?? '',
      })
      break
    case 'error':
      handlers.onError?.(parsed.message ?? '')
      break
  }
}

/**
 * A message fit to show the user, from a failed turn's response body.
 *
 * FastAPI sends `detail` as a plain string for a raised HTTPException (the rate
 * limit, the cost cap, a missing API key) but as an *array of error objects*
 * for request-validation failures. That array used to be passed straight to
 * `new ApiError(…)`, where Error's own coercion turned it into the literal
 * "[object Object]" the chat banner then displayed.
 */
function errorMessageFor(status: number, body: unknown): string {
  const detail = (body as { detail?: unknown } | null)?.detail
  if (typeof detail === 'string' && detail) return detail
  if (status === 422) {
    return 'That message couldn’t be sent — it may be too long, or the file too large or unreadable.'
  }
  return `Something went wrong (error ${status}). Please try again.`
}

/**
 * POST a turn and consume the SSE stream, invoking `handlers` per event.
 *
 * Aborting `signal` tears down the request, which drops the connection and lets
 * the server release the user's in-flight turn slot. The abort surfaces here as
 * a rejection, so callers that stop deliberately should check
 * `signal.aborted` before treating it as a failure.
 */
export async function streamChatMessage(
  conversationId: number,
  content: string,
  handlers: ChatStreamHandlers,
  file: File | null = null,
  signal?: AbortSignal,
): Promise<void> {
  let token = useAuthStore.getState().accessToken
  // Minted once per send, so the 401 retry below re-POSTs under the same key
  // and the server sees one attempt rather than two.
  const idempotencyKey = newIdempotencyKey()
  let res = await postChatMessage(conversationId, content, file, token, idempotencyKey, signal)

  if (res.status === 401) {
    token = await refreshAccessToken()
    // A null token means the refresh cookie is gone/expired, and
    // refreshAccessToken() has already logged out — so the route guard will
    // bounce to /login. Only retry when we actually got a fresh token;
    // otherwise fall through with the original 401 instead of firing a second
    // doomed request that surfaces a confusing raw error.
    if (token) {
      res = await postChatMessage(conversationId, content, file, token, idempotencyKey, signal)
    }
  }

  if (!res.ok || !res.body) {
    let body: unknown = null
    try {
      body = await res.json()
    } catch {
      // no JSON body
    }
    throw new ApiError(res.status, body, errorMessageFor(res.status, body))
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
