import { API_PREFIX, apiFetch } from '@/lib/api'
import type { Note, NoteCreateInput, NoteUpdateInput } from '@/types'

export function listNotes(q?: string) {
  const query = q ? `?q=${encodeURIComponent(q)}` : ''
  return apiFetch<Note[]>(`${API_PREFIX}/notes${query}`)
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
