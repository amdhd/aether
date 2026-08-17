import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'

import { sendVerificationEmail } from '@/api/auth'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useAuthStore } from '@/store/auth'

export function EmailVerificationCard() {
  const user = useAuthStore((state) => state.user)
  const [sent, setSent] = useState(false)

  const resend = useMutation({
    mutationFn: sendVerificationEmail,
    onSuccess: () => setSent(true),
  })

  const verified = user?.email_verified ?? false

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          Email address
          <Badge variant={verified ? 'success' : 'warning'}>
            {verified ? 'Confirmed' : 'Unconfirmed'}
          </Badge>
        </CardTitle>
        <CardDescription>
          {verified
            ? 'This address has been confirmed.'
            : 'Confirming your address lets you recover the account if you forget your password.'}
        </CardDescription>
      </CardHeader>
      {!verified && (
        <CardContent className="space-y-3">
          <p className="text-sm text-muted-foreground">{user?.email}</p>
          {sent ? (
            <p role="status" className="text-sm text-muted-foreground">
              Confirmation email sent. The link expires in 24 hours.
            </p>
          ) : (
            <Button onClick={() => resend.mutate()} disabled={resend.isPending}>
              {resend.isPending ? 'Sending...' : 'Send confirmation email'}
            </Button>
          )}
          {resend.isError && (
            <p role="alert" className="text-sm text-red-600">
              Could not send the email. Please try again.
            </p>
          )}
        </CardContent>
      )}
    </Card>
  )
}
