import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import * as notesApi from '@/api/notes'
import { renderWithProviders } from '@/test/utils'
import type { Note, Page } from '@/types'

import { NotesPage } from './NotesPage'

vi.mock('@/api/notes')

const mockNotes: Note[] = [
  {
    id: 1,
    title: 'Grocery list',
    content: 'milk, eggs, bread',
    tags: ['errands'],
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  },
  {
    id: 2,
    title: 'Meeting notes',
    content: 'discuss roadmap',
    tags: [],
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  },
]

function mockPage(items: Note[]): Page<Note> {
  return { items, total: items.length, limit: 50, offset: 0 }
}

describe('NotesPage', () => {
  it('renders notes returned from the API', async () => {
    vi.mocked(notesApi.listNotes).mockResolvedValue(mockPage(mockNotes))

    renderWithProviders(<NotesPage />)

    expect(await screen.findByText('Grocery list')).toBeInTheDocument()
    expect(screen.getByText('Meeting notes')).toBeInTheDocument()
    expect(screen.getByText('errands')).toBeInTheDocument()
  })

  it('shows an error message if notes fail to load', async () => {
    vi.mocked(notesApi.listNotes).mockRejectedValue(new Error('network error'))

    renderWithProviders(<NotesPage />)

    expect(await screen.findByText(/failed to load notes/i)).toBeInTheDocument()
  })

  it('shows an empty state when there are no notes', async () => {
    vi.mocked(notesApi.listNotes).mockResolvedValue(mockPage([]))

    renderWithProviders(<NotesPage />)

    expect(await screen.findByText(/no notes yet/i)).toBeInTheDocument()
  })

  it('confirms before deleting a note', async () => {
    vi.mocked(notesApi.listNotes).mockResolvedValue(mockPage(mockNotes))
    vi.mocked(notesApi.deleteNote).mockResolvedValue(undefined)

    renderWithProviders(<NotesPage />)

    await screen.findByText('Grocery list')
    await userEvent.click(screen.getByRole('button', { name: /delete grocery list/i }))

    expect(await screen.findByRole('heading', { name: /delete "grocery list"\?/i })).toBeInTheDocument()
    expect(notesApi.deleteNote).not.toHaveBeenCalled()

    await userEvent.click(screen.getByRole('button', { name: /^delete$/i }))

    await waitFor(() => expect(notesApi.deleteNote).toHaveBeenCalledWith(1))
  })
})
