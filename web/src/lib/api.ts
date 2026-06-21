import { useAuthStore } from '@/store/auth'
import type { AccessToken } from '@/types'

export const API_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'
export const API_PREFIX = '/api/v1'

export class ApiError extends Error {
  status: number
  body: unknown

  constructor(status: number, body: unknown, message?: string) {
    super(message ?? `Request failed with status ${status}`)
    this.status = status
    this.body = body
  }
}

let refreshPromise: Promise<string | null> | null = null

export async function refreshAccessToken(): Promise<string | null> {
  const { setAccessToken, logout } = useAuthStore.getState()

  // The refresh token rides along as an HttpOnly cookie (credentials:'include'),
  // so there is nothing to read from the store or send in the body.
  if (!refreshPromise) {
    refreshPromise = (async () => {
      try {
        const res = await fetch(`${API_URL}${API_PREFIX}/auth/refresh`, {
          method: 'POST',
          credentials: 'include',
        })
        if (!res.ok) {
          logout()
          return null
        }
        const data: AccessToken = await res.json()
        setAccessToken(data.access_token)
        return data.access_token
      } catch {
        logout()
        return null
      } finally {
        refreshPromise = null
      }
    })()
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
