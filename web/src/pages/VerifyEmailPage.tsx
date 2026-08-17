import { useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'

import { confirmEmail, getMe } from '@/api/auth'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useAuthStore } from '@/store/auth'

type Status = 'confirming' | 'done' | 'failed' | 'missing'

export function VerifyEmailPage() {
  const [searchParams] = useSearchParams()
  const token = searchParams.get('token') ?? ''
  const [status, setStatus] = useState<Status>(token ? 'confirming' : 'missing')
  const accessToken = useAuthStore((state) => state.accessToken)
  const setUser = useAuthStore((state) => state.setUser)
  // The token is single-use, so a second confirm would fail. StrictMode mounts
  // effects twice in development, which would otherwise turn a good link into an
  // error on the first visit.
  const attempted = useRef(false)

  useEffect(() => {
    if (!token || attempted.current) return
    attempted.current = true

    confirmEmail(token)
      .then(async () => {
        setStatus('done')
        // Refresh the cached user so the banner disappears without a reload —
        // only possible when this tab happens to be signed in.
        if (accessToken) {
          try {
            setUser(await getMe())
          } catch {
            // Not fatal: the address is confirmed either way.
          }
        }
      })
      .catch(() => setStatus('failed'))
  }, [token, accessToken, setUser])

  const body = {
    missing: 'This confirmation link is missing its token. Copy the full link from the email.',
    confirming: 'Confirming your email address...',
    done: 'Your email address is confirmed. Thanks!',
    failed: 'That confirmation link is invalid or has expired. You can send yourself a new one from Settings.',
  }[status]

  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>{status === 'done' ? 'Email confirmed' : 'Confirm your email'}</CardTitle>
          <CardDescription>Aether</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <p role="status" className="text-sm text-muted-foreground">
            {body}
          </p>
          {status !== 'confirming' && (
            <Button asChild className="w-full">
              <Link to={accessToken ? '/settings' : '/login'}>
                {accessToken ? 'Back to settings' : 'Go to sign in'}
              </Link>
            </Button>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
