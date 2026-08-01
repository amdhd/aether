import { useQuery } from '@tanstack/react-query'
import { MessageSquare, Sparkles, Wrench, Zap } from 'lucide-react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { PieLabelRenderProps } from 'recharts'

import { getAnalyticsSummary } from '@/api/analytics'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { toolLabel } from '@/lib/toolLabels'
import { useThemeStore } from '@/store/theme'

const TOOL_COLORS = ['#4f46e5', '#0ea5e9', '#22c55e', '#f59e0b', '#ef4444', '#a855f7']

// One window for everything the API aggregates per-period, so the page can say
// so once and mean it everywhere.
const RANGE_DAYS = 14
const RANGE_LABEL = `Last ${RANGE_DAYS} days`

function formatShortDate(value: unknown): string {
  const date = new Date(`${String(value)}T00:00:00Z`)
  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', timeZone: 'UTC' })
}

interface ToolSlice {
  tool_name: string
  label: string
  count: number
}

function renderToolLabel(props: PieLabelRenderProps): string {
  const { label, count } = props as unknown as ToolSlice
  return `${label} (${count})`
}

export function AnalyticsPage() {
  const isDark = useThemeStore((state) => state.theme === 'dark')
  const gridStroke = isDark ? '#1e293b' : '#e2e8f0'
  const tooltipStyle = isDark
    ? { backgroundColor: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, color: '#f8fafc' }
    : undefined

  const { data, isLoading, isError } = useQuery({
    queryKey: ['analytics', 'summary', RANGE_DAYS],
    queryFn: () => getAnalyticsSummary(RANGE_DAYS),
  })

  const hasData = (data?.totals.messages ?? 0) > 0 || (data?.totals.conversations ?? 0) > 0

  const toolSlices: ToolSlice[] =
    data?.tool_usage.map((entry) => ({ ...entry, label: toolLabel(entry.tool_name) })) ?? []

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Analytics</h1>
        <p className="text-muted-foreground">Insights into your conversations and tool usage.</p>
      </div>

      {isError && <p className="text-sm text-red-600">Failed to load analytics. Please try again.</p>}

      {isLoading ? (
        <div className="space-y-6">
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {[...Array(4)].map((_, i) => (
              <Skeleton key={i} className="h-24 w-full" />
            ))}
          </div>
          <div className="grid gap-4 lg:grid-cols-2">
            <Skeleton className="h-72 w-full" />
            <Skeleton className="h-72 w-full" />
          </div>
          <Skeleton className="h-72 w-full" />
        </div>
      ) : !data ? null : !hasData ? (
        <p className="rounded-card border border-dashed border-border p-8 text-center text-sm text-muted-foreground">
          No activity yet. Start chatting with Aether to see your usage stats here.
        </p>
      ) : (
        <div className="space-y-6">
          {/* Totals are lifetime figures, while everything below them covers the
              window. Unlabelled, they read as part of it. */}
          <section aria-labelledby="totals-heading">
            <h2 id="totals-heading" className="mb-3 text-sm font-medium text-muted-foreground">
              All time
            </h2>
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <StatCard
                icon={<Sparkles className="h-4 w-4" />}
                label="Conversations"
                value={data.totals.conversations.toLocaleString()}
              />
              <StatCard
                icon={<MessageSquare className="h-4 w-4" />}
                label="Messages sent"
                value={data.totals.messages.toLocaleString()}
              />
              <StatCard
                icon={<Zap className="h-4 w-4" />}
                label="Prompt tokens"
                value={data.totals.prompt_tokens.toLocaleString()}
              />
              <StatCard
                icon={<Zap className="h-4 w-4" />}
                label="Completion tokens"
                value={data.totals.completion_tokens.toLocaleString()}
              />
            </div>
          </section>

          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle>Messages per day</CardTitle>
                <CardDescription>{RANGE_LABEL}</CardDescription>
              </CardHeader>
              <CardContent className="h-72">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={data.messages_per_day}>
                    <CartesianGrid strokeDasharray="3 3" vertical={false} stroke={gridStroke} />
                    <XAxis dataKey="date" tickFormatter={formatShortDate} fontSize={12} stroke="#94a3b8" />
                    <YAxis allowDecimals={false} fontSize={12} stroke="#94a3b8" />
                    <Tooltip labelFormatter={formatShortDate} contentStyle={tooltipStyle} />
                    <Bar dataKey="count" name="Messages" fill="#4f46e5" radius={[4, 4, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Token usage per day</CardTitle>
                <CardDescription>Prompt vs. completion tokens · {RANGE_LABEL}</CardDescription>
              </CardHeader>
              <CardContent className="h-72">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={data.tokens_per_day}>
                    <CartesianGrid strokeDasharray="3 3" vertical={false} stroke={gridStroke} />
                    <XAxis dataKey="date" tickFormatter={formatShortDate} fontSize={12} stroke="#94a3b8" />
                    <YAxis allowDecimals={false} fontSize={12} stroke="#94a3b8" />
                    <Tooltip labelFormatter={formatShortDate} contentStyle={tooltipStyle} />
                    <Legend />
                    <Bar
                      dataKey="prompt_tokens"
                      name="Prompt"
                      stackId="tokens"
                      fill="#6366f1"
                      radius={[0, 0, 0, 0]}
                    />
                    <Bar
                      dataKey="completion_tokens"
                      name="Completion"
                      stackId="tokens"
                      fill="#a5b4fc"
                      radius={[4, 4, 0, 0]}
                    />
                  </BarChart>
                </ResponsiveContainer>
              </CardContent>
            </Card>
          </div>

          <Card>
            <CardHeader>
              <CardTitle>What Aether did for you</CardTitle>
              <CardDescription>
                Actions the assistant took on your behalf · {RANGE_LABEL}
              </CardDescription>
            </CardHeader>
            <CardContent className="h-72">
              {toolSlices.length === 0 ? (
                <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
                  <Wrench className="mr-2 h-4 w-4" />
                  No actions in the last {RANGE_DAYS} days.
                </div>
              ) : (
                <ResponsiveContainer width="100%" height="100%">
                  <PieChart>
                    <Pie
                      data={toolSlices}
                      dataKey="count"
                      nameKey="label"
                      cx="50%"
                      cy="50%"
                      outerRadius={90}
                      label={renderToolLabel}
                    >
                      {toolSlices.map((entry, index) => (
                        <Cell key={entry.tool_name} fill={TOOL_COLORS[index % TOOL_COLORS.length]} />
                      ))}
                    </Pie>
                    <Tooltip contentStyle={tooltipStyle} />
                    <Legend />
                  </PieChart>
                </ResponsiveContainer>
              )}
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  )
}

function StatCard({ icon, label, value }: { icon: React.ReactNode; label: string; value: string }) {
  return (
    <Card>
      <CardContent className="flex items-center gap-3 p-4">
        {/* brand-50/600 are fixed light-mode values, so the dark variants are
            not optional here — without them this is a white disc on a dark
            card. Matches the identical chip on the dashboard. */}
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-brand-50 text-brand-600 dark:bg-brand-500/15 dark:text-brand-300">
          {icon}
        </div>
        <div>
          <p className="text-xs text-muted-foreground">{label}</p>
          <p className="text-lg font-semibold leading-tight">{value}</p>
        </div>
      </CardContent>
    </Card>
  )
}
