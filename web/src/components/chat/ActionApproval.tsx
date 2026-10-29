import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { decideAction } from '@/api/chat'
import { Button } from '@/components/ui/button'
import type { ChatMessage } from '@/types'

export function ActionApproval({ conversationId, message }: { conversationId: number; message: ChatMessage }) {
  const queryClient = useQueryClient()
  const mutation = useMutation({
    mutationFn: (decision: 'approve' | 'reject') => decideAction(conversationId, message.id, decision),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ['conversation', conversationId] }),
  })
  let proposal: { arguments?: Record<string, unknown>; expires_at?: string } = {}
  try { proposal = JSON.parse(message.content ?? '{}') } catch { /* Invalid proposals cannot be approved. */ }
  const [openedAt] = useState(() => Date.now())
  const expiresAt = Date.parse(proposal.expires_at ?? '')
  const expired = !Number.isFinite(expiresAt) || expiresAt <= openedAt
  return (
    <div className="w-full space-y-2 rounded border border-border p-3">
      <p>Review this action before approving. {expired ? 'Approval expired.' : 'Approval expires in 10 minutes from creation.'}</p>
      <dl className="space-y-1 break-all">
        {Object.entries(proposal.arguments ?? {}).map(([key, value]) => (
          <div key={key}><dt className="font-medium">{key.replaceAll('_', ' ')}</dt><dd className="whitespace-pre-wrap">{typeof value === 'string' ? value : JSON.stringify(value)}</dd></div>
        ))}
      </dl>
      <div className="flex gap-2">
        <Button size="sm" disabled={mutation.isPending || expired || !proposal.arguments} onClick={() => mutation.mutate('approve')}>Approve action</Button>
        <Button size="sm" variant="outline" disabled={mutation.isPending} onClick={() => mutation.mutate('reject')}>Reject action</Button>
      </div>
      {mutation.isError && <p role="alert">Could not confirm the action. Refresh and check its status before retrying.</p>}
    </div>
  )
}
