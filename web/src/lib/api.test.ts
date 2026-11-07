import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { refreshAccessToken } from './api'
import { useAuthStore } from '@/store/auth'

beforeEach(() => useAuthStore.setState({ accessToken: 'existing', user: { id: 1 } as never }))
afterEach(() => { vi.unstubAllGlobals(); useAuthStore.getState().logout() })

it.each([429, 500, 503, 403])('preserves identity after refresh status %s', async (status) => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status, headers: { 'Retry-After': '60' } })))
  expect(await refreshAccessToken()).toBeNull()
  expect(useAuthStore.getState().accessToken).toBe('existing')
  expect(useAuthStore.getState().user?.id).toBe(1)
})
it('preserves identity and does not replay an ambiguous network failure', async () => {
  const fetch = vi.fn().mockRejectedValue(new TypeError('offline'))
  vi.stubGlobal('fetch', fetch)
  expect(await refreshAccessToken()).toBeNull()
  expect(fetch).toHaveBeenCalledTimes(1)
  expect(useAuthStore.getState().accessToken).toBe('existing')
})
it('clears identity only after authentication rejection', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status: 401 })))
  expect(await refreshAccessToken()).toBeNull()
  expect(useAuthStore.getState().accessToken).toBeNull()
  expect(useAuthStore.getState().user).toBeNull()
})
it('retries a short pre-rotation rate limit once and recovers', async () => {
  const fetch = vi.fn().mockResolvedValueOnce(new Response(null, { status: 429, headers: { 'Retry-After': '0' } }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ access_token: 'new' }), { status: 200 }))
  vi.stubGlobal('fetch', fetch)
  expect(await refreshAccessToken()).toBe('new')
  expect(fetch).toHaveBeenCalledTimes(2)
})

it('serialises refreshes across tabs so the shared cookie is never sent twice', async () => {
  // A Web Locks stand-in shared by two "tabs" (two copies of the module).
  let held: Promise<unknown> = Promise.resolve()
  const names: string[] = []
  vi.stubGlobal('navigator', {
    locks: {
      request: (name: string, work: () => Promise<unknown>) => {
        names.push(name)
        const run = held.then(work)
        held = run.catch(() => undefined)
        return run
      },
    },
  })

  let inFlight = 0
  let maxInFlight = 0
  let issued = 0
  vi.stubGlobal('fetch', vi.fn(async () => {
    inFlight += 1
    maxInFlight = Math.max(maxInFlight, inFlight)
    await new Promise((resolve) => setTimeout(resolve, 5))
    inFlight -= 1
    issued += 1
    return new Response(JSON.stringify({ access_token: `t${issued}` }), { status: 200 })
  }))

  const tabA = await import('./api?tab=a' as string)
  const tabB = await import('./api?tab=b' as string)
  const tokens = await Promise.all([tabA.refreshAccessToken(), tabB.refreshAccessToken()])

  expect(maxInFlight).toBe(1)
  expect(tokens.sort()).toEqual(['t1', 't2'])
  expect(names).toEqual(['aether-auth-refresh', 'aether-auth-refresh'])
})

it('still refreshes where the Web Locks API is unavailable', async () => {
  vi.stubGlobal('navigator', {})
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ access_token: 'solo' }), { status: 200 })))
  expect(await refreshAccessToken()).toBe('solo')
})
