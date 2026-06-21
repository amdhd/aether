import { useEffect } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { RouterProvider } from 'react-router-dom'

import { getMe } from '@/api/auth'
import { bootstrapAuth } from '@/lib/api'
import { router } from '@/router'
import { useAuthStore } from '@/store/auth'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
})

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
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  )
}

export default App
