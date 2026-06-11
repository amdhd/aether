import { useAuthStore } from '@/store/auth'
import type { Token } from '@/types'

const API_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'
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

async function refreshAccessToken(): Promise<string | null> {
  const { refreshToken, setTokens, logout } = useAuthStore.getState()
  if (!refreshToken) return null

  if (!refreshPromise) {
    refreshPromise = (async () => {
      try {
        const res = await fetch(`${API_URL}${API_PREFIX}/auth/refresh`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ refresh_token: refreshToken }),
        })
        if (!res.ok) {
          logout()
          return null
        }
        const data: Token = await res.json()
        setTokens(data.access_token, data.refresh_token)
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
