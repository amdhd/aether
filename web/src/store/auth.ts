import { create } from 'zustand'

import type { User } from '@/types'

interface AuthState {
  // Access token is held in memory only — never in localStorage — so an XSS
  // payload can't read it. The refresh token lives in an HttpOnly cookie the
  // browser manages; on page load we silently exchange it for a new access
  // token (see bootstrapAuth in lib/api).
  accessToken: string | null
  user: User | null
  // False until the initial silent refresh on app load has completed, so routes
  // don't bounce to /login before we know whether there's a valid session.
  bootstrapped: boolean
  setAccessToken: (accessToken: string | null) => void
  setUser: (user: User | null) => void
  setBootstrapped: (bootstrapped: boolean) => void
  logout: () => void
}

export const useAuthStore = create<AuthState>()((set) => ({
  accessToken: null,
  user: null,
  bootstrapped: false,
  setAccessToken: (accessToken) => set({ accessToken }),
  setUser: (user) => set({ user }),
  setBootstrapped: (bootstrapped) => set({ bootstrapped }),
  logout: () => set({ accessToken: null, user: null }),
}))
