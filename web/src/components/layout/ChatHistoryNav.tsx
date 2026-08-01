import { ChevronDown } from 'lucide-react'
import { useState } from 'react'

import { ConversationList } from '@/components/layout/ConversationList'
import { cn } from '@/lib/utils'

// The sidebar is a peek at recent chats, not the full archive.
const HISTORY_LIMIT = 5

/** The desktop sidebar's collapsible slice of chat history. */
export function ChatHistoryNav() {
  const [open, setOpen] = useState(true)

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
        <div className="mt-0.5 pl-3">
          <ConversationList limit={HISTORY_LIMIT} />
        </div>
      )}
    </div>
  )
}
