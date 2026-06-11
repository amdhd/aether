import { API_PREFIX, apiFetch } from '@/lib/api'
import type { Page, Task, TaskCreateInput, TaskUpdateInput } from '@/types'

export function listTasks() {
  return apiFetch<Page<Task>>(`${API_PREFIX}/tasks`)
}

export function createTask(input: TaskCreateInput) {
  return apiFetch<Task>(`${API_PREFIX}/tasks`, {
    method: 'POST',
    body: input,
  })
}

export function updateTask(id: number, input: TaskUpdateInput) {
  return apiFetch<Task>(`${API_PREFIX}/tasks/${id}`, {
    method: 'PUT',
    body: input,
  })
}

export function deleteTask(id: number) {
  return apiFetch<void>(`${API_PREFIX}/tasks/${id}`, {
    method: 'DELETE',
  })
}
