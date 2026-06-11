import { Link } from 'react-router-dom'

import { Card, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useAuthStore } from '@/store/auth'

export function DashboardPage() {
  const user = useAuthStore((state) => state.user)

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Welcome back{user ? `, ${user.name}` : ''}</h1>
        <p className="text-slate-500">Here&apos;s a quick look at your assistant.</p>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <Link to="/chat">
          <Card className="h-full transition-colors hover:border-brand-300">
            <CardHeader>
              <CardTitle>Chat</CardTitle>
              <CardDescription>Talk to your AI assistant</CardDescription>
            </CardHeader>
          </Card>
        </Link>
        <Link to="/tasks">
          <Card className="h-full transition-colors hover:border-brand-300">
            <CardHeader>
              <CardTitle>Tasks</CardTitle>
              <CardDescription>Manage your to-dos</CardDescription>
            </CardHeader>
          </Card>
        </Link>
        <Link to="/notes">
          <Card className="h-full transition-colors hover:border-brand-300">
            <CardHeader>
              <CardTitle>Notes</CardTitle>
              <CardDescription>Capture and search your notes</CardDescription>
            </CardHeader>
          </Card>
        </Link>
      </div>
    </div>
  )
}
