import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronDown, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'

import {
  CONVERSATION_PARAM,
  CONVERSATIONS_PAGE_SIZE,
  deleteConversation,
  listConversations,
} from '@/api/chat'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'

// The sidebar is a peek at recent chats, not the full archive.
const HISTORY_LIMIT = 5

export function ChatHistoryNav() {
  const queryClient = useQueryClient()
  const [searchParams, setSearchParams] = useSearchParams()
  const [open, setOpen] = useState(true)
  const [deletingId, setDeletingId] = useState<number | null>(null)

  const selectedParam = searchParams.get(CONVERSATION_PARAM)
  const activeId = selectedParam !== null && /^\d+$/.test(selectedParam) ? Number(selectedParam) : null

  // Same key as the chat page, so this shares one request and one cache entry.
  const { data: page, isLoading } = useQuery({
    queryKey: ['conversations', CONVERSATIONS_PAGE_SIZE],
    queryFn: () => listConversations(CONVERSATIONS_PAGE_SIZE),
  })
  const conversations = (page?.items ?? []).slice(0, HISTORY_LIMIT)

  const select = (id: number | null) => {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        if (id === null) next.delete(CONVERSATION_PARAM)
        else next.set(CONVERSATION_PARAM, String(id))
        return next
      },
      { replace: true },
    )
  }

  const deleteMutation = useMutation({
    mutationFn: (id: number) => deleteConversation(id),
    onSuccess: (_data, id) => {
      queryClient.removeQueries({ queryKey: ['conversation', id] })
      queryClient.invalidateQueries({ queryKey: ['conversations'] })
      // Falling back to the newest chat is the page's default when no id is set.
      if (activeId === id) select(null)
      setDeletingId(null)
    },
  })

  return (
    <div className="mt-0.5">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((prev) => !prev)}
        className="flex w-full items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground focus-ring"
      >
        <ChevronDown className={cn('h-3.5 w-3.5 transition-transform', !open && '-rotate-90')} />
        Recent chats
      </button>

      {open && (
        <div className="mt-0.5 space-y-0.5 pl-3">
          {isLoading ? (
            [...Array(3)].map((_, i) => <Skeleton key={i} className="h-7 w-full" />)
          ) : conversations.length === 0 ? (
            <p className="px-3 py-1 text-xs text-muted-foreground">No conversations yet.</p>
          ) : (
            conversations.map((c) => (
              <div
                key={c.id}
                className={cn(
                  'group flex items-center gap-1 rounded-md transition-colors',
                  c.id === activeId ? 'bg-surface-muted' : 'hover:bg-surface-muted/60',
                )}
              >
                <button
                  className={cn(
                    'min-w-0 flex-1 truncate rounded-md px-3 py-1.5 text-left text-sm focus-ring',
                    c.id === activeId ? 'font-medium text-foreground' : 'text-muted-foreground',
                  )}
                  onClick={() => select(c.id)}
                >
                  {c.title}
                </button>
                <button
                  aria-label={`Delete conversation ${c.title}`}
                  className="mr-1 shrink-0 rounded p-1 opacity-0 focus-ring group-hover:opacity-100"
                  onClick={() => setDeletingId(c.id)}
                >
                  <Trash2 className="h-3.5 w-3.5 text-muted-foreground hover:text-red-600" />
                </button>
              </div>
            ))
          )}
        </div>
      )}

      <ConfirmDialog
        open={deletingId !== null}
        onOpenChange={(isOpen) => !isOpen && setDeletingId(null)}
        title="Delete this conversation?"
        description="This action cannot be undone."
        isConfirming={deleteMutation.isPending}
        onConfirm={() => deletingId !== null && deleteMutation.mutate(deletingId)}
      />
    </div>
  )
}
