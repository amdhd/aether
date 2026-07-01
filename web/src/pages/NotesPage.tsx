import { useQuery } from '@tanstack/react-query'
import { Pencil, Plus, Search, Trash2 } from 'lucide-react'
import { useState } from 'react'

import { createNote, deleteNote, listNotes, updateNote } from '@/api/notes'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { NoteFormDialog } from '@/components/notes/NoteFormDialog'
import { useCrudMutations } from '@/hooks/useCrudMutations'
import { useDebounce } from '@/hooks/useDebounce'
import type { Note, NoteCreateInput } from '@/types'

const PAGE_SIZE = 50
const MAX_LIMIT = 100

export function NotesPage() {
  const [search, setSearch] = useState('')
  const [limit, setLimit] = useState(PAGE_SIZE)
  const debouncedSearch = useDebounce(search, 300)

  const { data, isLoading, isError } = useQuery({
    queryKey: ['notes', debouncedSearch, limit],
    queryFn: () => listNotes(debouncedSearch || undefined, limit),
  })
  const notes = data?.items ?? []
  const canLoadMore = data !== undefined && notes.length < data.total && limit < MAX_LIMIT

  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingNote, setEditingNote] = useState<Note | null>(null)
  const [deletingNote, setDeletingNote] = useState<Note | null>(null)

  const { createMutation, updateMutation, deleteMutation } = useCrudMutations<Note, NoteCreateInput>({
    queryKey: ['notes'],
    create: createNote,
    update: updateNote,
    remove: deleteNote,
    onDeleteSuccess: () => setDeletingNote(null),
  })

  const openCreateDialog = () => {
    setEditingNote(null)
    setDialogOpen(true)
  }

  const openEditDialog = (note: Note) => {
    setEditingNote(note)
    setDialogOpen(true)
  }

  const handleSubmit = async (input: NoteCreateInput) => {
    if (editingNote) {
      await updateMutation.mutateAsync({ id: editingNote.id, input })
    } else {
      await createMutation.mutateAsync(input)
    }
  }

  const handleDelete = (note: Note) => {
    setDeletingNote(note)
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Notes</h1>
          <p className="text-muted-foreground">Capture ideas and search through them later.</p>
        </div>
        <Button onClick={openCreateDialog}>
          <Plus className="h-4 w-4" />
          New note
        </Button>
      </div>

      <div className="relative max-w-sm">
        <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          type="search"
          placeholder="Search notes..."
          aria-label="Search notes"
          className="pl-9"
          value={search}
          onChange={(e) => {
            setSearch(e.target.value)
            setLimit(PAGE_SIZE)
          }}
        />
      </div>

      {isError && <p className="text-sm text-red-600">Failed to load notes. Please try again.</p>}

      {isLoading ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {[...Array(3)].map((_, i) => (
            <Skeleton key={i} className="h-40 w-full" />
          ))}
        </div>
      ) : notes.length === 0 ? (
        <p className="rounded-card border border-dashed border-border p-8 text-center text-sm text-muted-foreground">
          {debouncedSearch ? 'No notes match your search.' : 'No notes yet. Create your first one.'}
        </p>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {notes.map((note) => (
            <Card key={note.id} className="flex flex-col">
              <CardHeader className="pb-2">
                <CardTitle className="text-sm">{note.title}</CardTitle>
              </CardHeader>
              <CardContent className="flex-1 space-y-2 pb-2">
                {note.content && <p className="line-clamp-4 text-sm text-muted-foreground">{note.content}</p>}
                {note.tags.length > 0 && (
                  <div className="flex flex-wrap gap-1">
                    {note.tags.map((tag) => (
                      <Badge key={tag} variant="secondary">
                        {tag}
                      </Badge>
                    ))}
                  </div>
                )}
              </CardContent>
              <CardFooter className="justify-end gap-1">
                <Button variant="ghost" size="icon" aria-label={`Edit ${note.title}`} onClick={() => openEditDialog(note)}>
                  <Pencil className="h-4 w-4" />
                </Button>
                <Button variant="ghost" size="icon" aria-label={`Delete ${note.title}`} onClick={() => handleDelete(note)}>
                  <Trash2 className="h-4 w-4" />
                </Button>
              </CardFooter>
            </Card>
          ))}
        </div>
      )}

      {canLoadMore && (
        <div className="flex justify-center">
          <Button variant="outline" onClick={() => setLimit((prev) => Math.min(prev + PAGE_SIZE, MAX_LIMIT))}>
            Load more
          </Button>
        </div>
      )}

      {dialogOpen && (
        <NoteFormDialog
          open={dialogOpen}
          onOpenChange={setDialogOpen}
          note={editingNote}
          onSubmit={handleSubmit}
          isSubmitting={createMutation.isPending || updateMutation.isPending}
        />
      )}

      <ConfirmDialog
        open={deletingNote !== null}
        onOpenChange={(open) => !open && setDeletingNote(null)}
        title={`Delete "${deletingNote?.title}"?`}
        description="This action cannot be undone."
        isConfirming={deleteMutation.isPending}
        onConfirm={() => deletingNote && deleteMutation.mutate(deletingNote.id)}
      />
    </div>
  )
}
