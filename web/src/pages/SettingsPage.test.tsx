import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import * as integrationsApi from '@/api/integrations'
import { useAuthStore } from '@/store/auth'
import { renderWithProviders } from '@/test/utils'
import type { User } from '@/types'

import { SettingsPage } from './SettingsPage'

vi.mock('@/api/integrations')

const mockUser: User = { id: 1, email: 'a@example.com', name: 'Ada Lovelace', created_at: '2026-01-01T00:00:00Z' }

describe('SettingsPage', () => {
  afterEach(() => {
    useAuthStore.setState({ accessToken: null, refreshToken: null, user: null })
  })

  it('shows the current user account info', async () => {
    useAuthStore.setState({ user: mockUser })
    vi.mocked(integrationsApi.getGoogleStatus).mockResolvedValue({ connected: false })

    renderWithProviders(<SettingsPage />)

    expect(await screen.findByText('Ada Lovelace')).toBeInTheDocument()
    expect(screen.getByText('a@example.com')).toBeInTheDocument()
  })

  it('shows a connected badge and lets the user disconnect Google Calendar', async () => {
    useAuthStore.setState({ user: mockUser })
    vi.mocked(integrationsApi.getGoogleStatus).mockResolvedValue({ connected: true })
    vi.mocked(integrationsApi.disconnectGoogle).mockResolvedValue(undefined)

    renderWithProviders(<SettingsPage />)

    expect(await screen.findByText('Connected')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /disconnect/i }))

    expect(integrationsApi.disconnectGoogle).toHaveBeenCalled()
  })

  it('lets the user start connecting Google Calendar when not connected', async () => {
    vi.stubGlobal('location', { ...window.location, href: '' })
    useAuthStore.setState({ user: mockUser })
    vi.mocked(integrationsApi.getGoogleStatus).mockResolvedValue({ connected: false })
    vi.mocked(integrationsApi.getGoogleConnectUrl).mockResolvedValue({
      authorization_url: 'https://accounts.google.com/o/oauth2/auth',
    })

    renderWithProviders(<SettingsPage />)

    expect(await screen.findByText('Not connected')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /connect google calendar/i }))

    await waitFor(() => expect(integrationsApi.getGoogleConnectUrl).toHaveBeenCalled())

    vi.unstubAllGlobals()
  })
})
