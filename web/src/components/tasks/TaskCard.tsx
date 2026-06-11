import { Pencil, Trash2 } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import type { Task, TaskStatus } from '@/types'

const priorityVariant = {
  low: 'secondary',
  medium: 'warning',
  high: 'destructive',
} as const

interface TaskCardProps {
  task: Task
  onStatusChange: (status: TaskStatus) => void
  onEdit: () => void
  onDelete: () => void
}

export function TaskCard({ task, onStatusChange, onEdit, onDelete }: TaskCardProps) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-start justify-between gap-2">
          <CardTitle className="text-sm">{task.title}</CardTitle>
          <Badge variant={priorityVariant[task.priority]}>{task.priority}</Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-2 pb-2">
        {task.description && <p className="text-sm text-slate-600">{task.description}</p>}
        {task.due_date && <p className="text-xs text-slate-400">Due {task.due_date}</p>}
      </CardContent>
      <CardFooter className="flex items-center justify-between gap-2">
        <Select value={task.status} onValueChange={(value) => onStatusChange(value as TaskStatus)}>
          <SelectTrigger aria-label={`Status for ${task.title}`} className="h-8 w-28 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="todo">To do</SelectItem>
            <SelectItem value="doing">Doing</SelectItem>
            <SelectItem value="done">Done</SelectItem>
          </SelectContent>
        </Select>
        <div className="flex gap-1">
          <Button variant="ghost" size="icon" aria-label={`Edit ${task.title}`} onClick={onEdit}>
            <Pencil className="h-4 w-4" />
          </Button>
          <Button variant="ghost" size="icon" aria-label={`Delete ${task.title}`} onClick={onDelete}>
            <Trash2 className="h-4 w-4" />
          </Button>
        </div>
      </CardFooter>
    </Card>
  )
}
