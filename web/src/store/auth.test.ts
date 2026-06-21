import { beforeEach, describe, expect, it } from 'vitest'

import type { User } from '@/types'

import { useAuthStore } from './auth'

const mockUser: User = { id: 1, email: 'a@example.com', name: 'A', created_at: '2026-01-01T00:00:00Z' }

describe('useAuthStore', () => {
  beforeEach(() => {
    useAuthStore.setState({ accessToken: null, user: null, bootstrapped: false })
  })

  it('starts with no session', () => {
    const state = useAuthStore.getState()
    expect(state.accessToken).toBeNull()
    expect(state.user).toBeNull()
    expect(state.bootstrapped).toBe(false)
  })

  it('setAccessToken stores the access token in memory', () => {
    useAuthStore.getState().setAccessToken('access-123')
    expect(useAuthStore.getState().accessToken).toBe('access-123')
  })

  it('setUser stores the current user', () => {
    useAuthStore.getState().setUser(mockUser)
    expect(useAuthStore.getState().user).toEqual(mockUser)
  })

  it('setBootstrapped flips the bootstrap flag', () => {
    useAuthStore.getState().setBootstrapped(true)
    expect(useAuthStore.getState().bootstrapped).toBe(true)
  })

  it('logout clears the access token and user', () => {
    useAuthStore.getState().setAccessToken('access-123')
    useAuthStore.getState().setUser(mockUser)

    useAuthStore.getState().logout()

    const state = useAuthStore.getState()
    expect(state.accessToken).toBeNull()
    expect(state.user).toBeNull()
  })
})
