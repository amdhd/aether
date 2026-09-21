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

  it('skips a malformed frame instead of aborting the rest of the stream', async () => {
    const sseBody =
      'event: token\ndata: {"content":"Hel"}\n\n' +
      'event: token\ndata: {broken json\n\n' + // malformed — must be skipped, not fatal
      'event: token\ndata: {"content":"lo"}\n\n' +
      'event: done\ndata: {"conversation_title":"Trip"}\n\n'
    fetchMock.mockResolvedValue(
      new Response(sseBody, { status: 200, headers: { 'Content-Type': 'text/event-stream' } }),
    )

    const tokens: string[] = []
    let doneTitle = ''
    await streamChatMessage(1, 'hi', {
      onToken: (chunk) => tokens.push(chunk),
      onDone: (data) => {
        doneTitle = data.conversation_title
      },
    })

    // The good frames on both sides of the malformed one still arrived.
    expect(tokens.join('')).toBe('Hello')
    expect(doneTitle).toBe('Trip')
  })
})

describe('streamChatMessage error messages', () => {
  it('passes a string detail through as-is', async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ detail: 'Another reply is still in progress.' }), { status: 429 }),
    )

    await expect(streamChatMessage(1, 'hi', {})).rejects.toThrow('Another reply is still in progress.')
  })

  it('never surfaces FastAPI’s validation array as "[object Object]"', async () => {
    // What the server actually sends when the message exceeds MAX_MESSAGE_CHARS.
    const body = {
      detail: [{ type: 'string_too_long', loc: ['body', 'content'], msg: 'String should have at most 16000 characters' }],
    }
    fetchMock.mockResolvedValue(new Response(JSON.stringify(body), { status: 422 }))

    await expect(streamChatMessage(1, 'hi', {})).rejects.toThrow(/couldn’t be sent/)
    // Error's own coercion of that array is what used to reach the chat banner.
    await expect(streamChatMessage(1, 'hi', {})).rejects.not.toThrow(/object Object/)
  })

  it('falls back to a readable message when there is no JSON body', async () => {
    fetchMock.mockResolvedValue(new Response('<html>502</html>', { status: 502 }))

    await expect(streamChatMessage(1, 'hi', {})).rejects.toThrow('Something went wrong (error 502). Please try again.')
  })
})

describe('streamChatMessage idempotency', () => {
  const headersOf = (call: number) =>
    (fetchMock.mock.calls[call][1] as RequestInit).headers as Record<string, string>

  it('reuses one key across the 401 refresh retry', async () => {
    // The retry re-POSTs the same body. Minting a second key there would make
    // the server treat one send as two attempts, which is the duplicate the
    // header exists to prevent.
    fetchMock
      .mockResolvedValueOnce(new Response(null, { status: 401 }))
      .mockResolvedValueOnce(
        new Response('event: done\ndata: {"conversation_title":"T"}\n\n', {
          status: 200,
          headers: { 'Content-Type': 'text/event-stream' },
        }),
      )
    refreshMock.mockResolvedValue('fresh-token')

    await streamChatMessage(1, 'hi', {})

    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(headersOf(0)['Idempotency-Key']).toBeTruthy()
    expect(headersOf(1)['Idempotency-Key']).toBe(headersOf(0)['Idempotency-Key'])
  })

  it('mints a different key for a different send', async () => {
    // Reusing one across two real messages would have the server silently drop
    // the second — the opposite failure.
    const ok = () =>
      new Response('event: done\ndata: {"conversation_title":"T"}\n\n', {
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
      })
    fetchMock.mockResolvedValueOnce(ok()).mockResolvedValueOnce(ok())
    useAuthStore.setState({ accessToken: 'good-token' })

    await streamChatMessage(1, 'one', {})
    await streamChatMessage(1, 'two', {})

    expect(headersOf(1)['Idempotency-Key']).not.toBe(headersOf(0)['Idempotency-Key'])
  })

  it('reports a refused duplicate as a replay rather than an empty answer', async () => {
    fetchMock.mockResolvedValue(
      new Response(
        'event: replay\ndata: {"message":"That message was already sent.","conversation_title":"T"}\n\n' +
          'event: done\ndata: {"conversation_title":"T"}\n\n',
        { status: 200, headers: { 'Content-Type': 'text/event-stream' } },
      ),
    )
    useAuthStore.setState({ accessToken: 'good-token' })

    let replayed = ''
    const tokens: string[] = []
    await streamChatMessage(1, 'hi', {
      onReplay: (data) => {
        replayed = data.message
      },
      onToken: (chunk) => tokens.push(chunk),
    })

    expect(replayed).toBe('That message was already sent.')
    // Nothing streamed, which is why the caller has to reload rather than keep
    // what it has on screen.
    expect(tokens).toEqual([])
  })
})
