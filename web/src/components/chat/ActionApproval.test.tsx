import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { decideAction } from '@/api/chat'
import { renderWithProviders } from '@/test/utils'
import { ActionApproval } from './ActionApproval'
import type { ChatMessage } from '@/types'

vi.mock('@/api/chat', () => ({ decideAction: vi.fn() }))

const message: ChatMessage = {
  id: 9, role: 'tool', content: JSON.stringify({ arguments: { task_id: 42 }, expires_at: '2099-01-01T00:00:00Z' }),
  tool_name: 'delete_task', action_status: 'pending', reasoning_content: null,
  tool_calls: null, attachment_name: null, created_at: '2026-01-01T00:00:00Z',
}

describe('ActionApproval', () => {
  it('shows the exact target and sends only the explicit decision', async () => {
    vi.mocked(decideAction).mockResolvedValue({ ...message, action_status: 'completed' })
    renderWithProviders(<ActionApproval conversationId={3} message={message} />)
    expect(screen.getByText('42')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Approve action' }))
    await waitFor(() => expect(decideAction).toHaveBeenCalledWith(3, 9, 'approve'))
  })
  it('disables approval for expired proposals', () => {
    renderWithProviders(<ActionApproval conversationId={3} message={{ ...message, content: JSON.stringify({ arguments: { task_id: 42 }, expires_at: '2000-01-01T00:00:00Z' }) }} />)
    expect(screen.getByRole('button', { name: 'Approve action' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Reject action' })).toBeEnabled()
  })
})
