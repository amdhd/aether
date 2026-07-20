import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronRight, Paperclip, Plus, Send, Trash2, X } from 'lucide-react'
import { useEffect, useRef, useState, type ChangeEvent, type KeyboardEvent } from 'react'
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
import { Button } from '@/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { useCrudMutations } from '@/hooks/useCrudMutations'
import { cn } from '@/lib/utils'
import type { Conversation, ConversationCreateInput, MessageRole, Persona } from '@/types'

const CONVERSATIONS_PAGE_SIZE = 50
const CONVERSATIONS_MAX_LIMIT = 100

const PERSONA_LABELS: Record<Persona, string> = {
  productivity_coach: 'Productivity Coach',
  research_assistant: 'Research Assistant',
  casual_friend: 'Casual Friend',
  marketing_coach: 'Marketing Coach',
}

const ATTACHMENT_ACCEPT = '.csv,.tsv'

const PROSE =
  'text-[15px] leading-7 [&_a]:font-medium [&_a]:text-foreground [&_a]:underline [&_a]:underline-offset-2 [&_code]:rounded [&_code]:bg-surface-muted [&_code]:px-1.5 [&_code]:py-0.5 [&_code]:text-[13px] [&_h1]:mb-2 [&_h1]:mt-4 [&_h1]:text-lg [&_h1]:font-semibold [&_h2]:mb-2 [&_h2]:mt-4 [&_h2]:text-base [&_h2]:font-semibold [&_h3]:mb-1.5 [&_h3]:mt-3 [&_h3]:font-semibold [&_li]:mb-1 [&_ol]:mb-3 [&_ol]:list-decimal [&_ol]:pl-5 [&_p:last-child]:mb-0 [&_p]:mb-3 [&_pre]:mb-3 [&_pre]:overflow-x-auto [&_pre]:rounded-lg [&_pre]:border [&_pre]:border-border [&_pre]:bg-surface-muted [&_pre]:p-3 [&_pre]:text-[13px] [&_pre_code]:bg-transparent [&_pre_code]:p-0 [&_ul]:mb-3 [&_ul]:list-disc [&_ul]:pl-5'

function MessageContent({ content }: { content: string }) {
  return (
    <div className={PROSE}>
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
    </div>
  )
}

function ThinkingBlock({ content }: { content: string }) {
  return (
    <details className="group/think mb-3 text-sm text-muted-foreground">
      <summary className="inline-flex cursor-pointer list-none items-center gap-1 font-medium text-muted-foreground transition-colors hover:text-foreground focus-ring">
        <ChevronRight className="h-3.5 w-3.5 transition-transform group-open/think:rotate-90" />
        Thought process
      </summary>
      <p className="mt-2 whitespace-pre-wrap border-l border-border pl-3 leading-6">{content}</p>
    </details>
  )
}

function AttachmentChip({ name }: { name: string }) {
  return (
    <span className="inline-flex max-w-full items-center gap-1.5 rounded-md border border-border bg-surface px-2 py-1 text-xs text-muted-foreground">
      <Paperclip className="h-3 w-3 shrink-0" />
      <span className="truncate">{name}</span>
    </span>
  )
}

function Message({
  role,
  content,
  reasoningContent,
  attachmentName,
}: {
  role: MessageRole
  content: string
  reasoningContent?: string | null
  attachmentName?: string | null
}) {
  if (role === 'user') {
    return (
      <div className="flex flex-col items-end gap-1.5">
        {attachmentName && <AttachmentChip name={attachmentName} />}
        <div className="max-w-[80%] whitespace-pre-wrap rounded-2xl rounded-br-sm bg-surface-muted px-4 py-2.5 text-[15px] leading-7">
          {content}
        </div>
      </div>
    )
  }

  return (
    <div className="flex gap-3">
      <div
        aria-hidden
        className="mt-0.5 flex h-7 w-7 shrink-0 select-none items-center justify-center rounded-full bg-foreground text-[13px] font-semibold text-background"
      >
        A
      </div>
      <div className="min-w-0 flex-1 pt-0.5">
        {reasoningContent && <ThinkingBlock content={reasoningContent} />}
        <MessageContent content={content} />
      </div>
    </div>
  )
}

export function ChatPage() {
  const queryClient = useQueryClient()
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [draft, setDraft] = useState('')
  const [attachedFile, setAttachedFile] = useState<File | null>(null)
  const [attachError, setAttachError] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [isStreaming, setIsStreaming] = useState(false)
  const [pendingUserContent, setPendingUserContent] = useState<string | null>(null)
  const [pendingAttachmentName, setPendingAttachmentName] = useState<string | null>(null)
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

  const handleFileSelect = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0] ?? null
    setAttachError(null)
    if (file) {
      const name = file.name.toLowerCase()
      if (!name.endsWith('.csv') && !name.endsWith('.tsv')) {
        setAttachError('Only .csv or .tsv files are supported.')
        event.target.value = ''
        return
      }
    }
    setAttachedFile(file)
    // Allow re-selecting the same file after removing it.
    event.target.value = ''
  }

  const handleSend = async () => {
    const content = draft.trim()
    if (activeId === null || !content || isStreaming) return

    const file = attachedFile
    setDraft('')
    setAttachedFile(null)
    setAttachError(null)
    setPendingUserContent(content)
    setPendingAttachmentName(file?.name ?? null)
    resetStreamingState()
    setErrorMessage(null)
    setIsStreaming(true)

    try {
      await streamChatMessage(
        activeId,
        content,
        {
          onToken: (chunk) => setStreamingContent((prev) => prev + chunk),
          onReasoning: (chunk) => setStreamingReasoning((prev) => prev + chunk),
          onToolCall: (name) => setStreamingToolCalls((prev) => [...prev, name]),
          onError: (message) => setErrorMessage(message),
        },
        file,
      )
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : 'Something went wrong. Please try again.')
    } finally {
      await queryClient.invalidateQueries({ queryKey: ['conversation', activeId] })
      await queryClient.invalidateQueries({ queryKey: ['conversations'] })
      setIsStreaming(false)
      setPendingUserContent(null)
      setPendingAttachmentName(null)
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
  const isEmptyConversation =
    activeId !== null &&
    !conversationLoading &&
    visibleMessages.length === 0 &&
    pendingUserContent === null &&
    !isStreaming

  return (
    <div className="flex h-[calc(100vh-2rem)] gap-6 sm:h-[calc(100vh-3rem)]">
      <aside className="hidden w-64 flex-col gap-3 sm:flex">
        <Button onClick={() => createMutation.mutate({})} disabled={createMutation.isPending}>
          <Plus className="h-4 w-4" />
          New chat
        </Button>
        <div className="flex-1 space-y-0.5 overflow-y-auto">
          {conversationsLoading ? (
            [...Array(3)].map((_, i) => <Skeleton key={i} className="h-9 w-full" />)
          ) : conversations.length === 0 ? (
            <p className="px-2 py-1.5 text-sm text-muted-foreground">No conversations yet.</p>
          ) : (
            <>
              {conversations.map((c) => (
                <div
                  key={c.id}
                  className={cn(
                    'group flex items-center gap-1 rounded-md px-2.5 py-2 text-sm transition-colors',
                    c.id === activeId
                      ? 'bg-surface-muted font-medium text-foreground'
                      : 'text-muted-foreground hover:bg-surface-muted/60 hover:text-foreground',
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
                  className="w-full text-muted-foreground"
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
            <SelectTrigger aria-label="Select conversation" className="mb-3 sm:hidden">
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

        <div className="flex items-center justify-between gap-2 pb-4">
          <h1 className="min-w-0 truncate text-base font-semibold tracking-tight">
            {conversation?.title ?? 'Chat'}
          </h1>
          <div className="flex items-center gap-2">
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
                <SelectTrigger aria-label="Assistant persona" className="h-9 w-44 shrink-0 text-sm">
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
        </div>

        <div ref={scrollRef} className="flex-1 overflow-y-auto">
          <div className="mx-auto w-full max-w-3xl px-1 pb-6">
            {activeId === null ? (
              <div className="flex min-h-[50vh] flex-col items-center justify-center text-center">
                <h2 className="text-2xl font-semibold tracking-tight">Start a new conversation</h2>
                <p className="mt-2 text-sm text-muted-foreground">
                  Create a chat from the sidebar to begin talking with Aether.
                </p>
              </div>
            ) : conversationLoading ? (
              <div className="space-y-6 pt-2">
                {[...Array(3)].map((_, i) => (
                  <Skeleton key={i} className="h-16 w-2/3" />
                ))}
              </div>
            ) : isEmptyConversation ? (
              <div className="flex min-h-[50vh] flex-col items-center justify-center text-center">
                <h2 className="text-2xl font-semibold tracking-tight">How can I help?</h2>
                <p className="mt-2 text-sm text-muted-foreground">
                  Ask about your tasks, notes, the weather, or anything else.
                </p>
              </div>
            ) : (
              <div className="space-y-6 pt-2">
                {visibleMessages.map((message) => (
                  <Message
                    key={message.id}
                    role={message.role}
                    content={message.content ?? ''}
                    reasoningContent={message.reasoning_content}
                    attachmentName={message.attachment_name}
                  />
                ))}
                {pendingUserContent !== null && (
                  <Message role="user" content={pendingUserContent} attachmentName={pendingAttachmentName} />
                )}
                {isStreaming && (
                  <div className="flex gap-3">
                    <div
                      aria-hidden
                      className="mt-0.5 flex h-7 w-7 shrink-0 select-none items-center justify-center rounded-full bg-foreground text-[13px] font-semibold text-background"
                    >
                      A
                    </div>
                    <div
                      className="min-w-0 flex-1 pt-0.5"
                      // Announce the assistant's reply to screen readers as it
                      // streams in, rather than leaving them silent until the
                      // query refetch swaps in the final message.
                      aria-live="polite"
                      aria-atomic="false"
                      aria-busy={isStreaming}
                    >
                      {streamingToolCalls.map((name, i) => (
                        <p key={i} className="mb-2 flex items-center gap-2 text-sm text-muted-foreground">
                          <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-muted-foreground" />
                          Using <span className="font-medium text-foreground">{name}</span>
                        </p>
                      ))}
                      {streamingReasoning && <ThinkingBlock content={streamingReasoning} />}
                      {streamingContent ? (
                        <MessageContent content={streamingContent} />
                      ) : (
                        !streamingReasoning && (
                          <p className="flex items-center gap-1 py-1 text-sm text-muted-foreground">
                            <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted-foreground [animation-delay:-0.3s]" />
                            <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted-foreground [animation-delay:-0.15s]" />
                            <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted-foreground" />
                          </p>
                        )
                      )}
                    </div>
                  </div>
                )}
              </div>
            )}
            {errorMessage && (
              <p className="mt-4 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900/50 dark:bg-red-950/40 dark:text-red-300">
                {errorMessage}
              </p>
            )}
          </div>
        </div>

        <div className="mx-auto w-full max-w-3xl px-1 pt-2">
          <input
            ref={fileInputRef}
            type="file"
            accept={ATTACHMENT_ACCEPT}
            className="hidden"
            aria-hidden
            onChange={handleFileSelect}
          />
          <div className="relative rounded-2xl border border-border bg-surface shadow-sm transition-colors focus-within:border-foreground/25">
            {attachedFile && (
              <div className="flex px-3 pt-3">
                <span className="inline-flex max-w-full items-center gap-1.5 rounded-md border border-border bg-surface-muted px-2 py-1 text-xs text-muted-foreground">
                  <Paperclip className="h-3 w-3 shrink-0" />
                  <span className="truncate">{attachedFile.name}</span>
                  <button
                    type="button"
                    aria-label="Remove attachment"
                    className="focus-ring shrink-0 hover:text-foreground"
                    onClick={() => setAttachedFile(null)}
                  >
                    <X className="h-3 w-3" />
                  </button>
                </span>
              </div>
            )}
            <textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={activeId === null ? 'Start a new conversation first' : 'Message Aether…'}
              aria-label="Message"
              disabled={activeId === null || isStreaming}
              rows={1}
              className="block max-h-40 min-h-[52px] w-full resize-none rounded-2xl bg-transparent py-3.5 pl-12 pr-14 text-[15px] leading-6 placeholder:text-muted-foreground focus:outline-none disabled:cursor-not-allowed disabled:opacity-60"
            />
            <Button
              variant="ghost"
              size="icon"
              className="absolute bottom-2.5 left-2.5 h-9 w-9 rounded-lg text-muted-foreground"
              aria-label="Attach CSV file"
              onClick={() => fileInputRef.current?.click()}
              disabled={activeId === null || isStreaming}
            >
              <Paperclip className="h-4 w-4" />
            </Button>
            <Button
              size="icon"
              className="absolute bottom-2.5 right-2.5 h-9 w-9 rounded-lg"
              aria-label="Send message"
              onClick={() => void handleSend()}
              disabled={activeId === null || !draft.trim() || isStreaming}
            >
              <Send className="h-4 w-4" />
            </Button>
          </div>
          {attachError && <p className="px-1 pt-1 text-xs text-red-600 dark:text-red-400">{attachError}</p>}
          <p className="py-2 text-center text-xs text-muted-foreground">
            Aether can make mistakes. Attach a .csv to analyze campaign data. Press Enter to send, Shift+Enter for a new line.
          </p>
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
