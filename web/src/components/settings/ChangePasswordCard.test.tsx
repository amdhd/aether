import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import * as authApi from '@/api/auth'
import { ApiError } from '@/lib/api'
import { useAuthStore } from '@/store/auth'
import { renderWithProviders } from '@/test/utils'

import { ChangePasswordCard } from './ChangePasswordCard'

vi.mock('@/api/auth')

async function fill(current: string, next: string, confirm: string) {
  await userEvent.type(screen.getByLabelText(/current password/i), current)
  await userEvent.type(screen.getByLabelText(/^new password$/i), next)
  await userEvent.type(screen.getByLabelText(/confirm new password/i), confirm)
  await userEvent.click(screen.getByRole('button', { name: /update password/i }))
}

describe('ChangePasswordCard', () => {
  afterEach(() => {
    useAuthStore.setState({ accessToken: null, user: null, bootstrapped: false })
    vi.clearAllMocks()
  })

  it('will not submit when the two new passwords differ', async () => {
    renderWithProviders(<ChangePasswordCard />)
    await fill('current-password', 'new-password-1', 'new-password-2')

    expect(await screen.findByRole('alert')).toHaveTextContent(/do not match/i)
    expect(authApi.changePassword).not.toHaveBeenCalled()
  })

  it('stores the reissued access token so the session survives the change', async () => {
    // The server revokes every session and hands this caller a fresh pair. Not
    // swapping the in-memory token would 401 the very next request.
    useAuthStore.setState({ accessToken: 'stale-token' })
    vi.mocked(authApi.changePassword).mockResolvedValue({
      access_token: 'reissued-token',
      token_type: 'bearer',
    })

    renderWithProviders(<ChangePasswordCard />)
    await fill('current-password', 'new-password-1', 'new-password-1')

    await waitFor(() => expect(useAuthStore.getState().accessToken).toBe('reissued-token'))
    expect(await screen.findByRole('status')).toHaveTextContent(/other devices have been signed out/i)
  })

  it('reports a wrong current password without clearing the form', async () => {
    vi.mocked(authApi.changePassword).mockRejectedValue(new ApiError(400, null))

    renderWithProviders(<ChangePasswordCard />)
    await fill('wrong-password', 'new-password-1', 'new-password-1')

    expect(await screen.findByRole('alert')).toHaveTextContent(/current password is not correct/i)
  })
})
