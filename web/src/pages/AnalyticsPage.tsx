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

import { getAnalyticsSummary, type ToolUsageCount } from '@/api/analytics'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { useThemeStore } from '@/store/theme'

const TOOL_COLORS = ['#4f46e5', '#0ea5e9', '#22c55e', '#f59e0b', '#ef4444', '#a855f7']

function formatShortDate(value: unknown): string {
  const date = new Date(`${String(value)}T00:00:00Z`)
  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', timeZone: 'UTC' })
}

function renderToolLabel(props: PieLabelRenderProps): string {
  const { tool_name, count } = props as unknown as ToolUsageCount
  return `${tool_name} (${count})`
}

export function AnalyticsPage() {
  const isDark = useThemeStore((state) => state.theme === 'dark')
  const gridStroke = isDark ? '#1e293b' : '#e2e8f0'
  const tooltipStyle = isDark
    ? { backgroundColor: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, color: '#f8fafc' }
    : undefined

  const { data, isLoading, isError } = useQuery({
    queryKey: ['analytics', 'summary'],
    queryFn: () => getAnalyticsSummary(14),
  })

  const hasData = (data?.totals.messages ?? 0) > 0 || (data?.totals.conversations ?? 0) > 0

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

          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle>Messages per day</CardTitle>
                <CardDescription>Last 14 days</CardDescription>
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
                <CardDescription>Prompt vs. completion tokens</CardDescription>
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
              <CardTitle>Tool call breakdown</CardTitle>
              <CardDescription>Which tools the assistant has used on your behalf</CardDescription>
            </CardHeader>
            <CardContent className="h-72">
              {data.tool_usage.length === 0 ? (
                <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
                  <Wrench className="mr-2 h-4 w-4" />
                  No tool calls yet.
                </div>
              ) : (
                <ResponsiveContainer width="100%" height="100%">
                  <PieChart>
                    <Pie
                      data={data.tool_usage}
                      dataKey="count"
                      nameKey="tool_name"
                      cx="50%"
                      cy="50%"
                      outerRadius={90}
                      label={renderToolLabel}
                    >
                      {data.tool_usage.map((entry, index) => (
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
        <div className="flex h-9 w-9 items-center justify-center rounded-full bg-brand-50 text-brand-600">
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
