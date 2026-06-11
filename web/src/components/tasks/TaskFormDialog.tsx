import { useState } from 'react'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import type { Task, TaskCreateInput, TaskPriority, TaskStatus } from '@/types'

interface TaskFormDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  task?: Task | null
  onSubmit: (input: TaskCreateInput) => Promise<void>
  isSubmitting?: boolean
}

const emptyForm: TaskCreateInput = {
  title: '',
  description: '',
  due_date: null,
  priority: 'medium',
  status: 'todo',
}

export function TaskFormDialog({ open, onOpenChange, task, onSubmit, isSubmitting }: TaskFormDialogProps) {
  const [form, setForm] = useState<TaskCreateInput>(() =>
    task
      ? {
          title: task.title,
          description: task.description ?? '',
          due_date: task.due_date,
          priority: task.priority,
          status: task.status,
        }
      : emptyForm,
  )
  const [error, setError] = useState<string | null>(null)

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!form.title.trim()) {
      setError('Title is required.')
      return
    }
    setError(null)
    try {
      await onSubmit({
        ...form,
        description: form.description?.trim() ? form.description : null,
        due_date: form.due_date || null,
      })
      onOpenChange(false)
    } catch {
      setError('Could not save task. Please try again.')
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{task ? 'Edit task' : 'New task'}</DialogTitle>
        </DialogHeader>
        <form className="space-y-4" onSubmit={handleSubmit}>
          <div className="space-y-2">
            <Label htmlFor="task-title">Title</Label>
            <Input
              id="task-title"
              required
              value={form.title}
              onChange={(e) => setForm((f) => ({ ...f, title: e.target.value }))}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="task-description">Description</Label>
            <Textarea
              id="task-description"
              value={form.description ?? ''}
              onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
            />
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="task-due-date">Due date</Label>
              <Input
                id="task-due-date"
                type="date"
                value={form.due_date ?? ''}
                onChange={(e) => setForm((f) => ({ ...f, due_date: e.target.value || null }))}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="task-priority">Priority</Label>
              <Select
                value={form.priority}
                onValueChange={(value) => setForm((f) => ({ ...f, priority: value as TaskPriority }))}
              >
                <SelectTrigger id="task-priority">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="low">Low</SelectItem>
                  <SelectItem value="medium">Medium</SelectItem>
                  <SelectItem value="high">High</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
          <div className="space-y-2">
            <Label htmlFor="task-status">Status</Label>
            <Select
              value={form.status}
              onValueChange={(value) => setForm((f) => ({ ...f, status: value as TaskStatus }))}
            >
              <SelectTrigger id="task-status">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="todo">To do</SelectItem>
                <SelectItem value="doing">Doing</SelectItem>
                <SelectItem value="done">Done</SelectItem>
              </SelectContent>
            </Select>
          </div>
          {error && (
            <p role="alert" className="text-sm text-red-600">
              {error}
            </p>
          )}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={isSubmitting}>
              {isSubmitting ? 'Saving...' : 'Save'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
