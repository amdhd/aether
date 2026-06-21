import { Navigate, Outlet } from 'react-router-dom'

import { PageLoader } from '@/components/layout/PageLoader'
import { useAuthStore } from '@/store/auth'

export function ProtectedRoute() {
  const accessToken = useAuthStore((state) => state.accessToken)
  const bootstrapped = useAuthStore((state) => state.bootstrapped)

  // Until the initial silent refresh finishes we don't yet know if there's a
  // valid session, so hold on a loader instead of flashing the login page.
  if (!bootstrapped) {
    return <PageLoader />
  }

  if (!accessToken) {
    return <Navigate to="/login" replace />
  }

  return <Outlet />
}
