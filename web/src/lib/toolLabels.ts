const TOOL_LABELS: Record<string, string> = {
  create_task: 'Added a task',
  list_tasks: 'Looked up tasks',
  update_task: 'Updated a task',
  delete_task: 'Deleted a task',
  create_note: 'Added a note',
  list_notes: 'Looked up notes',
  search_notes: 'Searched notes',
  web_search: 'Searched the web',
  calendar_list_events: 'Checked the calendar',
  calendar_create_event: 'Added a calendar event',
  calendar_delete_event: 'Deleted a calendar event',
}

/** Human-readable name for a tool, falling back to a de-underscored version. */
export function toolLabel(toolName: string): string {
  const known = TOOL_LABELS[toolName]
  if (known) return known
  const words = toolName.replace(/[_-]+/g, ' ').trim()
  return words.charAt(0).toUpperCase() + words.slice(1)
}
