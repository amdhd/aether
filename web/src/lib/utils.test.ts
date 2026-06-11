import { describe, expect, it } from 'vitest'

import { cn } from './utils'

describe('cn', () => {
  it('merges class names and resolves tailwind conflicts', () => {
    expect(cn('px-2', 'px-4')).toBe('px-4')
  })

  it('drops falsy values', () => {
    const isHidden = false
    expect(cn('text-red-500', isHidden && 'hidden', undefined, 'font-bold')).toBe('text-red-500 font-bold')
  })
})
