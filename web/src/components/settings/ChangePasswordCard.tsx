import { useState } from 'react'

import { changePassword } from '@/api/auth'
import { ApiError } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useAuthStore } from '@/store/auth'

const MIN_PASSWORD_LENGTH = 8

export function ChangePasswordCard() {
  const setAccessToken = useAuthStore((state) => state.setAccessToken)

  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState(false)
  const [isSubmitting, setIsSubmitting] = useState(false)

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault()
    setDone(false)
    if (next !== confirm) {
      setError('Those passwords do not match.')
      return
    }
    setError(null)
    setIsSubmitting(true)
    try {
      // The server revokes every session and issues this caller a fresh pair, so
      // the token in memory is now stale — swap it in or the next request 401s.
      const tokens = await changePassword({ current_password: current, new_password: next })
      setAccessToken(tokens.access_token)
      setCurrent('')
      setNext('')
      setConfirm('')
      setDone(true)
    } catch (err) {
      if (err instanceof ApiError && err.status === 400) {
        setError('Your current password is not correct.')
      } else if (err instanceof ApiError && err.status === 422) {
        setError(`Choose a password of at least ${MIN_PASSWORD_LENGTH} characters.`)
      } else {
        setError('Something went wrong. Please try again.')
      }
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Password</CardTitle>
        <CardDescription>
          Changing it signs out every other device. This one stays signed in.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form className="max-w-sm space-y-4" onSubmit={handleSubmit}>
          <div className="space-y-2">
            <Label htmlFor="current-password">Current password</Label>
            <Input
              id="current-password"
              type="password"
              autoComplete="current-password"
              required
              value={current}
              onChange={(e) => setCurrent(e.target.value)}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="new-password">New password</Label>
            <Input
              id="new-password"
              type="password"
              autoComplete="new-password"
              required
              minLength={MIN_PASSWORD_LENGTH}
              value={next}
              onChange={(e) => setNext(e.target.value)}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="confirm-password">Confirm new password</Label>
            <Input
              id="confirm-password"
              type="password"
              autoComplete="new-password"
              required
              minLength={MIN_PASSWORD_LENGTH}
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
            />
          </div>
          {error && (
            <p role="alert" className="text-sm text-red-600">
              {error}
            </p>
          )}
          {done && (
            <p role="status" className="text-sm text-muted-foreground">
              Password updated. Other devices have been signed out.
            </p>
          )}
          <Button type="submit" disabled={isSubmitting}>
            {isSubmitting ? 'Updating...' : 'Update password'}
          </Button>
        </form>
      </CardContent>
    </Card>
  )
}
