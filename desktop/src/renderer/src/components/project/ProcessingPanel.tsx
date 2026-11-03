import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CircleStop, Pause, Play, RotateCcw } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Badge, Panel, Progress, StatusBadge } from '@/components/ui/display'
import { ConfirmDialog } from '@/components/ui/overlay'
import { api } from '@/lib/api'
import type { Project } from '@/lib/types'
import { formatNumber, pct, relativeTime } from '@/lib/utils'

export function ProcessingPanel({ project }: { project: Project }): ReactNode {
  const qc = useQueryClient()
  const [confirmCancel, setConfirmCancel] = useState(false)
  const { data: s } = useQuery({
    queryKey: ['processing-status', project.id],
    queryFn: () => api.processingStatus(project.id),
    refetchInterval: (q) => (q.state.data?.state === 'running' ? 1500 : 6000)
  })
  const refresh = (): void => {
    void qc.invalidateQueries({ queryKey: ['processing-status', project.id] })
    void qc.invalidateQueries({ queryKey: ['project', project.id] })
    void qc.invalidateQueries({ queryKey: ['documents', project.id] })
    void qc.invalidateQueries({ queryKey: ['dashboard'] })
  }
  const act = useMutation({
    mutationFn: async (action: 'start' | 'pause' | 'cancel' | 'retry') => {
      if (action === 'start') return api.startProcessing(project.id)
      if (action === 'pause') return api.pauseProcessing(project.id)
      if (action === 'cancel') return api.cancelProcessing(project.id)
      const r = await api.retryFailed(project.id)
      await api.startProcessing(project.id)
      return r
    },
    onSuccess: (_d, action) => {
      refresh()
      setConfirmCancel(false)
      toast.success({ start: 'Processing started', pause: 'Processing will pause after the current batch', cancel: 'Processing cancelled', retry: 'Retrying failed items' }[action])
    },
    onError: (e: Error) => toast.error(e.message)
  })

  const state = s?.state ?? project.processing_state
  const pages = s?.pages
  const jp = s?.jobs_procedure ?? {}
  const jd = s?.jobs_description ?? {}
  const totalJobs = Object.values(jp).reduce((a, b) => a + (b ?? 0), 0)
  const searched = totalJobs - (jp.pending ?? 0)
  const described = (jd.generated ?? 0) + (jd.provisional ?? 0) + (jd.needs_review ?? 0)
  const failedPages = pages?.failed ?? 0
  const archived = project.status === 'archived'

  return (
    <Panel
      title={
        <span className="flex items-center gap-2">
          Processing
          {state === 'running' ? <Badge tone="info" dot>Running</Badge> : state === 'paused' ? <Badge tone="warn" dot>Paused</Badge> : null}
          {s?.memory_wait ? <Badge tone="warn">Waiting for free memory</Badge> : null}
        </span>
      }
      actions={
        <>
          {state === 'running' ? (
            <Button size="sm" onClick={() => act.mutate('pause')} loading={act.isPending && act.variables === 'pause'}>
              <Pause /> Pause
            </Button>
          ) : (
            <Button size="sm" variant="primary" disabled={archived || project.stats.documents === 0} onClick={() => act.mutate('start')} loading={act.isPending && act.variables === 'start'}>
              <Play /> {state === 'paused' ? 'Resume' : 'Start processing'}
            </Button>
          )}
          {state !== 'idle' ? (
            <Button size="sm" variant="danger-outline" onClick={() => setConfirmCancel(true)}>
              <CircleStop /> Cancel
            </Button>
          ) : null}
          {state === 'idle' && failedPages + (jp.failed ?? 0) + (jd.failed ?? 0) > 0 ? (
            <Button size="sm" onClick={() => act.mutate('retry')} loading={act.isPending && act.variables === 'retry'}>
              <RotateCcw /> Retry failed
            </Button>
          ) : null}
        </>
      }
    >
      {!pages || pages.total === 0 ? (
        <p className="text-[13px] text-muted">Import manuals to begin. Processing runs locally and can be paused and resumed at any time.</p>
      ) : (
        <div className="space-y-4">
          {state !== 'idle' ? (
            <div className="rounded-md border border-line bg-surface-2 px-3 py-2">
              <div className="flex items-center justify-between text-[13px]">
                <span className="font-medium">{state === 'paused' ? 'Paused — work completed so far is saved' : s?.stage ?? 'Preparing…'}</span>
                {s?.run ? <span className="text-xs text-muted">Started {relativeTime(s.run.started_at)}</span> : null}
              </div>
              {s?.running.map((t) => (
                <p key={t.id} className="mt-0.5 truncate text-xs text-muted">
                  {t.message || t.label}
                  {t.attempts > 1 ? ` (attempt ${t.attempts})` : ''}
                </p>
              ))}
            </div>
          ) : null}
          <Stage label="Pages processed" done={pages.done} total={pages.total} detail={
            <span className="flex gap-2">
              {pages.needs_review ? <span className="text-warn">{formatNumber(pages.needs_review)} need review</span> : null}
              {failedPages ? <span className="text-danger">{formatNumber(failedPages)} failed</span> : null}
            </span>
          } active={state === 'running' && pages.done < pages.total} />
          <Stage label="Procedures searched" done={searched} total={totalJobs} detail={
            <span className="flex gap-2">
              {jp.verified ? <span className="text-ok">{jp.verified} verified</span> : null}
              {jp.needs_confirmation ? <span className="text-warn">{jp.needs_confirmation} to confirm</span> : null}
              {jp.not_found ? <span className="text-muted">{jp.not_found} not found</span> : null}
            </span>
          } active={state === 'running' && pages.done === pages.total && searched < totalJobs} />
          <Stage label="Descriptions extracted" done={described} total={totalJobs - (jd.not_available ?? 0)} active={state === 'running' && searched === totalJobs && totalJobs > 0} />
          {s?.run && state === 'idle' ? (
            <div className="flex items-center gap-2 text-xs text-muted">
              Last run <StatusBadge status={s.run.status} kind="run" /> {relativeTime(s.run.finished_at ?? s.run.started_at)} · {s.run.message}
            </div>
          ) : null}
        </div>
      )}
      <ConfirmDialog
        open={confirmCancel}
        onOpenChange={setConfirmCancel}
        title="Cancel processing?"
        description="Queued work is removed. Everything completed so far — extracted pages, jobs, matches and all review work — is kept. You can start processing again later to continue."
        confirmLabel="Cancel processing"
        destructive
        loading={act.isPending}
        onConfirm={() => act.mutate('cancel')}
      />
    </Panel>
  )
}

function Stage({ label, done, total, detail, active }: { label: string; done: number; total: number; detail?: ReactNode; active?: boolean }): ReactNode {
  return (
    <div>
      <div className="mb-1 flex items-baseline justify-between gap-3 text-[13px]">
        <span className="text-text">{label}</span>
        <span className="tabular text-xs text-muted">
          {formatNumber(done)} / {formatNumber(total)}
        </span>
      </div>
      <Progress value={pct(done, total)} tone={total > 0 && done >= total ? 'ok' : 'accent'} indeterminate={active && total === 0} />
      {detail ? <div className="mt-1 text-xs">{detail}</div> : null}
    </div>
  )
}
