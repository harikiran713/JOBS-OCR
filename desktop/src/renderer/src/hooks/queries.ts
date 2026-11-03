import { useMutation, useQuery, useQueryClient, type UseQueryResult } from '@tanstack/react-query'
import { toast } from 'sonner'
import { api } from '@/lib/api'
import type { JobDetail, Project, Settings } from '@/lib/types'
import { useApp } from '@/stores/app'

export function useCurrentProject(): UseQueryResult<Project> & { projectId: string | null } {
  const projectId = useApp((s) => s.currentProjectId)
  const q = useQuery({ queryKey: ['project', projectId], queryFn: () => api.project(projectId!), enabled: !!projectId, retry: false })
  return Object.assign(q, { projectId })
}

export function useSettings(): UseQueryResult<Settings> {
  return useQuery({ queryKey: ['settings'], queryFn: api.settings, staleTime: 60_000 })
}

export function useReviewer(): string {
  const { data } = useSettings()
  return data?.['ui.reviewer_name'] || 'Reviewer'
}

/** Applies a job mutation, refreshes caches and reports errors consistently. */
export function useJobMutation<TArgs>(fn: (args: TArgs) => Promise<JobDetail>, success?: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: fn,
    onSuccess: (job) => {
      qc.setQueryData(['job', job.id], job)
      void qc.invalidateQueries({ queryKey: ['jobs'] })
      void qc.invalidateQueries({ queryKey: ['review-queue'] })
      void qc.invalidateQueries({ queryKey: ['project'] })
      if (success) toast.success(success)
    },
    onError: (e: Error) => toast.error(e.message)
  })
}
