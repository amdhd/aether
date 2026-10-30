import { useEffect } from 'react'
import { act, render, screen } from '@testing-library/react'
import { useQueryClient, type QueryClient } from '@tanstack/react-query'
import { afterEach, expect, it } from 'vitest'
import { useAuthStore } from '@/store/auth'
import { SessionQueryProvider } from './SessionQueryProvider'

let current: QueryClient
function Probe() {
  const client = useQueryClient()
  useEffect(() => { current = client }, [client])
  return <span>{String(client.getQueryData(['private']) ?? 'empty')}</span>
}
afterEach(() => useAuthStore.getState().logout())
it('isolates logout/account changes and late writes from the preceding account', () => {
  useAuthStore.setState({ accessToken: 'a', user: { id: 1 } as never })
  const view = render(<SessionQueryProvider><Probe /></SessionQueryProvider>)
  const old = current
  old.setQueryData(['private'], 'account A')
  view.rerender(<SessionQueryProvider><Probe /></SessionQueryProvider>)
  expect(screen.getByText('account A')).toBeInTheDocument()
  act(() => useAuthStore.getState().logout())
  expect(screen.getByText('empty')).toBeInTheDocument()
  act(() => useAuthStore.setState({ accessToken: 'b', user: { id: 2 } as never }))
  old.setQueryData(['private'], 'late response for A')
  expect(current).not.toBe(old)
  expect(current.getQueryData(['private'])).toBeUndefined()
})
