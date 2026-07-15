export interface Page<T> {
  items: T[]
  total: number
  limit: number
  offset: number
}

export interface User {
  id: number
  email: string
  name: string
  created_at: string
}

export interface AccessToken {
  access_token: string
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

export type Persona = 'productivity_coach' | 'research_assistant' | 'casual_friend' | 'marketing_coach'
export type MessageRole = 'user' | 'assistant' | 'tool'

export interface ToolCall {
  id: string
  type: 'function'
  function: {
    name: string
    arguments: string
  }
}

export interface Conversation {
  id: number
  title: string
  persona: Persona
  created_at: string
  updated_at: string
}

export interface ChatMessage {
  id: number
  role: MessageRole
  content: string | null
  reasoning_content: string | null
  tool_calls: ToolCall[] | null
  tool_name: string | null
  attachment_name: string | null
  created_at: string
}

export interface ConversationDetail extends Conversation {
  messages: ChatMessage[]
}

export interface ConversationCreateInput {
  title?: string
  persona?: Persona
}

export type ConversationUpdateInput = Partial<ConversationCreateInput>
