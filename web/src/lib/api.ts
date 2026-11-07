import { useAuthStore } from '@/store/auth'
import type { AccessToken } from '@/types'

export const API_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'
export const API_PREFIX = '/api/v1'

// The refresh endpoint authenticates with a cookie, so the browser attaches it
// to cross-site requests on its own and the server requires this header as proof
// the request came from us. It sits outside the CORS safelist, so sending it
// forces a preflight that only our own origin passes — which is exactly why an
// attacker's page cannot reproduce it. Every other endpoint uses a Bearer token
// and needs no such marker.
export const CSRF_HEADER = 'X-Requested-With'
export const CSRF_HEADERS: Record<string, string> = { [CSRF_HEADER]: 'XMLHttpRequest' }

export class ApiError extends Error {
  status: number
  body: unknown

  constructor(status: number, body: unknown, message?: string) {
    super(message ?? `Request failed with status ${status}`)
    this.status = status
    this.body = body
  }
}

/**
 * The server's own explanation of a failed request, fit to show a user, or null
 * when there is none (a network failure, an empty or unexpected body).
 *
 * FastAPI answers an HTTPException with `detail` as a string ("You're saving
 * notes too quickly…") and a validation failure with `detail` as a list of
 * `{loc, msg}` — the first of which names the field and the rule it broke.
 */
export function apiErrorDetail(error: unknown): string | null {
  if (!(error instanceof ApiError)) return null
  const detail = (error.body as { detail?: unknown } | null)?.detail
  if (typeof detail === 'string' && detail.trim()) return detail.trim()
  if (Array.isArray(detail) && detail.length > 0) {
    const first = detail[0] as { loc?: unknown[]; msg?: unknown }
    if (typeof first?.msg === 'string') {
      const field = Array.isArray(first.loc) ? first.loc[first.loc.length - 1] : undefined
      return typeof field === 'string' && field !== 'body'
        ? `${field.replaceAll('_', ' ')}: ${first.msg}`
        : first.msg
    }
  }
  return null
}

/** Whether trying the same request again could succeed. */
export function isRetryable(error: unknown): boolean {
  return !(error instanceof ApiError) || error.status === 429 || error.status >= 500
}

let refreshPromise: Promise<string | null> | null = null

// Refresh tokens are single-use: the server rotates the cookie on every refresh
// and treats a second use of the old one as theft, revoking every session the
// user has. `refreshPromise` stops one tab refreshing twice, but two tabs share
// the cookie and not that variable — so when both tabs' access tokens expire
// together (back from a break, or a second tab opening), both sent the same
// cookie and the loser signed the user out everywhere.
//
// A Web Lock is shared by every tab of this origin, so refreshes now run one at
// a time across tabs. The tab that waited sends the cookie the first tab's
// refresh just replaced, which is a valid, unused token. Browsers without the
// Locks API fall back to the per-tab guard alone.
export const REFRESH_LOCK = 'aether-auth-refresh'

function acrossTabs<T>(work: () => Promise<T>): Promise<T> {
  const locks = typeof navigator !== 'undefined' ? navigator.locks : undefined
  return locks?.request ? locks.request(REFRESH_LOCK, work) : work()
}

export async function refreshAccessToken(): Promise<string | null> {
  const { setAccessToken, logout } = useAuthStore.getState()

  // The refresh token rides along as an HttpOnly cookie (credentials:'include'),
  // so there is nothing to read from the store or send in the body.
  if (!refreshPromise) {
    refreshPromise = acrossTabs(async () => {
      try {
        const request = () => fetch(`${API_URL}${API_PREFIX}/auth/refresh`, {
          method: 'POST', credentials: 'include', headers: CSRF_HEADERS,
        })
        let res = await request()
        // A 429 is rejected before token rotation, so one short retry is safe.
        // Network/5xx responses may hide a committed rotation: never replay
        // those automatically with an uncertain single-use refresh cookie.
        const retryAfter = Number(res.headers.get('Retry-After') ?? '1')
        if (res.status === 429 && Number.isFinite(retryAfter) && retryAfter >= 0 && retryAfter <= 1) {
          await new Promise((resolve) => setTimeout(resolve, Math.max(100, retryAfter * 1000)))
          res = await request()
        }
        if (!res.ok) {
          if (res.status === 401) logout()
          return null
        }
        const data: AccessToken = await res.json()
        setAccessToken(data.access_token)
        return data.access_token
      } catch {
        // Keep existing identity/cache state during recoverable outages.
        return null
      } finally {
        refreshPromise = null
      }
    })
  }

  return refreshPromise
}

/**
 * On app load, try to exchange the refresh cookie for an access token. Resolves
 * to the access token (logged in) or null (no/expired session). Marks the auth
 * store as bootstrapped so route guards can stop showing a loader.
 */
export async function bootstrapAuth(): Promise<string | null> {
  try {
    return await refreshAccessToken()
  } finally {
    useAuthStore.getState().setBootstrapped(true)
  }
}

interface RequestOptions extends Omit<RequestInit, 'body'> {
  body?: unknown
  skipAuth?: boolean
}

export async function apiFetch<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { body, skipAuth, headers, ...rest } = options

  const doFetch = async (token: string | null): Promise<Response> => {
    const finalHeaders: Record<string, string> = {
      ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
      ...(headers as Record<string, string> | undefined),
    }
    if (token && !skipAuth) {
      finalHeaders.Authorization = `Bearer ${token}`
    }
    return fetch(`${API_URL}${path}`, {
      ...rest,
      credentials: 'include',
      headers: finalHeaders,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    })
  }

  let token = useAuthStore.getState().accessToken
  let res = await doFetch(token)

  if (res.status === 401 && !skipAuth) {
    token = await refreshAccessToken()
    if (token) {
      res = await doFetch(token)
    }
  }

  if (!res.ok) {
    let errorBody: unknown = null
    try {
      errorBody = await res.json()
    } catch {
      // no JSON body
    }
    throw new ApiError(res.status, errorBody)
  }

  if (res.status === 204) {
    return undefined as T
  }

  return res.json() as Promise<T>
}
