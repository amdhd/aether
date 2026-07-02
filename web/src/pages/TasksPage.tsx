import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ClipboardList, Plus } from 'lucide-react'
import { useState } from 'react'
import { toast } from 'sonner'

import { createTask, deleteTask, listTasks, updateTask } from '@/api/tasks'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import { EmptyState } from '@/components/EmptyState'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { TaskCard } from '@/components/tasks/TaskCard'
import { TaskFormDialog } from '@/components/tasks/TaskFormDialog'
import { useCrudMutations } from '@/hooks/useCrudMutations'
import type { Page, Task, TaskCreateInput, TaskStatus } from '@/types'

const columns: { status: TaskStatus; label: string }[] = [
  { status: 'todo', label: 'To do' },
  { status: 'doing', label: 'Doing' },
  { status: 'done', label: 'Done' },
]

const TASKS_KEY = ['tasks']

export function TasksPage() {
  const queryClient = useQueryClient()
  const { data, isLoading, isError } = useQuery({ queryKey: TASKS_KEY, queryFn: listTasks })
  const tasks = data?.items ?? []

  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingTask, setEditingTask] = useState<Task | null>(null)
  const [deletingTask, setDeletingTask] = useState<Task | null>(null)

  const { createMutation, updateMutation, deleteMutation } = useCrudMutations<Task, TaskCreateInput>({
    queryKey: TASKS_KEY,
    create: createTask,
    update: updateTask,
    remove: deleteTask,
    entityName: 'Task',
    onDeleteSuccess: () => setDeletingTask(null),
  })

  // Moving a card between columns updates the board immediately and rolls back
  // if the server rejects it, so the kanban feels instant.
  const statusMutation = useMutation({
    mutationFn: ({ task, status }: { task: Task; status: TaskStatus }) =>
      updateTask(task.id, { status }),
    onMutate: async ({ task, status }) => {
      await queryClient.cancelQueries({ queryKey: TASKS_KEY })
      const previous = queryClient.getQueryData<Page<Task>>(TASKS_KEY)
      queryClient.setQueryData<Page<Task>>(TASKS_KEY, (old) =>
        old
          ? { ...old, items: old.items.map((t) => (t.id === task.id ? { ...t, status } : t)) }
          : old,
      )
      return { previous }
    },
    onError: (_err, _vars, context) => {
      if (context?.previous) queryClient.setQueryData(TASKS_KEY, context.previous)
      toast.error("Couldn't move task. Please try again.")
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: TASKS_KEY }),
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
    statusMutation.mutate({ task, status })
  }

  const handleDelete = (task: Task) => {
    setDeletingTask(task)
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Tasks</h1>
          <p className="text-muted-foreground">Organize your work across to do, doing, and done.</p>
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
      ) : tasks.length === 0 ? (
        <EmptyState
          icon={<ClipboardList className="h-6 w-6" />}
          title="No tasks yet"
          description="Create your first task to start organizing your work."
          action={
            <Button onClick={openCreateDialog}>
              <Plus className="h-4 w-4" />
              New task
            </Button>
          }
        />
      ) : (
        <div className="grid gap-4 sm:grid-cols-3">
          {columns.map((column) => {
            const columnTasks = tasks.filter((task) => task.status === column.status)
            return (
              <div key={column.status} className="space-y-3">
                <h2 className="flex items-center gap-2 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
                  {column.label}
                  <span className="rounded-full bg-surface-muted px-2 py-0.5 text-xs font-medium text-muted-foreground">
                    {columnTasks.length}
                  </span>
                </h2>
                <div className="space-y-3">
                  {columnTasks.length === 0 && (
                    <p className="rounded-card border border-dashed border-border p-4 text-center text-sm text-muted-foreground">
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
