import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import * as authApi from '@/api/auth'
import { ApiError } from '@/lib/api'
import { renderWithProviders } from '@/test/utils'

import { LoginPage } from './LoginPage'

vi.mock('@/api/auth')

describe('LoginPage', () => {
  it('renders the login form', () => {
    renderWithProviders(<LoginPage />)

    expect(screen.getByRole('heading', { name: /sign in to aether/i })).toBeInTheDocument()
    expect(screen.getByLabelText(/email/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/password/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /sign in/i })).toBeInTheDocument()
  })

  it('shows an error message on invalid credentials', async () => {
    vi.mocked(authApi.login).mockRejectedValue(new ApiError(401, null))

    renderWithProviders(<LoginPage />)

    await userEvent.type(screen.getByLabelText(/email/i), 'test@example.com')
    await userEvent.type(screen.getByLabelText(/password/i), 'wrongpassword')
    await userEvent.click(screen.getByRole('button', { name: /sign in/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/incorrect email or password/i)
  })
})
