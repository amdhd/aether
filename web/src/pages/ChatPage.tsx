import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

export function ChatPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Chat</h1>
        <p className="text-slate-500">Your AI assistant.</p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>Coming soon</CardTitle>
          <CardDescription>
            The chat assistant will be available once the agent integration is wired up.
          </CardDescription>
        </CardHeader>
        <CardContent />
      </Card>
    </div>
  )
}
