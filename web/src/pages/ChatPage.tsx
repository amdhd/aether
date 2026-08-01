import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ArrowDown,
  BookOpen,
  ChevronRight,
  Megaphone,
  Paperclip,
  Plus,
  Send,
  Smile,
  Sparkles,
  Square,
  X,
  Zap,
  type LucideIcon,
} from 'lucide-react'
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ChangeEvent,
  type KeyboardEvent,
} from 'react'
import ReactMarkdown from 'react-markdown'
import { useSearchParams } from 'react-router-dom'
import remarkGfm from 'remark-gfm'

import {
  CONVERSATIONS_PAGE_SIZE,
  CONVERSATION_PARAM,
  MAX_ATTACHMENT_BYTES,
  MAX_MESSAGE_CHARS,
  createConversation,
  deleteConversation,
  getConversation,
  listConversations,
  streamChatMessage,
  updateConversation,
} from '@/api/chat'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { useCrudMutations } from '@/hooks/useCrudMutations'
import { cn } from '@/lib/utils'
import type { Conversation, ConversationCreateInput, MessageRole, Persona } from '@/types'

// Short labels + icons for the welcome-screen persona picker.
const PERSONA_OPTIONS: { value: Persona; label: string; Icon: LucideIcon }[] = [
  { value: 'productivity_coach', label: 'Productivity', Icon: Zap },
  { value: 'marketing_coach', label: 'Marketing', Icon: Megaphone },
  { value: 'research_assistant', label: 'Research', Icon: BookOpen },
  { value: 'casual_friend', label: 'Casual', Icon: Smile },
]

function PersonaPicker({
  value,
  onSelect,
  disabled,
}: {
  value: Persona
  onSelect: (persona: Persona) => void
  disabled?: boolean
}) {
  return (
    <div
      role="group"
      aria-label="Assistant persona"
      className="inline-flex flex-wrap items-center justify-center gap-1 rounded-full border border-border bg-surface-muted p-1"
    >
      {PERSONA_OPTIONS.map(({ value: personaValue, label, Icon }) => {
        const active = personaValue === value
        return (
          <button
            key={personaValue}
            type="button"
            aria-pressed={active}
            disabled={disabled}
            onClick={() => onSelect(personaValue)}
            className={cn(
              'inline-flex items-center gap-2 rounded-full px-4 py-2 text-sm font-medium transition-colors focus-ring disabled:cursor-not-allowed disabled:opacity-60',
              active
                ? 'bg-foreground text-background shadow-sm'
                : 'text-muted-foreground hover:text-foreground',
            )}
          >
            <Icon className="h-4 w-4" />
            {label}
          </button>
        )
      })}
    </div>
  )
}

const ATTACHMENT_ACCEPT = '.csv,.tsv'

// Distance from the bottom that still counts as "following along". Roughly one
// line of prose, so a stray trackpad nudge doesn't detach the view.
const SCROLL_PIN_THRESHOLD_PX = 100

// Counting every keystroke down from zero is noise; the number only matters
// once the limit is close enough to be worth planning around.
const COUNTER_VISIBLE_FROM = Math.floor(MAX_MESSAGE_CHARS * 0.9)

const ATTACHMENT_LIMIT_LABEL = `${Math.round(MAX_ATTACHMENT_BYTES / 1000)} KB`

const PROSE =
  'text-[15px] leading-7 [&_a]:font-medium [&_a]:text-foreground [&_a]:underline [&_a]:underline-offset-2 [&_code]:rounded [&_code]:bg-surface-muted [&_code]:px-1.5 [&_code]:py-0.5 [&_code]:text-[13px] [&_h1]:mb-2 [&_h1]:mt-4 [&_h1]:text-lg [&_h1]:font-semibold [&_h2]:mb-2 [&_h2]:mt-4 [&_h2]:text-base [&_h2]:font-semibold [&_h3]:mb-1.5 [&_h3]:mt-3 [&_h3]:font-semibold [&_li]:mb-1 [&_ol]:mb-3 [&_ol]:list-decimal [&_ol]:pl-5 [&_p:last-child]:mb-0 [&_p]:mb-3 [&_pre]:mb-3 [&_pre]:overflow-x-auto [&_pre]:rounded-lg [&_pre]:border [&_pre]:border-border [&_pre]:bg-surface-muted [&_pre]:p-3 [&_pre]:text-[13px] [&_pre_code]:bg-transparent [&_pre_code]:p-0 [&_ul]:mb-3 [&_ul]:list-disc [&_ul]:pl-5'

const MARKDOWN_COMPONENTS = {
  table: ({ children, ...props }: React.ComponentPropsWithoutRef<'table'>) => (
    <div className="mb-3 w-full overflow-x-auto rounded-lg border border-border">
      <table className="w-full border-collapse text-[13px]" {...props}>
        {children}
      </table>
    </div>
  ),
  thead: ({ children, ...props }: React.ComponentPropsWithoutRef<'thead'>) => (
    <thead className="bg-surface-muted" {...props}>
      {children}
    </thead>
  ),
  th: ({ children, ...props }: React.ComponentPropsWithoutRef<'th'>) => (
    <th
      className="whitespace-nowrap border-b border-border px-3 py-2 text-left font-semibold"
      {...props}
    >
      {children}
    </th>
  ),
  td: ({ children, ...props }: React.ComponentPropsWithoutRef<'td'>) => (
    <td className="whitespace-nowrap border-b border-border/60 px-3 py-2 align-top" {...props}>
      {children}
    </td>
  ),
  tbody: ({ children, ...props }: React.ComponentPropsWithoutRef<'tbody'>) => (
    <tbody className="[&>tr:last-child>td]:border-b-0" {...props}>
      {children}
    </tbody>
  ),
}

function MessageContent({ content }: { content: string }) {
  return (
    <div className={PROSE}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={MARKDOWN_COMPONENTS}>
        {content}
      </ReactMarkdown>
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

// Transient feedback about one conversation's last turn. `error` reads as a
// failure; `stopped` is the user's own doing and stays neutral.
interface Notice {
  id: number
  kind: 'error' | 'stopped'
  message: string
}

export function ChatPage() {
  const queryClient = useQueryClient()
  // Selection lives in the URL (?c=<id>) so the sidebar history can drive it.
  const [searchParams, setSearchParams] = useSearchParams()
  const selectedParam = searchParams.get(CONVERSATION_PARAM)
  const selectedId = selectedParam !== null && /^\d+$/.test(selectedParam) ? Number(selectedParam) : null
  const setSelectedId = useCallback(
    (id: number | null) => {
      setSearchParams(
        (prev: URLSearchParams) => {
          const next = new URLSearchParams(prev)
          if (id === null) next.delete(CONVERSATION_PARAM)
          else next.set(CONVERSATION_PARAM, String(id))
          return next
        },
        { replace: true },
      )
    },
    [setSearchParams],
  )
  // What sits in the composer belongs to one conversation, so it's keyed by id
  // rather than held as a single value: switching chats must not carry a draft
  // — or, worse, an attachment — into the wrong one, and coming back should
  // find what you were part-way through writing.
  const [drafts, setDrafts] = useState<Record<number, string>>({})
  const [attachments, setAttachments] = useState<Record<number, File>>({})
  const [attachError, setAttachError] = useState<{ id: number; message: string } | null>(null)
  // Persona highlighted on the landing screen (no active conversation yet);
  // picking one starts a new chat with that persona.
  const [landingPersona, setLandingPersona] = useState<Persona>('productivity_coach')
  // Persona picked for a conversation but not yet confirmed by the server, so
  // the picker highlights on click instead of after the refetch round-trip.
  // Scoped to a conversation id so it can't leak onto the next chat.
  const [pendingPersona, setPendingPersona] = useState<{ id: number; persona: Persona } | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  // Which conversation the in-flight turn belongs to, so its optimistic bubble
  // and streamed reply can't be drawn under a different chat the user switches
  // to mid-stream. Null means nothing is streaming.
  const [streamingFor, setStreamingFor] = useState<number | null>(null)
  const [pendingUserContent, setPendingUserContent] = useState<string | null>(null)
  const [pendingAttachmentName, setPendingAttachmentName] = useState<string | null>(null)
  const [streamingContent, setStreamingContent] = useState('')
  const [streamingReasoning, setStreamingReasoning] = useState('')
  const [streamingToolCalls, setStreamingToolCalls] = useState<string[]>([])
  // Feedback about the last turn — a failure, or the note that a stopped reply
  // was discarded. Carries the conversation it belongs to, so it stays put
  // instead of surfacing over whichever chat the user opens next.
  const [notice, setNotice] = useState<Notice | null>(null)
  // Lets the Stop button tear down the in-flight request. Null when idle.
  const abortRef = useRef<AbortController | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  // Only follow the tail while the reader is already at it — otherwise scrolling
  // up to re-read something yanks you back down on the next token. State rather
  // than a ref because scrolling away also has to reveal the way back, and
  // scoped by id so a different chat starts at its own tail however this one
  // was left.
  const [pinned, setPinned] = useState<{ id: number; atBottom: boolean } | null>(null)

  // Same key as the sidebar history, so the two share one request.
  const { data: conversationsPage } = useQuery({
    queryKey: ['conversations', CONVERSATIONS_PAGE_SIZE],
    queryFn: () => listConversations(CONVERSATIONS_PAGE_SIZE),
  })
  const conversations = conversationsPage?.items ?? []

  const activeId = selectedId ?? conversations[0]?.id ?? null
  const isStreaming = streamingFor !== null
  // The turn belongs to the chat on screen, so its bubbles should be drawn.
  const isStreamingHere = streamingFor !== null && streamingFor === activeId
  // A turn is running, but for a different conversation than the one on screen.
  const isStreamingElsewhere = isStreaming && !isStreamingHere

  // A chat nobody has scrolled in yet is, by definition, at its tail.
  const pinnedToBottom = pinned?.id === activeId ? pinned.atBottom : true

  // What the composer shows: this conversation's contents, never another's.
  const draft = activeId === null ? '' : (drafts[activeId] ?? '')
  const attachedFile = activeId === null ? null : (attachments[activeId] ?? null)

  // Writes take an explicit id because they outlive the render that started
  // them — an in-flight turn must return its draft to the chat it was written
  // for, wherever the user has navigated to since.
  const setDraftFor = useCallback((id: number, value: string | ((prev: string) => string)) => {
    setDrafts((prev) => ({
      ...prev,
      [id]: typeof value === 'function' ? value(prev[id] ?? '') : value,
    }))
  }, [])
  const setAttachmentFor = useCallback(
    (id: number, file: File | null | ((prev: File | null) => File | null)) => {
      setAttachments((prev) => {
        const resolved = typeof file === 'function' ? file(prev[id] ?? null) : file
        const next = { ...prev }
        if (resolved) next[id] = resolved
        else delete next[id]
        return next
      })
    },
    [],
  )

  const {
    data: conversation,
    isLoading: conversationLoading,
    isError: conversationFailed,
    refetch: refetchConversation,
  } = useQuery({
    queryKey: ['conversation', activeId],
    queryFn: () => getConversation(activeId as number),
    enabled: activeId !== null,
  })

  const { createMutation } = useCrudMutations<Conversation, ConversationCreateInput>({
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
    },
  })

  const personaMutation = useMutation({
    mutationFn: ({ id, persona }: { id: number; persona: Persona }) => updateConversation(id, { persona }),
    onMutate: ({ id, persona }) => {
      setNotice(null)
      setPendingPersona({ id, persona })
    },
    // Hold the optimistic highlight until the refetch lands, otherwise it
    // flashes back to the old persona while the query is in flight.
    onSuccess: async (_data, { id }) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['conversation', id] }),
        queryClient.invalidateQueries({ queryKey: ['conversations'] }),
      ])
      setPendingPersona((pending) => (pending?.id === id ? null : pending))
    },
    onError: (_error, { id }) => {
      setPendingPersona((pending) => (pending?.id === id ? null : pending))
      setNotice({ id, kind: 'error', message: 'Could not switch persona. Please try again.' })
    },
  })

  // The conversation detail can still be loading (or have failed) while the
  // empty state is on screen, so fall back to the list row before the default.
  const activePersona: Persona =
    (pendingPersona?.id === activeId ? pendingPersona.persona : undefined) ??
    conversation?.persona ??
    conversations.find((c) => c.id === activeId)?.persona ??
    'productivity_coach'

  const handleScroll = () => {
    const el = scrollRef.current
    if (activeId === null || !el) return
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight <= SCROLL_PIN_THRESHOLD_PX
    setPinned((prev) =>
      prev?.id === activeId && prev.atBottom === atBottom ? prev : { id: activeId, atBottom },
    )
  }

  const scrollToBottom = () => {
    const el = scrollRef.current
    if (activeId === null || !el) return
    setPinned({ id: activeId, atBottom: true })
    el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
  }

  useEffect(() => {
    if (!pinnedToBottom) return
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [pinnedToBottom, conversation?.messages.length, streamingContent, pendingUserContent])

  // Grow the composer with what's being written, up to the CSS max height,
  // instead of scrolling a fixed one-line box and hiding what came before.
  // Layout effect, not a plain one, so the box is the right size before the
  // browser paints — otherwise every keystroke past a line break flickers.
  // `draft` covers switching conversations too, since it's derived per id.
  useLayoutEffect(() => {
    const el = textareaRef.current
    if (!el) return
    // Measuring against the current height would only ever ratchet upwards;
    // resetting first is what lets the box shrink back as text is deleted.
    el.style.height = 'auto'
    el.style.height = `${el.scrollHeight}px`
  }, [draft])

  const resetStreamingState = () => {
    setStreamingContent('')
    setStreamingReasoning('')
    setStreamingToolCalls([])
  }

  const handleFileSelect = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0] ?? null
    // The picker is only reachable from a conversation's composer.
    if (activeId === null) return
    setAttachError(null)
    if (file) {
      const name = file.name.toLowerCase()
      if (!name.endsWith('.csv') && !name.endsWith('.tsv')) {
        setAttachError({ id: activeId, message: 'Only .csv or .tsv files are supported.' })
        event.target.value = ''
        return
      }
      // Catching this here rather than letting the server reject it saves
      // uploading the whole file only to be told it was too big.
      if (file.size > MAX_ATTACHMENT_BYTES) {
        setAttachError({
          id: activeId,
          message: `That file is too large. Attachments must be under ${ATTACHMENT_LIMIT_LABEL}.`,
        })
        event.target.value = ''
        return
      }
    }
    setAttachmentFor(activeId, file)
    // Allow re-selecting the same file after removing it.
    event.target.value = ''
  }

  const handleSend = async () => {
    const content = draft.trim()
    if (activeId === null || !content || isStreaming || content.length > MAX_MESSAGE_CHARS) return

    const sendingTo = activeId
    const file = attachedFile
    const controller = new AbortController()
    abortRef.current = controller
    setDraftFor(sendingTo, '')
    setAttachmentFor(sendingTo, null)
    setAttachError(null)
    setPendingUserContent(content)
    setPendingAttachmentName(file?.name ?? null)
    resetStreamingState()
    setNotice(null)
    setStreamingFor(sendingTo)

    try {
      await streamChatMessage(
        sendingTo,
        content,
        {
          onToken: (chunk) => setStreamingContent((prev) => prev + chunk),
          onReasoning: (chunk) => setStreamingReasoning((prev) => prev + chunk),
          onToolCall: (name) => setStreamingToolCalls((prev) => [...prev, name]),
          onError: (message) => setNotice({ id: sendingTo, kind: 'error', message }),
        },
        file,
        controller.signal,
      )
    } catch (err) {
      // Stopping is a choice, not a failure: no error banner, and no handing the
      // message back — the server persisted it before the first token. But it
      // only persists a reply once the turn completes, so the partial text on
      // screen is about to disappear on the refetch below; say so rather than
      // letting it vanish unexplained.
      if (controller.signal.aborted) {
        setNotice({
          id: sendingTo,
          kind: 'stopped',
          message: 'You stopped this reply before it finished, so it wasn’t saved.',
        })
      } else {
        setNotice({
          id: sendingTo,
          kind: 'error',
          message: err instanceof Error ? err.message : 'Something went wrong. Please try again.',
        })
        // The turn never landed, so the composer was cleared for nothing. Hand
        // the message back rather than making them retype it — into the chat it
        // was written for, and never over something typed there since.
        setDraftFor(sendingTo, (current) => current || content)
        setAttachmentFor(sendingTo, (current) => current ?? file)
      }
    } finally {
      if (abortRef.current === controller) abortRef.current = null
      await queryClient.invalidateQueries({ queryKey: ['conversation', sendingTo] })
      await queryClient.invalidateQueries({ queryKey: ['conversations'] })
      setStreamingFor(null)
      setPendingUserContent(null)
      setPendingAttachmentName(null)
      resetStreamingState()
    }
  }

  const handleStop = () => abortRef.current?.abort()

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    // An IME uses Enter to accept a candidate word; sending on that would fire
    // the message off half-composed for CJK input.
    if (event.nativeEvent.isComposing) return
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      void handleSend()
    }
  }

  const visibleMessages = (conversation?.messages ?? []).filter(
    (message) => message.role !== 'tool' && message.content,
  )
  // A failed detail fetch must not fall through to the welcome screen: that
  // renders a persona picker with no conversation behind it, so every click is
  // silently dropped and the chat looks merely empty rather than broken.
  const isEmptyConversation =
    activeId !== null &&
    !conversationLoading &&
    !conversationFailed &&
    visibleMessages.length === 0 &&
    !isStreamingHere

  // Clicking "New chat" while already sitting in an unused one would just pile
  // up identical empty conversations in the history, so the button lands you in
  // the composer of the one you already have instead of creating another.
  const handleNewChat = () => {
    if (isEmptyConversation) {
      textareaRef.current?.focus()
      return
    }
    createMutation.mutate({})
  }

  // With nothing to read yet, the composer is the whole point of the screen, so
  // it sits with the greeting in the middle rather than pinned to the bottom of
  // an empty page. Once there's a transcript it returns to the bottom.
  const showWelcome = activeId === null || isEmptyConversation

  // The server rejects an over-long message with a 422 whose body is a
  // validation array, not a sentence — so catch it here, where we can say
  // something useful and the message is still in the box.
  const overLimit = draft.trim().length > MAX_MESSAGE_CHARS

  const composer = (
    <div className="w-full">
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
                onClick={() => activeId !== null && setAttachmentFor(activeId, null)}
              >
                <X className="h-3 w-3" />
              </button>
            </span>
          </div>
        )}
        <textarea
          ref={textareaRef}
          value={draft}
          onChange={(e) => activeId !== null && setDraftFor(activeId, e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={activeId === null ? 'Start a new conversation first' : 'Message Aether…'}
          aria-label="Message"
          // Only the absence of a conversation makes the composer unusable. A
          // turn in flight — here or in another chat — still lets you write the
          // next one; it's sending that has to wait.
          disabled={activeId === null}
          rows={1}
          className="block max-h-40 min-h-[52px] w-full resize-none overflow-y-auto rounded-2xl bg-transparent py-3.5 pl-12 pr-14 text-[15px] leading-6 placeholder:text-muted-foreground focus:outline-none disabled:cursor-not-allowed disabled:opacity-60"
        />
        <Button
          variant="ghost"
          size="icon"
          className="absolute bottom-2.5 left-2.5 h-9 w-9 rounded-lg text-muted-foreground"
          aria-label="Attach CSV file"
          onClick={() => fileInputRef.current?.click()}
          disabled={activeId === null}
        >
          <Paperclip className="h-4 w-4" />
        </Button>
        {isStreamingHere ? (
          <Button
            variant="outline"
            size="icon"
            className="absolute bottom-2.5 right-2.5 h-9 w-9 rounded-lg"
            aria-label="Stop generating"
            onClick={handleStop}
          >
            <Square className="h-3.5 w-3.5 fill-current" />
          </Button>
        ) : (
          <Button
            size="icon"
            className="absolute bottom-2.5 right-2.5 h-9 w-9 rounded-lg"
            aria-label="Send message"
            onClick={() => void handleSend()}
            disabled={activeId === null || !draft.trim() || isStreaming || overLimit}
          >
            <Send className="h-4 w-4" />
          </Button>
        )}
      </div>
      {attachError?.id === activeId && (
        <p className="px-1 pt-1 text-xs text-red-600 dark:text-red-400">{attachError.message}</p>
      )}
      {draft.length >= COUNTER_VISIBLE_FROM && (
        <p
          className={cn(
            'px-1 pt-1 text-right text-xs tabular-nums',
            overLimit ? 'text-red-600 dark:text-red-400' : 'text-muted-foreground',
          )}
        >
          {draft.trim().length.toLocaleString()} / {MAX_MESSAGE_CHARS.toLocaleString()}
        </p>
      )}
      <p className="py-2 text-center text-xs text-muted-foreground">
        {overLimit
          ? // Send is disabled; the counter just above says by how much.
            'This message is too long to send. Shorten it, or split it across two messages.'
          : isStreamingElsewhere
            ? // The server allows one turn per user at a time, so sending here
              // would be rejected. Name the reason instead of leaving a dead button.
              'Aether is replying in another chat. Wait for it to finish, or stop it there.'
            : `Aether can make mistakes. Attach a .csv (under ${ATTACHMENT_LIMIT_LABEL}) to analyze campaign data.`}
      </p>
    </div>
  )

  // Shown only in the conversation it happened in — a failed turn in one chat
  // has nothing to say about the one the user moved on to.
  const noticeBanner = notice?.id === activeId && (
    <p
      // Stopping is the user's own doing, so it stays neutral and merely polite
      // rather than interrupting as an alert.
      role={notice.kind === 'error' ? 'alert' : 'status'}
      className={cn(
        'mt-4 rounded-md border px-3 py-2 text-sm',
        notice.kind === 'error'
          ? 'border-red-200 bg-red-50 text-red-700 dark:border-red-900/50 dark:bg-red-950/40 dark:text-red-300'
          : 'border-border bg-surface-muted text-muted-foreground',
      )}
    >
      {notice.message}
    </p>
  )

  return (
    <div className="flex h-full flex-col">
      <div className="flex min-h-0 flex-1 flex-col">
        <div className="flex shrink-0 items-center justify-between gap-2 pb-4">
          <h1 className="min-w-0 truncate text-base font-semibold tracking-tight">
            {conversation?.title ?? 'Chat'}
          </h1>
          <Button
            variant="outline"
            size="sm"
            className="shrink-0"
            onClick={handleNewChat}
            disabled={createMutation.isPending}
          >
            <Plus className="h-4 w-4" />
            <span className="hidden sm:inline">New chat</span>
          </Button>
        </div>

        {showWelcome ? (
          // True vertical centering reads as low — the eye weighs the page
          // (and the header above this block) as part of the frame, so a
          // slight upward bias is what actually looks centered.
          <div className="flex min-h-0 flex-1 flex-col items-center justify-center overflow-y-auto pb-[12vh]">
            <div className="w-full max-w-3xl px-1 py-6">
              <div className="flex flex-col items-center text-center">
                <div className="flex items-center gap-3">
                  <Sparkles className="h-7 w-7 shrink-0 text-foreground" aria-hidden />
                  <h2 className="text-2xl font-semibold tracking-tight">Start chatting with Aether</h2>
                </div>
                <p className="mt-2 max-w-md text-sm text-muted-foreground">
                  {activeId === null
                    ? 'Choose a persona to start a new conversation — ask about your tasks, notes, campaigns, the weather, or anything else.'
                    : 'Choose a persona, then ask about your tasks, notes, campaigns, the weather, or anything else.'}
                </p>
                <div className="mt-6">
                  {activeId === null ? (
                    <PersonaPicker
                      value={landingPersona}
                      disabled={createMutation.isPending}
                      onSelect={(persona) => {
                        setLandingPersona(persona)
                        createMutation.mutate({ persona })
                      }}
                    />
                  ) : (
                    <PersonaPicker
                      value={activePersona}
                      onSelect={(persona) => personaMutation.mutate({ id: activeId, persona })}
                    />
                  )}
                </div>
              </div>
              <div className="mt-8">{composer}</div>
              {noticeBanner}
            </div>
          </div>
        ) : (
          <>
            {/* Anchors the jump-to-latest button over the tail of the
                transcript so it doesn't scroll away with the content. */}
            <div className="relative flex min-h-0 flex-1 flex-col">
              <div ref={scrollRef} onScroll={handleScroll} className="min-h-0 flex-1 overflow-y-auto">
                <div className="mx-auto w-full max-w-3xl px-1 pb-6">
                  {conversationLoading ? (
                    <div className="space-y-6 pt-2">
                      {[...Array(3)].map((_, i) => (
                        <Skeleton key={i} className="h-16 w-2/3" />
                      ))}
                    </div>
                  ) : conversationFailed ? (
                    <div className="flex min-h-[50vh] flex-col items-center justify-center text-center">
                      <h2 className="text-lg font-semibold tracking-tight">Couldn’t load this conversation</h2>
                      <p className="mt-2 max-w-md text-sm text-muted-foreground">
                        Its messages and persona are unavailable right now.
                      </p>
                      <Button
                        variant="outline"
                        size="sm"
                        className="mt-4"
                        onClick={() => void refetchConversation()}
                      >
                        Try again
                      </Button>
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
                      {isStreamingHere && pendingUserContent !== null && (
                        <Message
                          role="user"
                          content={pendingUserContent}
                          attachmentName={pendingAttachmentName}
                        />
                      )}
                      {isStreamingHere && (
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
                  {noticeBanner}
                </div>
              </div>

              {/* Scrolling up to re-read something detaches the view from the
                  tail, which used to leave no way back but scrolling the whole
                  reply by hand. */}
              {!pinnedToBottom && (
                <Button
                  variant="outline"
                  size="icon"
                  className="absolute inset-x-0 bottom-3 mx-auto h-8 w-8 rounded-full bg-surface shadow-md"
                  aria-label="Jump to latest"
                  onClick={scrollToBottom}
                >
                  <ArrowDown className="h-4 w-4" />
                </Button>
              )}
            </div>

            <div className="mx-auto w-full max-w-3xl px-1 pt-2">{composer}</div>
          </>
        )}
      </div>
    </div>
  )
}
