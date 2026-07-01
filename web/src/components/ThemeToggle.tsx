import { Moon, Sun } from 'lucide-react'

import { Button, type ButtonProps } from '@/components/ui/button'
import { useThemeStore } from '@/store/theme'

export function ThemeToggle({ variant = 'ghost', size = 'icon', className }: ButtonProps) {
  const theme = useThemeStore((state) => state.theme)
  const toggle = useThemeStore((state) => state.toggle)
  const isDark = theme === 'dark'

  return (
    <Button
      variant={variant}
      size={size}
      className={className}
      onClick={toggle}
      aria-label={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
      title={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
    >
      {isDark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
    </Button>
  )
}
