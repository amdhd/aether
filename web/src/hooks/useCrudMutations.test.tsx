import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { toast } from 'sonner'
import { describe, expect, it, vi } from 'vitest'

import { ApiError, apiErrorDetail } from '@/lib/api'

import { useCrudMutations } from './useCrudMutations'

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

function setup(error: unknown) {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } })
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>
  return renderHook(() => useCrudMutations({
    queryKey: ['notes'],
    entityName: 'Note',
    create: () => Promise.reject(error),
    update: () => Promise.reject(error),
    remove: () => Promise.reject(error),
  }), { wrapper })
}

async function failCreate(error: unknown) {
  vi.mocked(toast.error).mockClear()
  const { result } = setup(error)
  act(() => { result.current.createMutation.mutate({}) })
  await waitFor(() => expect(toast.error).toHaveBeenCalledTimes(1))
  return vi.mocked(toast.error).mock.calls[0][0] as string
}

describe('useCrudMutations errors', () => {
  it('shows a validation reason and does not suggest retrying', async () => {
    const message = await failCreate(new ApiError(422, {
      detail: [{ loc: ['body', 'content'], msg: 'String should have at most 100000 characters' }],
    }))
    expect(message).toBe("Couldn't create note. content: String should have at most 100000 characters.")
  })

  it('shows the rate-limit message without doubling "try again"', async () => {
    const message = await failCreate(new ApiError(429, { detail: "You're saving notes too quickly. Try again in a moment." }))
    expect(message).toBe("Couldn't create note. You're saving notes too quickly. Try again in a moment.")
  })

  it('offers a retry for server and network failures', async () => {
    expect(await failCreate(new ApiError(503, null))).toBe("Couldn't create note. Please try again.")
    expect(await failCreate(new TypeError('Failed to fetch'))).toBe("Couldn't create note. Please try again.")
  })
})

describe('apiErrorDetail', () => {
  it.each([
    [new ApiError(404, { detail: 'Note not found' }), 'Note not found'],
    [new ApiError(422, { detail: [{ loc: ['body'], msg: 'Invalid JSON' }] }), 'Invalid JSON'],
    [new ApiError(500, { detail: { nested: true } }), null],
    [new ApiError(500, 'not json'), null],
    [new Error('plain'), null],
  ])('%#', (error, expected) => {
    expect(apiErrorDetail(error)).toBe(expected)
  })
})
