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
import { Textarea } from '@/components/ui/textarea'
import type { Note, NoteCreateInput } from '@/types'

interface NoteFormDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  note?: Note | null
  onSubmit: (input: NoteCreateInput) => Promise<void>
  isSubmitting?: boolean
}

const emptyForm = { title: '', content: '', tags: '' }

export function NoteFormDialog({ open, onOpenChange, note, onSubmit, isSubmitting }: NoteFormDialogProps) {
  const [form, setForm] = useState(() =>
    note ? { title: note.title, content: note.content, tags: note.tags.join(', ') } : emptyForm,
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
        title: form.title,
        content: form.content,
        tags: form.tags
          .split(',')
          .map((tag) => tag.trim())
          .filter(Boolean),
      })
      onOpenChange(false)
    } catch {
      setError('Could not save note. Please try again.')
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{note ? 'Edit note' : 'New note'}</DialogTitle>
        </DialogHeader>
        <form className="space-y-4" onSubmit={handleSubmit}>
          <div className="space-y-2">
            <Label htmlFor="note-title">Title</Label>
            <Input
              id="note-title"
              required
              value={form.title}
              onChange={(e) => setForm((f) => ({ ...f, title: e.target.value }))}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="note-content">Content</Label>
            <Textarea
              id="note-content"
              rows={6}
              value={form.content}
              onChange={(e) => setForm((f) => ({ ...f, content: e.target.value }))}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="note-tags">Tags</Label>
            <Input
              id="note-tags"
              placeholder="comma, separated, tags"
              value={form.tags}
              onChange={(e) => setForm((f) => ({ ...f, tags: e.target.value }))}
            />
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
