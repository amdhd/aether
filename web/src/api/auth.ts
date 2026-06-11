import { API_PREFIX, ApiError, apiFetch } from '@/lib/api'
import type { Token, User } from '@/types'

const API_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

export function register(input: { email: string; name: string; password: string }) {
  return apiFetch<User>(`${API_PREFIX}/auth/register`, {
    method: 'POST',
    body: input,
    skipAuth: true,
  })
}

export async function login(input: { email: string; password: string }): Promise<Token> {
  const body = new URLSearchParams({ username: input.email, password: input.password })
  const res = await fetch(`${API_URL}${API_PREFIX}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body,
  })
  if (!res.ok) {
    let errorBody: unknown = null
    try {
      errorBody = await res.json()
    } catch {
      // no JSON body
    }
    throw new ApiError(res.status, errorBody)
  }
  return res.json() as Promise<Token>
}

export function logout(refreshToken: string) {
  return apiFetch<void>(`${API_PREFIX}/auth/logout`, {
    method: 'POST',
    body: { refresh_token: refreshToken },
  })
}

export function getMe() {
  return apiFetch<User>(`${API_PREFIX}/auth/me`)
}
