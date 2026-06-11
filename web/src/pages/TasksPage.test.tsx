import { screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import * as tasksApi from '@/api/tasks'
import { renderWithProviders } from '@/test/utils'
import type { Task } from '@/types'

import { TasksPage } from './TasksPage'

vi.mock('@/api/tasks')

const mockTasks: Task[] = [
  {
    id: 1,
    title: 'Write report',
    description: null,
    due_date: null,
    priority: 'high',
    status: 'todo',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  },
  {
    id: 2,
    title: 'Review PR',
    description: null,
    due_date: null,
    priority: 'medium',
    status: 'doing',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  },
]

describe('TasksPage', () => {
  it('renders tasks grouped into kanban columns', async () => {
    vi.mocked(tasksApi.listTasks).mockResolvedValue(mockTasks)

    renderWithProviders(<TasksPage />)

    expect(await screen.findByText('Write report')).toBeInTheDocument()
    expect(screen.getByText('Review PR')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: /tasks/i })).toBeInTheDocument()
  })

  it('shows an error message if tasks fail to load', async () => {
    vi.mocked(tasksApi.listTasks).mockRejectedValue(new Error('network error'))

    renderWithProviders(<TasksPage />)

    expect(await screen.findByText(/failed to load tasks/i)).toBeInTheDocument()
  })
})
