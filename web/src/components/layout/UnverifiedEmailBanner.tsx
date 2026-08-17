import { Link } from 'react-router-dom'

import { useAuthStore } from '@/store/auth'

/**
 * Advisory prompt for an unconfirmed address.
 *
 * Verification gates nothing — accounts that predate it keep working — so this
 * is a nudge, not a wall, and it stays dismissible by simply being ignorable.
 */
export function UnverifiedEmailBanner() {
  const user = useAuthStore((state) => state.user)
  if (!user || user.email_verified) return null

  return (
    <div
      role="status"
      className="shrink-0 border-b border-amber-300 bg-amber-50 px-4 py-2 text-sm text-amber-900 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-200"
    >
      Confirm your email address so you can recover this account if you forget your password.{' '}
      <Link to="/settings" className="font-medium underline underline-offset-2">
        Send the link
      </Link>
    </div>
  )
}
