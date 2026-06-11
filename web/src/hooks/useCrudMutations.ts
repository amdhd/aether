import { useMutation, useQueryClient, type QueryKey } from '@tanstack/react-query'

interface CrudMutationsOptions<TEntity, TCreateInput, TUpdateInput> {
  queryKey: QueryKey
  create: (input: TCreateInput) => Promise<TEntity>
  update: (id: number, input: TUpdateInput) => Promise<TEntity>
  remove: (id: number) => Promise<void>
  onCreateSuccess?: (created: TEntity) => void
  onDeleteSuccess?: (deletedId: number) => void
}

/**
 * Shares the create/update/delete mutation + query-invalidation pattern that's
 * repeated across the Tasks, Notes, and Chat pages.
 */
export function useCrudMutations<TEntity, TCreateInput, TUpdateInput = TCreateInput>({
  queryKey,
  create,
  update,
  remove,
  onCreateSuccess,
  onDeleteSuccess,
}: CrudMutationsOptions<TEntity, TCreateInput, TUpdateInput>) {
  const queryClient = useQueryClient()

  const createMutation = useMutation({
    mutationFn: (input: TCreateInput) => create(input),
    onSuccess: (created) => {
      queryClient.invalidateQueries({ queryKey })
      onCreateSuccess?.(created)
    },
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, input }: { id: number; input: TUpdateInput }) => update(id, input),
    onSuccess: () => queryClient.invalidateQueries({ queryKey }),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: number) => remove(id),
    onSuccess: (_data, deletedId) => {
      queryClient.invalidateQueries({ queryKey })
      onDeleteSuccess?.(deletedId)
    },
  })

  return { createMutation, updateMutation, deleteMutation }
}
