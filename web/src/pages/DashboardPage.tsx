import { useQuery } from '@tanstack/react-query'
import { ArrowRight, CheckCircle2, CircleDashed, ListTodo, MessageSquare, NotebookPen } from 'lucide-react'
import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'

import { getAnalyticsSummary } from '@/api/analytics'
import { listNotes } from '@/api/notes'
import { listTasks } from '@/api/tasks'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { useAuthStore } from '@/store/auth'
import type { Note, TaskStatus } from '@/types'

const STATUS_LABELS: Record<TaskStatus, string> = { todo: 'To do', doing: 'Doing', done: 'Done' }

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

export function DashboardPage() {
  const user = useAuthStore((state) => state.user)

  // Reuse the exact query keys the Tasks/Notes pages use so the dashboard
  // shares their cache (and reflects optimistic task moves immediately).
  const tasksQuery = useQuery({ queryKey: ['tasks'], queryFn: listTasks })
  const notesQuery = useQuery({ queryKey: ['notes', '', 50], queryFn: () => listNotes(undefined, 50) })
  const analyticsQuery = useQuery({
    queryKey: ['analytics', 'summary', 7],
    queryFn: () => getAnalyticsSummary(7),
  })

  const tasks = tasksQuery.data?.items ?? []
  const counts: Record<TaskStatus, number> = { todo: 0, doing: 0, done: 0 }
  for (const task of tasks) counts[task.status] += 1

  const recentTasks = [...tasks].sort((a, b) => b.created_at.localeCompare(a.created_at)).slice(0, 5)
  const recentNotes = (notesQuery.data?.items ?? []).slice(0, 5)
  const messagesThisWeek = analyticsQuery.data?.messages_per_day.reduce((sum, day) => sum + day.count, 0) ?? 0

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Welcome back{user ? `, ${user.name}` : ''}</h1>
          <p className="text-muted-foreground">Here&apos;s a quick look at your assistant.</p>
        </div>
        <Link
          to="/chat"
          className="inline-flex items-center gap-1.5 rounded-md text-sm font-medium text-brand-600 hover:text-brand-700 focus-ring dark:text-brand-300"
        >
          Open chat
          <ArrowRight className="h-4 w-4" />
        </Link>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatTile icon={<ListTodo className="h-4 w-4" />} label="To do" value={counts.todo} loading={tasksQuery.isLoading} />
        <StatTile icon={<CircleDashed className="h-4 w-4" />} label="In progress" value={counts.doing} loading={tasksQuery.isLoading} />
        <StatTile icon={<CheckCircle2 className="h-4 w-4" />} label="Done" value={counts.done} loading={tasksQuery.isLoading} />
        <StatTile
          icon={<MessageSquare className="h-4 w-4" />}
          label="Messages this week"
          value={messagesThisWeek}
          loading={analyticsQuery.isLoading}
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <RecentCard title="Recent tasks" icon={<ListTodo className="h-4 w-4" />} to="/tasks" isLoading={tasksQuery.isLoading}>
          {recentTasks.length === 0 ? (
            <EmptyRow>No tasks yet.</EmptyRow>
          ) : (
            recentTasks.map((task) => (
              <li key={task.id} className="flex items-center justify-between gap-2 py-2">
                <span className="min-w-0 truncate text-sm">{task.title}</span>
                <Badge variant="secondary" className="shrink-0">
                  {STATUS_LABELS[task.status]}
                </Badge>
              </li>
            ))
          )}
        </RecentCard>

        <RecentCard title="Recent notes" icon={<NotebookPen className="h-4 w-4" />} to="/notes" isLoading={notesQuery.isLoading}>
          {recentNotes.length === 0 ? (
            <EmptyRow>No notes yet.</EmptyRow>
          ) : (
            recentNotes.map((note: Note) => (
              <li key={note.id} className="flex items-center justify-between gap-2 py-2">
                <span className="min-w-0 truncate text-sm">{note.title}</span>
                <span className="shrink-0 text-xs text-muted-foreground">{formatDate(note.updated_at)}</span>
              </li>
            ))
          )}
        </RecentCard>
      </div>
    </div>
  )
}

function StatTile({ icon, label, value, loading }: { icon: ReactNode; label: string; value: number; loading: boolean }) {
  return (
    <Card>
      <CardContent className="flex items-center gap-3 p-4">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-brand-50 text-brand-600 dark:bg-brand-500/15 dark:text-brand-300">
          {icon}
        </div>
        <div className="min-w-0">
          <p className="truncate text-xs text-muted-foreground">{label}</p>
          {loading ? (
            <Skeleton className="mt-1 h-6 w-10" />
          ) : (
            <p className="text-lg font-semibold leading-tight">{value.toLocaleString()}</p>
          )}
        </div>
      </CardContent>
    </Card>
  )
}

function RecentCard({
  title,
  icon,
  to,
  isLoading,
  children,
}: {
  title: string
  icon: ReactNode
  to: string
  isLoading: boolean
  children: ReactNode
}) {
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="flex items-center gap-2 text-base">
          {icon}
          {title}
        </CardTitle>
        <Link to={to} className="text-sm font-medium text-brand-600 hover:text-brand-700 focus-ring dark:text-brand-300">
          View all
        </Link>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="space-y-2 py-1">
            {[...Array(3)].map((_, i) => (
              <Skeleton key={i} className="h-5 w-full" />
            ))}
          </div>
        ) : (
          <ul className="divide-y divide-border">{children}</ul>
        )}
      </CardContent>
    </Card>
  )
}

function EmptyRow({ children }: { children: ReactNode }) {
  return <li className="py-6 text-center text-sm text-muted-foreground">{children}</li>
}
