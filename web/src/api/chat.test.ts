import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const { refreshMock } = vi.hoisted(() => ({ refreshMock: vi.fn() }))

vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api')
  return { ...actual, refreshAccessToken: refreshMock }
})

import { streamChatMessage } from '@/api/chat'
import { useAuthStore } from '@/store/auth'

const fetchMock = vi.fn()

beforeEach(() => {
  vi.stubGlobal('fetch', fetchMock)
  fetchMock.mockReset()
  refreshMock.mockReset()
  useAuthStore.setState({ accessToken: 'expired-token' })
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('streamChatMessage auth handling', () => {
  it('does not fire a second request when the session refresh fails', async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ detail: 'Unauthorized' }), { status: 401 }))
    refreshMock.mockResolvedValue(null) // refresh cookie gone/expired; already logged out

    await expect(streamChatMessage(1, 'hi', {})).rejects.toMatchObject({ status: 401 })

    expect(refreshMock).toHaveBeenCalledOnce()
    // Only the original request — no doomed retry with a null token.
    expect(fetchMock).toHaveBeenCalledOnce()
  })

  it('retries with the refreshed token and streams the response', async () => {
    const sseBody =
      'event: token\ndata: {"content":"Hi"}\n\nevent: done\ndata: {"conversation_title":"Trip"}\n\n'
    fetchMock
      .mockResolvedValueOnce(new Response(null, { status: 401 }))
      .mockResolvedValueOnce(
        new Response(sseBody, { status: 200, headers: { 'Content-Type': 'text/event-stream' } }),
      )
    refreshMock.mockResolvedValue('fresh-token')

    const tokens: string[] = []
    let doneTitle = ''
    await streamChatMessage(1, 'hi', {
      onToken: (chunk) => tokens.push(chunk),
      onDone: (data) => {
        doneTitle = data.conversation_title
      },
    })

    expect(fetchMock).toHaveBeenCalledTimes(2)
    const secondInit = fetchMock.mock.calls[1][1] as RequestInit
    expect((secondInit.headers as Record<string, string>).Authorization).toBe('Bearer fresh-token')
    expect(tokens.join('')).toBe('Hi')
    expect(doneTitle).toBe('Trip')
  })
})
