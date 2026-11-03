import { zodResolver } from '@hookform/resolvers/zod'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Archive, ArchiveRestore, FolderKanban, MoreHorizontal, Plus, Trash2 } from 'lucide-react'
import { useEffect, useState, type ReactNode } from 'react'
import { useForm } from 'react-hook-form'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { toast } from 'sonner'
import { z } from 'zod'
import { Button } from '@/components/ui/button'
import { Badge, EmptyState, ErrorState, PageHeader, Progress, Spinner } from '@/components/ui/display'
import { Checkbox, Field, Input, Textarea } from '@/components/ui/form'
import { ConfirmDialog, Dialog, DialogContent, Menu, MenuContent, MenuItem, MenuTrigger } from '@/components/ui/overlay'
import { api } from '@/lib/api'
import type { Project } from '@/lib/types'
import { formatNumber, pct, relativeTime } from '@/lib/utils'
import { useApp } from '@/stores/app'

export const projectSchema = z.object({
  name: z.string().trim().min(1, 'Project name is required').max(200),
  vessel_name: z.string().trim().max(200),
  vessel_imo: z.string().trim().max(40), // no longer asked for; kept so older projects round-trip
  vessel_type: z.string().trim().max(100),
  owner: z.string().trim().max(200),
  notes: z.string().max(5000)
})
export type ProjectForm = z.infer<typeof projectSchema>

export function ProjectFormFields({ form }: { form: ReturnType<typeof useForm<ProjectForm>> }): ReactNode {
  const e = form.formState.errors
  return (
    <div className="grid grid-cols-2 gap-4">
      <Field label="Project name" error={e.name?.message} className="col-span-2" htmlFor="p-name">
        <Input id="p-name" autoFocus placeholder="e.g. MV Nordic Star – 2026 PMS setup" aria-invalid={!!e.name} {...form.register('name')} />
      </Field>
      <Field label="Ship name" htmlFor="p-vessel">
        <Input id="p-vessel" placeholder="MV Nordic Star" {...form.register('vessel_name')} />
      </Field>
      <Field label="Vessel type" htmlFor="p-type">
        <Input id="p-type" placeholder="Bulk carrier" {...form.register('vessel_type')} />
      </Field>
      <Field label="Notes" className="col-span-2" htmlFor="p-notes">
        <Textarea id="p-notes" rows={3} {...form.register('notes')} />
      </Field>
    </div>
  )
}

const EMPTY: ProjectForm = { name: '', vessel_name: '', vessel_imo: '', vessel_type: '', owner: '', notes: '' }

function CreateProjectDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }): ReactNode {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const setProject = useApp((s) => s.setProject)
  const form = useForm<ProjectForm>({ resolver: zodResolver(projectSchema), defaultValues: EMPTY })
  useEffect(() => {
    if (open) form.reset(EMPTY)
  }, [open, form])
  const create = useMutation({
    mutationFn: (v: ProjectForm) => api.createProject(v),
    onSuccess: (p) => {
      void qc.invalidateQueries({ queryKey: ['projects'] })
      void qc.invalidateQueries({ queryKey: ['dashboard'] })
      setProject(p.id)
      onOpenChange(false)
      toast.success('Project created — import the manuals next')
      navigate(`/projects/${p.id}`)
    },
    onError: (e: Error) => toast.error(e.message)
  })
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        title="New project"
        description="A project holds the manuals and maintenance checklist for one vessel or customer."
        className="w-[600px]"
        footer={
          <>
            <Button variant="secondary" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button variant="primary" loading={create.isPending} onClick={form.handleSubmit((v) => create.mutate(v))}>
              Create project
            </Button>
          </>
        }
      >
        <form onSubmit={form.handleSubmit((v) => create.mutate(v))}>
          <ProjectFormFields form={form} />
          <button type="submit" className="hidden" />
        </form>
      </DialogContent>
    </Dialog>
  )
}

export function ProjectsPage(): ReactNode {
  const [params, setParams] = useSearchParams()
  const [showArchived, setShowArchived] = useState(false)
  const navigate = useNavigate()
  const qc = useQueryClient()
  const projectId = useApp((s) => s.currentProjectId)
  const setProject = useApp((s) => s.setProject)
  const { data, isLoading, error, refetch } = useQuery({ queryKey: ['projects'], queryFn: api.projects, refetchInterval: 8000 })
  const open = params.get('new') === '1'
  const setOpen = (o: boolean): void => setParams(o ? { new: '1' } : {})

  const archive = useMutation({
    mutationFn: (p: Project) => (p.status === 'archived' ? api.restoreProject(p.id) : api.archiveProject(p.id)),
    onSuccess: (p) => {
      void qc.invalidateQueries({ queryKey: ['projects'] })
      toast.success(p.status === 'archived' ? 'Project archived' : 'Project restored')
      if (p.status === 'archived' && projectId === p.id) setProject(null)
    },
    onError: (e: Error) => toast.error(e.message)
  })

  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [toDelete, setToDelete] = useState<Project[] | null>(null)
  const del = useMutation({
    mutationFn: async (list: Project[]) => {
      for (const p of list) await api.deleteProject(p.id)
      return list
    },
    onSuccess: (list) => {
      if (list.some((p) => p.id === projectId)) setProject(null)
      setSelected(new Set())
      setToDelete(null)
      void qc.invalidateQueries({ queryKey: ['projects'] })
      toast.success(list.length === 1 ? `Project "${list[0].name}" deleted` : `${list.length} projects deleted`)
    },
    onError: (e: Error) => {
      void qc.invalidateQueries({ queryKey: ['projects'] })
      toast.error(e.message)
    }
  })
  const rows = (data ?? []).filter((p) => (showArchived ? true : p.status === 'active'))
  const toggle = (id: string, on: boolean): void =>
    setSelected((s) => {
      const n = new Set(s)
      if (on) n.add(id)
      else n.delete(id)
      return n
    })
  const allChecked = rows.length > 0 && rows.every((p) => selected.has(p.id))
  const someChecked = rows.some((p) => selected.has(p.id))
  const archivedCount = (data ?? []).filter((p) => p.status === 'archived').length

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        title="Projects"
        description="Each project has its own manuals, processing state and checklist"
        actions={
          <>
            {selected.size ? (
              <Button variant="danger" onClick={() => setToDelete((data ?? []).filter((p) => selected.has(p.id)))}>
                <Trash2 /> Delete selected ({selected.size})
              </Button>
            ) : null}
            {archivedCount ? (
              <Button variant="ghost" onClick={() => setShowArchived((s) => !s)}>
                {showArchived ? 'Hide archived' : `Show archived (${archivedCount})`}
              </Button>
            ) : null}
            <Button variant="primary" onClick={() => setOpen(true)}>
              <Plus /> New project
            </Button>
          </>
        }
      />
      <div className="min-h-0 flex-1 overflow-auto p-6">
        {error ? (
          <ErrorState error={error} onRetry={() => void refetch()} />
        ) : isLoading ? (
          <Spinner />
        ) : rows.length === 0 ? (
          <EmptyState
            icon={<FolderKanban />}
            title="No projects yet"
            description="Create a project for a vessel, then import its equipment manuals."
            action={
              <Button variant="primary" onClick={() => setOpen(true)}>
                <Plus /> New project
              </Button>
            }
          />
        ) : (
          <div className="overflow-hidden rounded-lg border border-line bg-surface shadow-sm">
            <table className="w-full text-[13px]">
              <thead className="bg-surface-2">
                <tr className="border-b border-line text-left text-xs text-muted">
                  <th className="w-10 pl-4">
                    <Checkbox
                      aria-label="Select all projects"
                      checked={allChecked ? true : someChecked ? 'indeterminate' : false}
                      onCheckedChange={(v) => setSelected(v ? new Set(rows.map((p) => p.id)) : new Set())}
                    />
                  </th>
                  <th className="px-4 py-2 font-medium">Project</th>
                  <th className="px-3 py-2 font-medium">Vessel</th>
                  <th className="px-3 py-2 font-medium">Manuals</th>
                  <th className="w-[20%] px-3 py-2 font-medium">Processing</th>
                  <th className="px-3 py-2 font-medium">Jobs</th>
                  <th className="px-3 py-2 font-medium">Approved</th>
                  <th className="px-3 py-2 font-medium">Updated</th>
                  <th className="w-10" />
                </tr>
              </thead>
              <tbody>
                {rows.map((p) => (
                  <tr
                    key={p.id}
                    className="cursor-pointer border-b border-line last:border-0 hover:bg-surface-2"
                    onClick={() => {
                      setProject(p.id)
                      navigate(`/projects/${p.id}`)
                    }}
                  >
                    <td className="pl-4" onClick={(e) => e.stopPropagation()}>
                      <Checkbox aria-label={`Select ${p.name}`} checked={selected.has(p.id)} onCheckedChange={(v) => toggle(p.id, !!v)} />
                    </td>
                    <td className="px-4 py-2.5">
                      <div className="flex items-center gap-2 font-medium">
                        {p.name}
                        {p.id === projectId ? <Badge tone="accent">Current</Badge> : null}
                        {p.status === 'archived' ? <Badge>Archived</Badge> : null}
                      </div>
                    </td>
                    <td className="px-3 text-muted">
                      {p.vessel_name || '—'}
                      {p.vessel_imo ? <span className="ml-1 text-xs text-subtle">IMO {p.vessel_imo.replace(/^IMO\s?/i, '')}</span> : null}
                    </td>
                    <td className="tabular px-3">{p.stats.documents}</td>
                    <td className="px-3">
                      {p.stats.pages_total ? (
                        <div className="flex items-center gap-2">
                          <Progress value={pct(p.stats.pages_done, p.stats.pages_total)} className="w-24" tone={p.stats.pages_done >= p.stats.pages_total ? 'ok' : 'accent'} />
                          <span className="tabular text-xs text-muted">{pct(p.stats.pages_done, p.stats.pages_total)}%</span>
                          {p.processing_state !== 'idle' ? <Badge tone={p.processing_state === 'running' ? 'info' : 'warn'}>{p.processing_state}</Badge> : null}
                        </div>
                      ) : (
                        <span className="text-xs text-subtle">No manuals</span>
                      )}
                    </td>
                    <td className="tabular px-3">{formatNumber(p.stats.jobs_total)}</td>
                    <td className="tabular px-3">{formatNumber(p.stats.jobs_approved)}</td>
                    <td className="px-3 text-xs text-muted">{relativeTime(p.updated_at)}</td>
                    <td className="pr-2" onClick={(e) => e.stopPropagation()}>
                      <Menu>
                        <MenuTrigger asChild>
                          <Button variant="ghost" size="icon-sm" aria-label="Project actions">
                            <MoreHorizontal />
                          </Button>
                        </MenuTrigger>
                        <MenuContent>
                          <MenuItem onSelect={() => archive.mutate(p)}>
                            {p.status === 'archived' ? <ArchiveRestore /> : <Archive />}
                            {p.status === 'archived' ? 'Restore project' : 'Archive project'}
                          </MenuItem>
                          <MenuItem danger onSelect={() => setToDelete([p])}>
                            <Trash2 /> Delete project
                          </MenuItem>
                        </MenuContent>
                      </Menu>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
      <CreateProjectDialog open={open} onOpenChange={setOpen} />
      <ConfirmDialog
        open={!!toDelete}
        onOpenChange={(o) => !o && setToDelete(null)}
        title={toDelete && toDelete.length > 1 ? `Delete ${toDelete.length} projects?` : `Delete project "${toDelete?.[0]?.name ?? ''}"?`}
        description={
          <div className="space-y-2">
            {toDelete && toDelete.length > 1 ? (
              <ul className="max-h-32 list-disc overflow-auto pl-5">
                {toDelete.map((p) => (
                  <li key={p.id}>
                    {p.name} — {p.stats.documents} manuals, {p.stats.jobs_total} jobs
                  </li>
                ))}
              </ul>
            ) : toDelete?.[0] ? (
              <p>
                {toDelete[0].stats.documents} manuals and {toDelete[0].stats.jobs_total} jobs ({toDelete[0].stats.jobs_approved} approved).
              </p>
            ) : null}
            <p>
              This permanently deletes the project's copies of the manuals, all jobs, descriptions and review work. It cannot be undone. Your original PDF files are not touched. To keep the work but hide the project, use Archive instead.
            </p>
          </div>
        }
        confirmLabel="Delete permanently"
        destructive
        loading={del.isPending}
        onConfirm={() => toDelete && del.mutate(toDelete)}
      />
    </div>
  )
}
