import { zodResolver } from '@hookform/resolvers/zod'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Archive, ArchiveRestore, ListChecks, Pencil, Upload } from 'lucide-react'
import { useEffect, useState, type ReactNode } from 'react'
import { useForm } from 'react-hook-form'
import { useNavigate, useParams } from 'react-router-dom'
import { toast } from 'sonner'
import { useImport } from '@/components/project/ImportDropzone'
import { ManualsTable } from '@/components/project/ManualsTable'
import { ProcessingPanel } from '@/components/project/ProcessingPanel'
import { Button } from '@/components/ui/button'
import { Badge, ErrorState, PageHeader, Panel, Spinner, Stat } from '@/components/ui/display'
import { ConfirmDialog, Dialog, DialogContent } from '@/components/ui/overlay'
import { api } from '@/lib/api'
import type { Project } from '@/lib/types'
import { formatNumber } from '@/lib/utils'
import { useApp } from '@/stores/app'
import { ProjectFormFields, projectSchema, type ProjectForm } from './Projects'

function EditProject({ project, open, onOpenChange }: { project: Project; open: boolean; onOpenChange: (o: boolean) => void }): ReactNode {
  const qc = useQueryClient()
  const form = useForm<ProjectForm>({ resolver: zodResolver(projectSchema) })
  useEffect(() => {
    if (open) form.reset({ name: project.name, vessel_name: project.vessel_name, vessel_imo: project.vessel_imo, vessel_type: project.vessel_type, owner: project.owner, notes: project.notes })
  }, [open, project, form])
  const save = useMutation({
    mutationFn: (v: ProjectForm) => api.updateProject(project.id, v),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['project', project.id] })
      void qc.invalidateQueries({ queryKey: ['projects'] })
      onOpenChange(false)
      toast.success('Project details saved')
    },
    onError: (e: Error) => toast.error(e.message)
  })
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        title="Project details"
        className="w-[600px]"
        footer={
          <>
            <Button onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button variant="primary" loading={save.isPending} onClick={form.handleSubmit((v) => save.mutate(v))}>
              Save
            </Button>
          </>
        }
      >
        <ProjectFormFields form={form} />
      </DialogContent>
    </Dialog>
  )
}

export function ProjectOverviewPage(): ReactNode {
  const { projectId = '' } = useParams()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const current = useApp((s) => s.currentProjectId)
  const setProject = useApp((s) => s.setProject)
  const [editing, setEditing] = useState(false)
  const [confirmArchive, setConfirmArchive] = useState(false)
  useEffect(() => {
    if (projectId && current !== projectId) setProject(projectId)
  }, [projectId, current, setProject])

  const { data: project, error, refetch } = useQuery({ queryKey: ['project', projectId], queryFn: () => api.project(projectId), refetchInterval: 5000 })
  const { pick, busy } = useImport(projectId)
  const archive = useMutation({
    mutationFn: () => (project?.status === 'archived' ? api.restoreProject(projectId) : api.archiveProject(projectId)),
    onSuccess: (p) => {
      void qc.invalidateQueries({ queryKey: ['project', projectId] })
      void qc.invalidateQueries({ queryKey: ['projects'] })
      setConfirmArchive(false)
      toast.success(p.status === 'archived' ? 'Project archived' : 'Project restored')
    },
    onError: (e: Error) => toast.error(e.message)
  })

  if (error) return <ErrorState error={error} onRetry={() => void refetch()} />
  if (!project) return <Spinner />
  const s = project.stats
  const archived = project.status === 'archived'

  return (
    <div className="h-full overflow-auto">
      <PageHeader
        title={
          <span className="flex items-center gap-2">
            {project.name}
            {archived ? <Badge>Archived</Badge> : null}
          </span>
        }
        description={[project.vessel_name, project.vessel_imo && `IMO ${project.vessel_imo.replace(/^IMO\s?/i, '')}`, project.vessel_type, project.owner].filter(Boolean).join(' · ') || 'No vessel details'}
        actions={
          <>
            <Button variant="ghost" onClick={() => setEditing(true)}>
              <Pencil /> Edit details
            </Button>
            <Button variant="ghost" onClick={() => (archived ? archive.mutate() : setConfirmArchive(true))}>
              {archived ? <ArchiveRestore /> : <Archive />} {archived ? 'Restore' : 'Archive'}
            </Button>
            <Button onClick={() => pick.mutate()} loading={busy} disabled={archived}>
              <Upload /> Import manuals
            </Button>
            <Button variant="primary" onClick={() => navigate(`/projects/${projectId}/jobs`)} disabled={s.jobs_total === 0}>
              <ListChecks /> View jobs
            </Button>
          </>
        }
      />
      <div className="space-y-5 p-6">
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <Stat label="Manuals" value={formatNumber(s.documents)} hint={`${formatNumber(s.pages_total)} pages`} />
          <Stat label="Jobs identified" value={formatNumber(s.jobs_total)} hint={`${formatNumber(s.jobs_described)} with descriptions`} />
          <Stat label="Awaiting review" value={formatNumber(s.jobs_pending_review)} tone={s.jobs_pending_review ? 'warn' : undefined} />
          <Stat label="Approved" value={formatNumber(s.jobs_approved)} tone={s.jobs_approved ? 'ok' : undefined} />
        </div>
        <div className="grid grid-cols-1 gap-5 xl:grid-cols-[1.25fr_1fr]">
          <ProcessingPanel project={project} />
          <Panel title="Next steps">
            <div className="grid gap-2">
              {[
                { icon: <ListChecks />, label: 'View jobs', hint: `${formatNumber(s.jobs_total)} jobs · OCR / Capital template exports`, to: `/projects/${projectId}/jobs` }
              ].map((l) => (
                <button key={l.to} onClick={() => navigate(l.to)} className="flex items-center gap-3 rounded-md border border-line px-3 py-2.5 text-left hover:border-line-strong hover:bg-surface-2">
                  <span className="text-subtle [&_svg]:size-4">{l.icon}</span>
                  <span className="flex-1 text-[13px] font-medium">{l.label}</span>
                  <span className="text-xs text-muted">{l.hint}</span>
                </button>
              ))}
            </div>
          </Panel>
        </div>
        <section>
          <h2 className="mb-2 text-[13px] font-semibold">Manuals</h2>
          <ManualsTable project={project} />
        </section>
      </div>
      <EditProject project={project} open={editing} onOpenChange={setEditing} />
      <ConfirmDialog
        open={confirmArchive}
        onOpenChange={setConfirmArchive}
        title="Archive project?"
        description="Archived projects are hidden from the project switcher and cannot be processed. All manuals, jobs and review work are kept and the project can be restored at any time."
        confirmLabel="Archive"
        loading={archive.isPending}
        onConfirm={() => archive.mutate()}
      />
    </div>
  )
}
