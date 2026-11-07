import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import * as authApi from '@/api/auth'
import { useAuthStore } from '@/store/auth'
import { renderWithProviders } from '@/test/utils'

import { SessionsCard } from './SessionsCard'

vi.mock('@/api/auth')

describe('SessionsCard', () => {
  it('signs out every device and clears this session', async () => {
    useAuthStore.setState({ accessToken: 'token', user: { id: 1 } as never })
    vi.mocked(authApi.logoutEverywhere).mockResolvedValue(undefined)
    renderWithProviders(<SessionsCard />)

    await userEvent.click(screen.getByRole('button', { name: 'Sign out of all devices' }))

    await waitFor(() => expect(authApi.logoutEverywhere).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(useAuthStore.getState().accessToken).toBeNull())
  })

  it('keeps the session and reports a failure', async () => {
    useAuthStore.setState({ accessToken: 'token', user: { id: 1 } as never })
    vi.mocked(authApi.logoutEverywhere).mockRejectedValue(new Error('offline'))
    renderWithProviders(<SessionsCard />)

    await userEvent.click(screen.getByRole('button', { name: 'Sign out of all devices' }))

    expect(await screen.findByRole('alert')).toHaveTextContent("Couldn't sign out other devices")
    expect(useAuthStore.getState().accessToken).toBe('token')
  })
})
