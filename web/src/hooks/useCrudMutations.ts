import { useMutation, useQueryClient, type QueryKey } from '@tanstack/react-query'
import { toast } from 'sonner'

interface CrudMutationsOptions<TEntity, TCreateInput, TUpdateInput> {
  queryKey: QueryKey
  create: (input: TCreateInput) => Promise<TEntity>
  update: (id: number, input: TUpdateInput) => Promise<TEntity>
  remove: (id: number) => Promise<void>
  onCreateSuccess?: (created: TEntity) => void
  onDeleteSuccess?: (deletedId: number) => void
  /** Human label (e.g. "Task") used in toast copy. Enables success toasts. */
  entityName?: string
  /** Set false to keep success toasts off but still surface errors. */
  toastSuccess?: boolean
}

/**
 * Shares the create/update/delete mutation + query-invalidation pattern that's
 * repeated across the Tasks, Notes, and Chat pages, plus consistent toast
 * feedback: errors always surface; successes toast when `entityName` is set.
 */
export function useCrudMutations<TEntity, TCreateInput, TUpdateInput = TCreateInput>({
  queryKey,
  create,
  update,
  remove,
  onCreateSuccess,
  onDeleteSuccess,
  entityName,
  toastSuccess = true,
}: CrudMutationsOptions<TEntity, TCreateInput, TUpdateInput>) {
  const queryClient = useQueryClient()

  const notifySuccess = (pastTense: string) => {
    if (entityName && toastSuccess) toast.success(`${entityName} ${pastTense}`)
  }
  const notifyError = (verb: string) =>
    toast.error(`Couldn't ${verb} ${entityName?.toLowerCase() ?? 'item'}. Please try again.`)

  const createMutation = useMutation({
    mutationFn: (input: TCreateInput) => create(input),
    onSuccess: (created) => {
      queryClient.invalidateQueries({ queryKey })
      notifySuccess('created')
      onCreateSuccess?.(created)
    },
    onError: () => notifyError('create'),
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, input }: { id: number; input: TUpdateInput }) => update(id, input),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey })
      notifySuccess('updated')
    },
    onError: () => notifyError('update'),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: number) => remove(id),
    onSuccess: (_data, deletedId) => {
      queryClient.invalidateQueries({ queryKey })
      notifySuccess('deleted')
      onDeleteSuccess?.(deletedId)
    },
    onError: () => notifyError('delete'),
  })

  return { createMutation, updateMutation, deleteMutation }
}
