export interface User {
  id: number
  email: string
  name: string
  created_at: string
}

export interface Token {
  access_token: string
  refresh_token: string
  token_type: string
}

export type TaskPriority = 'low' | 'medium' | 'high'
export type TaskStatus = 'todo' | 'doing' | 'done'

export interface Task {
  id: number
  title: string
  description: string | null
  due_date: string | null
  priority: TaskPriority
  status: TaskStatus
  created_at: string
  updated_at: string
}

export interface TaskCreateInput {
  title: string
  description?: string | null
  due_date?: string | null
  priority?: TaskPriority
  status?: TaskStatus
}

export type TaskUpdateInput = Partial<TaskCreateInput>

export interface Note {
  id: number
  title: string
  content: string
  tags: string[]
  created_at: string
  updated_at: string
}

export interface NoteCreateInput {
  title: string
  content?: string
  tags?: string[]
}

export type NoteUpdateInput = Partial<NoteCreateInput>
