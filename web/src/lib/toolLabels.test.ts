import { describe, expect, it } from 'vitest'

import { toolLabel } from './toolLabels'

describe('toolLabel', () => {
  it('maps known tools to plain-language labels', () => {
    expect(toolLabel('list_tasks')).toBe('Looked up tasks')
    expect(toolLabel('calendar_create_event')).toBe('Added a calendar event')
  })

  it('falls back to a readable version of an unknown tool name', () => {
    expect(toolLabel('sync_something_new')).toBe('Sync something new')
  })
})
