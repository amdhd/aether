import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Brain, Plus, Send, Trash2, Wrench } from 'lucide-react'
import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

import {
  createConversation,
  deleteConversation,
  getConversation,
  listConversations,
  streamChatMessage,
  updateConversation,
} from '@/api/chat'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Textarea } from '@/components/ui/textarea'
import { useCrudMutations } from '@/hooks/useCrudMutations'
import { cn } from '@/lib/utils'
import type { Conversation, ConversationCreateInput, MessageRole, Persona } from '@/types'

const CONVERSATIONS_PAGE_SIZE = 50
const CONVERSATIONS_MAX_LIMIT = 100

const PERSONA_LABELS: Record<Persona, string> = {
  productivity_coach: 'Productivity Coach',
  research_assistant: 'Research Assistant',
  casual_friend: 'Casual Friend',
}

function MessageContent({ content }: { content: string }) {
  return (
    <div className="text-sm leading-relaxed [&_a]:text-brand-600 [&_a]:underline [&_code]:rounded [&_code]:bg-surface-muted [&_code]:px-1 [&_code]:py-0.5 [&_code]:text-xs [&_ol]:mb-2 [&_ol]:list-decimal [&_ol]:pl-5 [&_p:last-child]:mb-0 [&_p]:mb-2 [&_pre]:mb-2 [&_pre]:overflow-x-auto [&_pre]:rounded-md [&_pre]:bg-slate-900 [&_pre]:p-3 [&_pre]:text-slate-50 [&_pre_code]:bg-transparent [&_pre_code]:p-0 [&_ul]:mb-2 [&_ul]:list-disc [&_ul]:pl-5">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
    </div>
  )
}

function ThinkingBlock({ content }: { content: string }) {
  return (
    <details className="mb-2 rounded-md border border-border bg-surface-muted px-3 py-2 text-xs text-muted-foreground">
      <summary className="flex cursor-pointer items-center gap-1.5 font-medium">
        <Brain className="h-3.5 w-3.5" />
        Thinking
      </summary>
      <p className="mt-2 whitespace-pre-wrap">{content}</p>
    </details>
  )
}

function ChatBubble({
  role,
  content,
  reasoningContent,
}: {
  role: MessageRole
  content: string
  reasoningContent?: string | null
}) {
  const isUser = role === 'user'
  return (
    <div className={cn('flex', isUser ? 'justify-end' : 'justify-start')}>
      <div
        className={cn(
          'max-w-[85%] rounded-lg px-3 py-2',
          isUser ? 'whitespace-pre-wrap bg-brand-600 text-sm text-white' : 'bg-surface-muted text-foreground',
        )}
      >
        {isUser ? (
          content
        ) : (
          <>
            {reasoningContent && <ThinkingBlock content={reasoningContent} />}
            <MessageContent content={content} />
          </>
        )}
      </div>
    </div>
  )
}

export function ChatPage() {
  const queryClient = useQueryClient()
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [draft, setDraft] = useState('')
  const [isStreaming, setIsStreaming] = useState(false)
  const [pendingUserContent, setPendingUserContent] = useState<string | null>(null)
  const [streamingContent, setStreamingContent] = useState('')
  const [streamingReasoning, setStreamingReasoning] = useState('')
  const [streamingToolCalls, setStreamingToolCalls] = useState<string[]>([])
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [conversationsLimit, setConversationsLimit] = useState(CONVERSATIONS_PAGE_SIZE)
  const [deletingConversationId, setDeletingConversationId] = useState<number | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)

  const { data: conversationsPage, isLoading: conversationsLoading } = useQuery({
    queryKey: ['conversations', conversationsLimit],
    queryFn: () => listConversations(conversationsLimit),
  })
  const conversations = conversationsPage?.items ?? []
  const canLoadMoreConversations =
    conversationsPage !== undefined &&
    conversations.length < conversationsPage.total &&
    conversationsLimit < CONVERSATIONS_MAX_LIMIT

  const activeId = selectedId ?? conversations[0]?.id ?? null

  const { data: conversation, isLoading: conversationLoading } = useQuery({
    queryKey: ['conversation', activeId],
    queryFn: () => getConversation(activeId as number),
    enabled: activeId !== null,
  })

  const { createMutation, deleteMutation } = useCrudMutations<Conversation, ConversationCreateInput>({
    queryKey: ['conversations'],
    create: createConversation,
    update: updateConversation,
    remove: deleteConversation,
    entityName: 'Conversation',
    toastSuccess: false,
    onCreateSuccess: (created) => setSelectedId(created.id),
    onDeleteSuccess: (deletedId) => {
      queryClient.removeQueries({ queryKey: ['conversation', deletedId] })
      if (activeId === deletedId) {
        setSelectedId(null)
      }
      setDeletingConversationId(null)
    },
  })

  const personaMutation = useMutation({
    mutationFn: ({ id, persona }: { id: number; persona: Persona }) => updateConversation(id, { persona }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['conversation', activeId] })
      queryClient.invalidateQueries({ queryKey: ['conversations'] })
    },
  })

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [conversation?.messages.length, streamingContent, pendingUserContent])

  const resetStreamingState = () => {
    setStreamingContent('')
    setStreamingReasoning('')
    setStreamingToolCalls([])
  }

  const handleSend = async () => {
    const content = draft.trim()
    if (activeId === null || !content || isStreaming) return

    setDraft('')
    setPendingUserContent(content)
    resetStreamingState()
    setErrorMessage(null)
    setIsStreaming(true)

    try {
      await streamChatMessage(activeId, content, {
        onToken: (chunk) => setStreamingContent((prev) => prev + chunk),
        onReasoning: (chunk) => setStreamingReasoning((prev) => prev + chunk),
        onToolCall: (name) => setStreamingToolCalls((prev) => [...prev, name]),
        onError: (message) => setErrorMessage(message),
      })
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : 'Something went wrong. Please try again.')
    } finally {
      await queryClient.invalidateQueries({ queryKey: ['conversation', activeId] })
      await queryClient.invalidateQueries({ queryKey: ['conversations'] })
      setIsStreaming(false)
      setPendingUserContent(null)
      resetStreamingState()
    }
  }

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      void handleSend()
    }
  }

  const visibleMessages = (conversation?.messages ?? []).filter(
    (message) => message.role !== 'tool' && message.content,
  )

  return (
    <div className="flex h-[75vh] gap-4">
      <aside className="hidden w-64 flex-col gap-2 sm:flex">
        <Button onClick={() => createMutation.mutate({})} disabled={createMutation.isPending}>
          <Plus className="h-4 w-4" />
          New chat
        </Button>
        <div className="flex-1 space-y-1 overflow-y-auto">
          {conversationsLoading ? (
            [...Array(3)].map((_, i) => <Skeleton key={i} className="h-10 w-full" />)
          ) : conversations.length === 0 ? (
            <p className="p-2 text-sm text-muted-foreground">No conversations yet.</p>
          ) : (
            <>
              {conversations.map((c) => (
                <div
                  key={c.id}
                  className={cn(
                    'group flex items-center gap-1 rounded-md px-2 py-2 text-sm',
                    c.id === activeId
                      ? 'bg-brand-50 text-brand-700 dark:bg-brand-500/15 dark:text-brand-200'
                      : 'hover:bg-surface-muted',
                  )}
                >
                  <button className="flex-1 truncate text-left focus-ring" onClick={() => setSelectedId(c.id)}>
                    {c.title}
                  </button>
                  <button
                    aria-label={`Delete conversation ${c.title}`}
                    className="opacity-0 focus-ring group-hover:opacity-100"
                    onClick={() => setDeletingConversationId(c.id)}
                  >
                    <Trash2 className="h-3.5 w-3.5 text-muted-foreground hover:text-red-600" />
                  </button>
                </div>
              ))}
              {canLoadMoreConversations && (
                <Button
                  variant="ghost"
                  size="sm"
                  className="w-full"
                  onClick={() => setConversationsLimit((prev) => Math.min(prev + CONVERSATIONS_PAGE_SIZE, CONVERSATIONS_MAX_LIMIT))}
                >
                  Load more
                </Button>
              )}
            </>
          )}
        </div>
      </aside>

      <div className="flex flex-1 flex-col">
        {conversations.length > 0 && (
          <Select
            value={activeId !== null ? String(activeId) : undefined}
            onValueChange={(value) => setSelectedId(Number(value))}
          >
            <SelectTrigger aria-label="Select conversation" className="mb-2 sm:hidden">
              <SelectValue placeholder="Select a conversation" />
            </SelectTrigger>
            <SelectContent>
              {conversations.map((c) => (
                <SelectItem key={c.id} value={String(c.id)}>
                  {c.title}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
        <div className="mb-3 flex items-center justify-between gap-2 border-b border-border pb-3">
          <div className="min-w-0">
            <h1 className="truncate text-lg font-semibold">{conversation?.title ?? 'Chat'}</h1>
            <p className="text-sm text-muted-foreground">Your AI assistant.</p>
          </div>
          <Button
            variant="outline"
            size="icon"
            className="shrink-0 sm:hidden"
            aria-label="New chat"
            onClick={() => createMutation.mutate({})}
            disabled={createMutation.isPending}
          >
            <Plus className="h-4 w-4" />
          </Button>
          {conversation && (
            <Select
              value={conversation.persona}
              onValueChange={(value) => personaMutation.mutate({ id: conversation.id, persona: value as Persona })}
            >
              <SelectTrigger aria-label="Assistant persona" className="w-48 shrink-0">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {Object.entries(PERSONA_LABELS).map(([value, label]) => (
                  <SelectItem key={value} value={value}>
                    {label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        </div>

        <div ref={scrollRef} className="flex-1 space-y-4 overflow-y-auto rounded-card border border-border bg-surface p-4">
          {activeId === null ? (
            <p className="text-sm text-muted-foreground">Start a new conversation to begin chatting with Aether.</p>
          ) : conversationLoading ? (
            <div className="space-y-3">
              {[...Array(3)].map((_, i) => (
                <Skeleton key={i} className="h-16 w-2/3" />
              ))}
            </div>
          ) : (
            <>
              {visibleMessages.length === 0 && pendingUserContent === null && (
                <p className="text-sm text-muted-foreground">Send a message to get started.</p>
              )}
              {visibleMessages.map((message) => (
                <ChatBubble
                  key={message.id}
                  role={message.role}
                  content={message.content ?? ''}
                  reasoningContent={message.reasoning_content}
                />
              ))}
              {pendingUserContent !== null && <ChatBubble role="user" content={pendingUserContent} />}
              {isStreaming && (
                <div className="space-y-2">
                  {streamingToolCalls.map((name, i) => (
                    <Badge key={i} variant="secondary" className="gap-1">
                      <Wrench className="h-3 w-3" />
                      Using {name}...
                    </Badge>
                  ))}
                  {streamingContent ? (
                    <ChatBubble role="assistant" content={streamingContent} reasoningContent={streamingReasoning} />
                  ) : streamingReasoning ? (
                    <ChatBubble role="assistant" content="" reasoningContent={streamingReasoning} />
                  ) : (
                    <p className="text-sm text-muted-foreground">Aether is thinking…</p>
                  )}
                </div>
              )}
            </>
          )}
          {errorMessage && <p className="text-sm text-red-600">{errorMessage}</p>}
        </div>

        <div className="mt-3 flex gap-2">
          <Textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={activeId === null ? 'Start a new conversation first' : 'Message Aether...'}
            aria-label="Message"
            disabled={activeId === null || isStreaming}
            className="min-h-[44px] flex-1 resize-none"
            rows={1}
          />
          <Button
            aria-label="Send message"
            onClick={() => void handleSend()}
            disabled={activeId === null || !draft.trim() || isStreaming}
          >
            <Send className="h-4 w-4" />
          </Button>
        </div>
      </div>

      <ConfirmDialog
        open={deletingConversationId !== null}
        onOpenChange={(open) => !open && setDeletingConversationId(null)}
        title="Delete this conversation?"
        description="This action cannot be undone."
        isConfirming={deleteMutation.isPending}
        onConfirm={() => deletingConversationId !== null && deleteMutation.mutate(deletingConversationId)}
      />
    </div>
  )
}
