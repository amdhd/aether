import { useQuery } from '@tanstack/react-query'
import { Plus } from 'lucide-react'
import { useState } from 'react'

import { createTask, deleteTask, listTasks, updateTask } from '@/api/tasks'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { TaskCard } from '@/components/tasks/TaskCard'
import { TaskFormDialog } from '@/components/tasks/TaskFormDialog'
import { useCrudMutations } from '@/hooks/useCrudMutations'
import type { Task, TaskCreateInput, TaskStatus } from '@/types'

const columns: { status: TaskStatus; label: string }[] = [
  { status: 'todo', label: 'To do' },
  { status: 'doing', label: 'Doing' },
  { status: 'done', label: 'Done' },
]

export function TasksPage() {
  const { data, isLoading, isError } = useQuery({ queryKey: ['tasks'], queryFn: listTasks })
  const tasks = data?.items ?? []

  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingTask, setEditingTask] = useState<Task | null>(null)
  const [deletingTask, setDeletingTask] = useState<Task | null>(null)

  const { createMutation, updateMutation, deleteMutation } = useCrudMutations<Task, TaskCreateInput>({
    queryKey: ['tasks'],
    create: createTask,
    update: updateTask,
    remove: deleteTask,
    onDeleteSuccess: () => setDeletingTask(null),
  })

  const openCreateDialog = () => {
    setEditingTask(null)
    setDialogOpen(true)
  }

  const openEditDialog = (task: Task) => {
    setEditingTask(task)
    setDialogOpen(true)
  }

  const handleSubmit = async (input: TaskCreateInput) => {
    if (editingTask) {
      await updateMutation.mutateAsync({ id: editingTask.id, input })
    } else {
      await createMutation.mutateAsync(input)
    }
  }

  const handleStatusChange = (task: Task, status: TaskStatus) => {
    updateMutation.mutate({
      id: task.id,
      input: {
        title: task.title,
        description: task.description,
        due_date: task.due_date,
        priority: task.priority,
        status,
      },
    })
  }

  const handleDelete = (task: Task) => {
    setDeletingTask(task)
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Tasks</h1>
          <p className="text-slate-500">Organize your work across to do, doing, and done.</p>
        </div>
        <Button onClick={openCreateDialog}>
          <Plus className="h-4 w-4" />
          New task
        </Button>
      </div>

      {isError && <p className="text-sm text-red-600">Failed to load tasks. Please try again.</p>}

      {isLoading ? (
        <div className="grid gap-4 sm:grid-cols-3">
          {columns.map((column) => (
            <div key={column.status} className="space-y-3">
              <Skeleton className="h-6 w-24" />
              <Skeleton className="h-32 w-full" />
              <Skeleton className="h-32 w-full" />
            </div>
          ))}
        </div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-3">
          {columns.map((column) => {
            const columnTasks = tasks.filter((task) => task.status === column.status)
            return (
              <div key={column.status} className="space-y-3">
                <h2 className="flex items-center gap-2 text-sm font-semibold uppercase tracking-wide text-slate-500">
                  {column.label}
                  <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600">
                    {columnTasks.length}
                  </span>
                </h2>
                <div className="space-y-3">
                  {columnTasks.length === 0 && (
                    <p className="rounded-card border border-dashed border-slate-200 p-4 text-center text-sm text-slate-400">
                      No tasks
                    </p>
                  )}
                  {columnTasks.map((task) => (
                    <TaskCard
                      key={task.id}
                      task={task}
                      onStatusChange={(status) => handleStatusChange(task, status)}
                      onEdit={() => openEditDialog(task)}
                      onDelete={() => handleDelete(task)}
                    />
                  ))}
                </div>
              </div>
            )
          })}
        </div>
      )}

      {dialogOpen && (
        <TaskFormDialog
          open={dialogOpen}
          onOpenChange={setDialogOpen}
          task={editingTask}
          onSubmit={handleSubmit}
          isSubmitting={createMutation.isPending || updateMutation.isPending}
        />
      )}

      <ConfirmDialog
        open={deletingTask !== null}
        onOpenChange={(open) => !open && setDeletingTask(null)}
        title={`Delete "${deletingTask?.title}"?`}
        description="This action cannot be undone."
        isConfirming={deleteMutation.isPending}
        onConfirm={() => deletingTask && deleteMutation.mutate(deletingTask.id)}
      />
    </div>
  )
}
