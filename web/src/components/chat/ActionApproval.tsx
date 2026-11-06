import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { decideAction } from '@/api/chat'
import { Button } from '@/components/ui/button'
import type { ChatMessage } from '@/types'

type Proposal = {
  arguments?: Record<string, unknown>
  // Read by the server from the user's own data when the action was proposed,
  // not written by the model. This is what the action will really touch.
  target?: Record<string, unknown> | null
  expires_at?: string
}

function Fields({ values }: { values: Record<string, unknown> }) {
  return (
    <dl className="space-y-1 break-all">
      {Object.entries(values).map(([key, value]) => (
        <div key={key}><dt className="font-medium">{key.replaceAll('_', ' ')}</dt><dd className="whitespace-pre-wrap">{value == null || value === '' ? '—' : typeof value === 'string' ? value : JSON.stringify(value)}</dd></div>
      ))}
    </dl>
  )
}

export function ActionApproval({ conversationId, message }: { conversationId: number; message: ChatMessage }) {
  const queryClient = useQueryClient()
  const mutation = useMutation({
    mutationFn: (decision: 'approve' | 'reject') => decideAction(conversationId, message.id, decision),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ['conversation', conversationId] }),
  })
  let proposal: Proposal = {}
  try { proposal = JSON.parse(message.content ?? '{}') } catch { /* Invalid proposals cannot be approved. */ }
  const [openedAt] = useState(() => Date.now())
  const expiresAt = Date.parse(proposal.expires_at ?? '')
  const expired = !Number.isFinite(expiresAt) || expiresAt <= openedAt
  // `kind` is a machine tag for the item type, not something to show the user.
  const target = Object.fromEntries(Object.entries(proposal.target ?? {}).filter(([key]) => key !== 'kind'))
  const hasTarget = proposal.target != null
  return (
    <div className="w-full space-y-2 rounded border border-border p-3">
      <p>Review this action before approving. {expired ? 'Approval expired.' : 'Approval expires in 10 minutes from creation.'}</p>
      {hasTarget && (
        <section aria-label="Affected item" className="space-y-1">
          <h3 className="text-xs font-semibold uppercase text-muted-foreground">Affects</h3>
          <Fields values={target} />
        </section>
      )}
      <section aria-label="Requested change" className="space-y-1">
        {hasTarget && <h3 className="text-xs font-semibold uppercase text-muted-foreground">Requested change</h3>}
        <Fields values={proposal.arguments ?? {}} />
      </section>
      <div className="flex gap-2">
        <Button size="sm" disabled={mutation.isPending || expired || !proposal.arguments} onClick={() => mutation.mutate('approve')}>Approve action</Button>
        <Button size="sm" variant="outline" disabled={mutation.isPending} onClick={() => mutation.mutate('reject')}>Reject action</Button>
      </div>
      {mutation.isError && <p role="alert">Could not confirm the action. Refresh and check its status before retrying.</p>}
    </div>
  )
}
