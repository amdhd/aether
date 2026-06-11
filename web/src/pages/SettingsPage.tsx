import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useAuthStore } from '@/store/auth'

export function SettingsPage() {
  const user = useAuthStore((state) => state.user)

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Settings</h1>
        <p className="text-slate-500">Manage your account and integrations.</p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>Account</CardTitle>
          <CardDescription>Your profile information</CardDescription>
        </CardHeader>
        <CardContent className="space-y-2 text-sm">
          <p>
            <span className="font-medium">Name:</span> {user?.name}
          </p>
          <p>
            <span className="font-medium">Email:</span> {user?.email}
          </p>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Google Calendar</CardTitle>
          <CardDescription>Connect your calendar to let the assistant manage events</CardDescription>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-slate-500">Coming soon.</p>
        </CardContent>
      </Card>
    </div>
  )
}
