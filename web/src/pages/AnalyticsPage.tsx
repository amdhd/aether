import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

export function AnalyticsPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Analytics</h1>
        <p className="text-slate-500">Insights into your tasks and productivity.</p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>Coming soon</CardTitle>
          <CardDescription>Charts will appear here once enough data is available.</CardDescription>
        </CardHeader>
        <CardContent />
      </Card>
    </div>
  )
}
