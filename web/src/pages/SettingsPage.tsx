import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'

import { disconnectGoogle, getGoogleConnectUrl, getGoogleStatus } from '@/api/integrations'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useAuthStore } from '@/store/auth'

export function SettingsPage() {
  const user = useAuthStore((state) => state.user)
  const queryClient = useQueryClient()
  const [searchParams, setSearchParams] = useSearchParams()
  const [callbackNotice] = useState<'connected' | 'error' | null>(() => {
    const result = searchParams.get('google')
    return result === 'connected' || result === 'error' ? result : null
  })

  const { data: googleStatus, isLoading: googleStatusLoading } = useQuery({
    queryKey: ['integrations', 'google', 'status'],
    queryFn: getGoogleStatus,
  })

  useEffect(() => {
    if (!callbackNotice) return
    queryClient.invalidateQueries({ queryKey: ['integrations', 'google', 'status'] })
    const next = new URLSearchParams(searchParams)
    next.delete('google')
    setSearchParams(next, { replace: true })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const connectMutation = useMutation({
    mutationFn: getGoogleConnectUrl,
    onSuccess: (data) => {
      window.location.href = data.authorization_url
    },
  })

  const disconnectMutation = useMutation({
    mutationFn: disconnectGoogle,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['integrations', 'google', 'status'] }),
  })

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Settings</h1>
        <p className="text-muted-foreground">Manage your account and integrations.</p>
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
        <CardContent className="space-y-3">
          {callbackNotice === 'connected' && (
            <p className="text-sm text-emerald-700">Google Calendar connected successfully.</p>
          )}
          {callbackNotice === 'error' && (
            <p className="text-sm text-red-600">
              Couldn&apos;t connect Google Calendar. Please try again.
            </p>
          )}

          <div className="flex items-center gap-3">
            {googleStatusLoading ? (
              <span className="text-sm text-muted-foreground">Checking status...</span>
            ) : googleStatus?.connected ? (
              <Badge variant="success">Connected</Badge>
            ) : (
              <Badge variant="secondary">Not connected</Badge>
            )}

            {googleStatus?.connected ? (
              <Button
                variant="outline"
                size="sm"
                onClick={() => disconnectMutation.mutate()}
                disabled={disconnectMutation.isPending}
              >
                {disconnectMutation.isPending ? 'Disconnecting...' : 'Disconnect'}
              </Button>
            ) : (
              <Button
                size="sm"
                onClick={() => connectMutation.mutate()}
                disabled={connectMutation.isPending}
              >
                {connectMutation.isPending ? 'Redirecting...' : 'Connect Google Calendar'}
              </Button>
            )}
          </div>

          {connectMutation.isError && (
            <p className="text-sm text-red-600">
              Couldn&apos;t start the Google connection. Is it configured on the server?
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
