import { Navigate, Outlet } from 'react-router-dom'

import { useAuthStore } from '@/store/auth'

export function ProtectedRoute() {
  const accessToken = useAuthStore((state) => state.accessToken)

  if (!accessToken) {
    return <Navigate to="/login" replace />
  }

  return <Outlet />
}
