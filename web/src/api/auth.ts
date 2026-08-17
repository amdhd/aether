import { API_PREFIX, API_URL, ApiError, apiFetch } from '@/lib/api'
import type { AccessToken, User } from '@/types'

export function register(input: { email: string; name: string; password: string }) {
  return apiFetch<User>(`${API_PREFIX}/auth/register`, {
    method: 'POST',
    body: input,
    skipAuth: true,
  })
}

export async function login(input: { email: string; password: string }): Promise<AccessToken> {
  const body = new URLSearchParams({ username: input.email, password: input.password })
  const res = await fetch(`${API_URL}${API_PREFIX}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    // Needed so the browser stores the HttpOnly refresh cookie set by the server.
    credentials: 'include',
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
  return res.json() as Promise<AccessToken>
}

export function logout() {
  // The refresh cookie is sent automatically (credentials:'include' in apiFetch)
  // and cleared by the server.
  return apiFetch<void>(`${API_PREFIX}/auth/logout`, { method: 'POST' })
}

export function getMe() {
  return apiFetch<User>(`${API_PREFIX}/auth/me`)
}

export function changePassword(input: { current_password: string; new_password: string }) {
  return apiFetch<AccessToken>(`${API_PREFIX}/auth/change-password`, {
    method: 'POST',
    body: input,
  })
}

export function forgotPassword(email: string) {
  // Deliberately unauthenticated, and the server answers identically whether or
  // not the address has an account — so there is nothing here to branch on.
  return apiFetch<{ detail: string }>(`${API_PREFIX}/auth/forgot-password`, {
    method: 'POST',
    body: { email },
    skipAuth: true,
  })
}

export function resetPassword(input: { token: string; new_password: string }) {
  return apiFetch<void>(`${API_PREFIX}/auth/reset-password`, {
    method: 'POST',
    body: input,
    skipAuth: true,
  })
}

export function sendVerificationEmail() {
  return apiFetch<{ detail: string }>(`${API_PREFIX}/auth/verify-email/send`, { method: 'POST' })
}

export function confirmEmail(token: string) {
  // Opened from a mail client, which may not be a browser holding a session.
  return apiFetch<void>(`${API_PREFIX}/auth/verify-email/confirm`, {
    method: 'POST',
    body: { token },
    skipAuth: true,
  })
}
