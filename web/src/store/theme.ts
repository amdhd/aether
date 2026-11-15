import { create } from 'zustand'

export type Theme = 'light' | 'dark'

const STORAGE_KEY = 'aether-theme'

function getInitialTheme(): Theme {
  if (typeof window === 'undefined') return 'dark'
  const stored = window.localStorage.getItem(STORAGE_KEY)
  if (stored === 'light' || stored === 'dark') return stored
  // Dusk's slate is the default; light is opt-in via the toggle.
  return 'dark'
}

function applyTheme(theme: Theme): void {
  const root = document.documentElement
  root.classList.toggle('dark', theme === 'dark')
  window.localStorage.setItem(STORAGE_KEY, theme)
}

interface ThemeState {
  theme: Theme
  toggle: () => void
  setTheme: (theme: Theme) => void
}

export const useThemeStore = create<ThemeState>((set, get) => ({
  theme: getInitialTheme(),
  toggle: () => {
    const next: Theme = get().theme === 'dark' ? 'light' : 'dark'
    applyTheme(next)
    set({ theme: next })
  },
  setTheme: (theme) => {
    applyTheme(theme)
    set({ theme })
  },
}))
