import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import * as authApi from '@/api/auth'
import { renderWithProviders } from '@/test/utils'

import { ForgotPasswordPage } from './ForgotPasswordPage'

vi.mock('@/api/auth')

describe('ForgotPasswordPage', () => {
  it('shows the same confirmation whether or not the address has an account', async () => {
    // The server deliberately answers identically for both, so the UI must not
    // reintroduce the account enumerator by saying "no such user".
    vi.mocked(authApi.forgotPassword).mockResolvedValue({ detail: 'ok' })

    renderWithProviders(<ForgotPasswordPage />)
    await userEvent.type(screen.getByLabelText(/email/i), 'ghost@example.com')
    await userEvent.click(screen.getByRole('button', { name: /send reset link/i }))

    expect(await screen.findByText(/has an account, a reset link is on its way/i)).toBeInTheDocument()
    expect(screen.queryByText(/not found|no account|does not exist/i)).not.toBeInTheDocument()
  })

  it('surfaces an error when the request fails outright', async () => {
    vi.mocked(authApi.forgotPassword).mockRejectedValue(new Error('network'))

    renderWithProviders(<ForgotPasswordPage />)
    await userEvent.type(screen.getByLabelText(/email/i), 'someone@example.com')
    await userEvent.click(screen.getByRole('button', { name: /send reset link/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/something went wrong/i)
  })
})
