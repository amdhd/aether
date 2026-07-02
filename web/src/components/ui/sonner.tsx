import { Toaster as SonnerToaster } from 'sonner'

import { useThemeStore } from '@/store/theme'

export function Toaster() {
  const theme = useThemeStore((state) => state.theme)

  return <SonnerToaster theme={theme} position="bottom-right" richColors closeButton />
}
