import { beforeEach, describe, expect, it } from 'vitest'

import type { User } from '@/types'

import { useAuthStore } from './auth'

const mockUser: User = { id: 1, email: 'a@example.com', name: 'A', created_at: '2026-01-01T00:00:00Z' }

describe('useAuthStore', () => {
  beforeEach(() => {
    useAuthStore.setState({ accessToken: null, refreshToken: null, user: null })
  })

  it('starts with no session', () => {
    const state = useAuthStore.getState()
    expect(state.accessToken).toBeNull()
    expect(state.refreshToken).toBeNull()
    expect(state.user).toBeNull()
  })

  it('setTokens stores access and refresh tokens', () => {
    useAuthStore.getState().setTokens('access-123', 'refresh-456')

    const state = useAuthStore.getState()
    expect(state.accessToken).toBe('access-123')
    expect(state.refreshToken).toBe('refresh-456')
  })

  it('setUser stores the current user', () => {
    useAuthStore.getState().setUser(mockUser)

    expect(useAuthStore.getState().user).toEqual(mockUser)
  })

  it('logout clears tokens and user', () => {
    useAuthStore.getState().setTokens('access-123', 'refresh-456')
    useAuthStore.getState().setUser(mockUser)

    useAuthStore.getState().logout()

    const state = useAuthStore.getState()
    expect(state.accessToken).toBeNull()
    expect(state.refreshToken).toBeNull()
    expect(state.user).toBeNull()
  })
})
