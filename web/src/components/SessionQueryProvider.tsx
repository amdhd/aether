import { useEffect, useMemo, type ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useAuthStore } from '@/store/auth'

export function SessionQueryProvider({ children }: { children: ReactNode }) {
  const identity = useAuthStore((state) => state.accessToken ? (state.user?.id ?? 'authenticating') : 'signed-out')
  // Each identity owns a different cache. Late requests and mutation callbacks
  // retain their old client and cannot populate the next account's cache.
  const client = useMemo(() => {
    void identity
    return new QueryClient({ defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } } })
  }, [identity])
  useEffect(() => () => { client.clear() }, [client])
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}
