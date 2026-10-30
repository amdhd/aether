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
