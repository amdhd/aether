import { cva, type VariantProps } from 'class-variance-authority'
import * as React from 'react'

import { cn } from '@/lib/utils'

const badgeVariants = cva(
  'inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium transition-colors focus-ring',
  {
    variants: {
      variant: {
        default: 'border-transparent bg-accent/15 text-accent-text',
        secondary: 'border-transparent bg-surface-muted text-foreground',
        destructive: 'border-transparent bg-danger/15 text-danger',
        outline: 'border-border text-foreground',
        success: 'border-transparent bg-accent/15 text-accent-text',
        warning: 'border-transparent bg-signal/15 text-signal',
      },
    },
    defaultVariants: {
      variant: 'default',
    },
  },
)

export interface BadgeProps extends React.HTMLAttributes<HTMLDivElement>, VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
  return <div className={cn(badgeVariants({ variant }), className)} {...props} />
}

export { badgeVariants }
