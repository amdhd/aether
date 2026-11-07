import { useMutation } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'

import { logoutEverywhere } from '@/api/auth'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useAuthStore } from '@/store/auth'

/**
 * "Sign out" in the sidebar ends only this browser's session. This is the
 * deliberate, everywhere version — for a lost phone or a shared computer you
 * can no longer reach — without having to change the password.
 */
export function SessionsCard() {
  const navigate = useNavigate()
  const logout = useAuthStore((state) => state.logout)
  const mutation = useMutation({
    mutationFn: logoutEverywhere,
    onSuccess: () => {
      logout()
      navigate('/login')
    },
  })

  return (
    <Card>
      <CardHeader>
        <CardTitle>Sessions</CardTitle>
        <CardDescription>Signing out in the sidebar only signs out this browser.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <Button variant="outline" disabled={mutation.isPending} onClick={() => mutation.mutate()}>
          Sign out of all devices
        </Button>
        {mutation.isError && <p role="alert" className="text-sm text-red-600 dark:text-red-400">Couldn&apos;t sign out other devices. Please try again.</p>}
      </CardContent>
    </Card>
  )
}
