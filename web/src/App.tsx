import { useEffect } from 'react'
import { SessionQueryProvider } from '@/components/SessionQueryProvider'
import { RouterProvider } from 'react-router-dom'

import { getMe } from '@/api/auth'
import { Toaster } from '@/components/ui/sonner'
import { bootstrapAuth } from '@/lib/api'
import { router } from '@/router'
import { useAuthStore } from '@/store/auth'

function App() {
  // On load, try to exchange the HttpOnly refresh cookie for an access token
  // and hydrate the current user. Route guards wait on `bootstrapped`.
  useEffect(() => {
    let active = true
    bootstrapAuth().then(async (token) => {
      if (active && token) {
        try {
          useAuthStore.getState().setUser(await getMe())
        } catch {
          // ignore; the access token is still usable for other calls
        }
      }
    })
    return () => {
      active = false
    }
  }, [])

  return (
    <SessionQueryProvider>
      <RouterProvider router={router} />
      <Toaster />
    </SessionQueryProvider>
  )
}

export default App
