import { API_PREFIX, apiFetch } from '@/lib/api'
import type { Note, NoteCreateInput, NoteUpdateInput, Page } from '@/types'

export function listNotes(q?: string, limit?: number, offset?: number) {
  const params = new URLSearchParams()
  if (q) params.set('q', q)
  if (limit !== undefined) params.set('limit', String(limit))
  if (offset !== undefined) params.set('offset', String(offset))
  const query = params.toString() ? `?${params.toString()}` : ''
  return apiFetch<Page<Note>>(`${API_PREFIX}/notes${query}`)
}

export function createNote(input: NoteCreateInput) {
  return apiFetch<Note>(`${API_PREFIX}/notes`, {
    method: 'POST',
    body: input,
  })
}

export function updateNote(id: number, input: NoteUpdateInput) {
  return apiFetch<Note>(`${API_PREFIX}/notes/${id}`, {
    method: 'PUT',
    body: input,
  })
}

export function deleteNote(id: number) {
  return apiFetch<void>(`${API_PREFIX}/notes/${id}`, {
    method: 'DELETE',
  })
}
