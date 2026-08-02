import { useQuery } from '@tanstack/react-query'
import { MessageSquare, Sparkles, Wrench, Zap } from 'lucide-react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { getAnalyticsSummary } from '@/api/analytics'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { toolLabel } from '@/lib/toolLabels'
import { useThemeStore } from '@/store/theme'

// One window for everything the API aggregates per-period, so the page can say
// so once and mean it everywhere.
const RANGE_DAYS = 14
const RANGE_LABEL = `Last ${RANGE_DAYS} days`

/**
 * Chart colours, stepped per mode from the app's own zinc ramp rather than the
 * indigo/rainbow this page used to carry — the rest of the product reads
 * black-and-white on purpose.
 *
 * Nothing here encodes *identity*: two of the three charts are a single series,
 * and the third (prompt vs. completion) is two parts of one quantity, so a
 * two-step ramp of one hue is the honest encoding and lightness does the work.
 * The dark column is selected against the dark surface, not flipped.
 *
 * Checked with the dataviz validator in both modes: CVD separation ΔE 27.8
 * (light) / 32.0 (dark) and contrast ≥ 3:1 against the surface both pass. It
 * also reports two failures — lightness band and chroma floor — that apply to
 * categorical palettes only, per its own scope note; a grey by construction has
 * no chroma, and here that is the point rather than a defect.
 */
const CHART = {
  light: {
    series: '#27272a', // zinc-800
    seriesMuted: '#71717a', // zinc-500
    grid: '#e4e4e7', // zinc-200 — matches --border
    axis: '#71717a',
    surface: '#ffffff',
    tooltip: undefined,
  },
  dark: {
    series: '#d4d4d8', // zinc-300
    seriesMuted: '#71717a', // zinc-500
    grid: '#2e2e2e', // matches --border
    axis: '#a1a1aa',
    surface: '#1f1f1f',
    tooltip: {
      backgroundColor: '#1f1f1f',
      border: '1px solid #2e2e2e',
      borderRadius: 8,
      color: '#f4f4f5',
    },
  },
} as const

function formatShortDate(value: unknown): string {
  const date = new Date(`${String(value)}T00:00:00Z`)
  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', timeZone: 'UTC' })
}

interface ToolAction {
  tool_name: string
  label: string
  count: number
}

export function AnalyticsPage() {
  const isDark = useThemeStore((state) => state.theme === 'dark')
  const chart = isDark ? CHART.dark : CHART.light

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['analytics', 'summary', RANGE_DAYS],
    queryFn: () => getAnalyticsSummary(RANGE_DAYS),
  })

  const hasData = (data?.totals.messages ?? 0) > 0 || (data?.totals.conversations ?? 0) > 0

  // Descending: a horizontal chart draws the first row at the top, so this puts
  // the biggest bar where the eye starts. (The API already orders by count, but
  // the ranking is the point of the chart, so don't leave it to chance.)
  const toolActions: ToolAction[] = (data?.tool_usage ?? [])
    .map((entry) => ({ ...entry, label: toolLabel(entry.tool_name) }))
    .sort((a, b) => b.count - a.count)

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Analytics</h1>
        <p className="text-muted-foreground">Insights into your conversations and tool usage.</p>
      </div>

      {isError && (
        // A bare sentence left the reader with nothing to do but reload the
        // page, and the red had no dark-mode step so it sat near-invisible.
        <div
          role="alert"
          className="rounded-card border border-red-200 bg-red-50 p-4 dark:border-red-900/50 dark:bg-red-950/40"
        >
          <p className="text-sm text-red-700 dark:text-red-300">Failed to load analytics.</p>
          <Button variant="outline" size="sm" className="mt-3" onClick={() => void refetch()}>
            Try again
          </Button>
        </div>
      )}

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
                    {/* Solid hairline: a dashed grid reads as a threshold or a
                        projection when it is only a grid. */}
                    <CartesianGrid vertical={false} stroke={chart.grid} />
                    <XAxis dataKey="date" tickFormatter={formatShortDate} fontSize={12} stroke={chart.axis} />
                    <YAxis allowDecimals={false} fontSize={12} stroke={chart.axis} />
                    <Tooltip labelFormatter={formatShortDate} contentStyle={chart.tooltip} />
                    {/* One series, so no legend — the card title names it. */}
                    <Bar dataKey="count" name="Messages" fill={chart.series} maxBarSize={32} radius={[4, 4, 0, 0]} />
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
                    <CartesianGrid vertical={false} stroke={chart.grid} />
                    <XAxis dataKey="date" tickFormatter={formatShortDate} fontSize={12} stroke={chart.axis} />
                    <YAxis allowDecimals={false} fontSize={12} stroke={chart.axis} />
                    <Tooltip labelFormatter={formatShortDate} contentStyle={chart.tooltip} />
                    {/* Two series, so the legend is not optional. */}
                    <Legend />
                    {/* A hairline in the surface colour separates the two
                        segments instead of letting them fuse into one block. */}
                    <Bar
                      dataKey="prompt_tokens"
                      name="Prompt"
                      stackId="tokens"
                      fill={chart.seriesMuted}
                      stroke={chart.surface}
                      strokeWidth={2}
                    />
                    <Bar
                      dataKey="completion_tokens"
                      name="Completion"
                      stackId="tokens"
                      fill={chart.series}
                      stroke={chart.surface}
                      strokeWidth={2}
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
            {/* Was a pie whose slice labels repeated the legend beside it and
                ran off the edge of the card once an action had a long name.
                The job here is comparing magnitudes across up to a dozen
                categories, which is a bar chart — horizontal, because the
                category names are sentences. The names then live on the axis,
                so there is nothing for a legend to add and no per-slice colour
                to invent: one series, one hue. */}
            <CardContent
              // Grows with the number of actions so the bars keep a readable
              // thickness instead of being crushed into a fixed box.
              style={{ height: Math.max(220, toolActions.length * 34 + 40) }}
            >
              {toolActions.length === 0 ? (
                <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
                  <Wrench className="mr-2 h-4 w-4" />
                  No actions in the last {RANGE_DAYS} days.
                </div>
              ) : (
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={toolActions} layout="vertical" margin={{ left: 0, right: 16 }}>
                    <CartesianGrid horizontal={false} stroke={chart.grid} />
                    <XAxis type="number" allowDecimals={false} fontSize={12} stroke={chart.axis} />
                    <YAxis
                      type="category"
                      dataKey="label"
                      // Enough for the longest label toolLabels.ts produces
                      // ("Deleted a calendar event") to stay on one line —
                      // recharts word-wraps a category tick that outgrows this,
                      // and a wrapped label pushes its own row out of line.
                      width={196}
                      fontSize={12}
                      stroke={chart.axis}
                      tickLine={false}
                      axisLine={false}
                    />
                    <Tooltip contentStyle={chart.tooltip} cursor={{ fill: chart.grid }} />
                    <Bar dataKey="count" name="Times" fill={chart.series} radius={[0, 4, 4, 0]} />
                  </BarChart>
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
