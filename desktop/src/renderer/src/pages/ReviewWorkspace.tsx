import { useQuery } from '@tanstack/react-query'
import { Check, ChevronLeft, ChevronRight, History, ListRestart, MoreHorizontal, ThumbsDown, TriangleAlert } from 'lucide-react'
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { DescriptionSection } from '@/components/job/Descriptions'
import { JobFieldsForm } from '@/components/job/JobFieldsForm'
import { ProcedureMatches } from '@/components/job/ProcedureMatches'
import { PdfViewer, type HighlightBand, type HighlightRect } from '@/components/viewer/PdfViewer'
import { Button } from '@/components/ui/button'
import { Badge, ErrorState, Spinner, StatusBadge } from '@/components/ui/display'
import { Textarea } from '@/components/ui/form'
import { ConfirmDialog, Menu, MenuContent, MenuItem, MenuTrigger, Tooltip } from '@/components/ui/overlay'
import { useJobMutation, useReviewer } from '@/hooks/queries'
import { api } from '@/lib/api'
import type { JobDetail, Match } from '@/lib/types'
import { ATTENTION_LABELS, cn, pageRange } from '@/lib/utils'


interface ViewTarget {
  documentId: string
  page: number
  matchId: number | null
}

function Section({ title, children, right }: { title: ReactNode; children: ReactNode; right?: ReactNode }): ReactNode {
  return (
    <section className="border-b border-line px-5 py-4">
      <div className="mb-3 flex items-center gap-2">
        <h2 className="text-[13px] font-semibold">{title}</h2>
        <span className="flex-1" />
        {right}
      </div>
      {children}
    </section>
  )
}

function defaultTarget(job: JobDetail): ViewTarget {
  const m = job.matches.find((x) => x.selected) ?? job.matches.find((x) => x.status !== 'rejected')
  if (m) return { documentId: m.document_id, page: m.start_page, matchId: m.id }
  return { documentId: job.document_id, page: job.schedule_page_no, matchId: null }
}

function Workspace({ job, queue }: { job: JobDetail; queue: string[] }): ReactNode {
  const navigate = useNavigate()
  const reviewer = useReviewer()
  const [view, setView] = useState<ViewTarget>(() => defaultTarget(job))
  const [comment, setComment] = useState('')
  const [confirmForce, setConfirmForce] = useState(false)
  useEffect(() => setView(defaultTarget(job)), [job.id]) // eslint-disable-line react-hooks/exhaustive-deps

  const { data: docs = [] } = useQuery({ queryKey: ['documents', job.project_id], queryFn: () => api.documents(job.project_id) })
  const doc = docs.find((d) => d.id === view.documentId)

  const save = useJobMutation((changes: Record<string, unknown>) => api.editJob(job.id, changes, reviewer), 'Corrections saved')
  const review = useJobMutation((a: { action: string; match_id?: number }) => api.review(job.id, a.action, reviewer, { comment, match_id: a.match_id }))
  const select = useJobMutation((m: Match) => api.selectMatch(job.id, m.id, reviewer), 'Procedure confirmed')
  const reject = useJobMutation((m: Match) => api.rejectMatch(job.id, m.id, reviewer), 'Procedure marked incorrect')
  const manual = useJobMutation(
    (v: { document_id: string; start_page: number; end_page: number; match_id?: number }) => api.manualMatch(job.id, { ...v, reviewer }),
    'Procedure pages saved'
  )
  const regen = useJobMutation((force: boolean) => api.regenerate(job.id, force, reviewer), 'Descriptions re-extracted')
  const regenShort = useJobMutation(() => api.regenerateShort(job.id, reviewer), 'Short description regenerated')
  const editDesc = useJobMutation((v: { kind: 'long' | 'short'; content: string }) => api.editDescription(job.id, v.kind, v.content, reviewer), 'Description saved')

  const idx = queue.indexOf(job.id)
  const prevId = idx > 0 ? queue[idx - 1] : null
  const nextId = idx >= 0 && idx < queue.length - 1 ? queue[idx + 1] : idx === -1 ? queue[0] ?? null : null
  const needsConfirmation = job.procedure_status === 'needs_confirmation'
  const viewedCandidate = job.matches.find((m) => m.id === view.matchId && !m.selected && m.status !== 'rejected')

  const approve = (): void => {
    if (needsConfirmation) {
      if (viewedCandidate) review.mutate({ action: 'approve', match_id: viewedCandidate.id }, { onSuccess: () => nextId && navigate(`/review/${nextId}`) })
      return
    }
    review.mutate({ action: 'approve' }, { onSuccess: () => nextId && navigate(`/review/${nextId}`) })
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      const t = e.target as HTMLElement
      if (t.closest('input, textarea, select, [contenteditable]') || e.ctrlKey || e.metaKey || e.altKey) return
      if (e.key === 'j' && nextId) navigate(`/review/${nextId}`)
      if (e.key === 'k' && prevId) navigate(`/review/${prevId}`)
      if (e.key === 'a' && !review.isPending) approve()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  // Evidence overlays for the viewer: schedule row and procedure range (across pages).
  const { bands, rects } = useMemo(() => {
    const b: HighlightBand[] = []
    const r: HighlightRect[] = []
    if (view.documentId === job.document_id && job.source_bbox?.length === 4) {
      r.push({ page: job.schedule_page_no, rect: job.source_bbox, tone: 'schedule' })
    }
    const m = job.matches.find((x) => x.id === view.matchId)
    if (m && m.document_id === view.documentId) {
      for (let p = m.start_page; p <= m.end_page; p++) {
        b.push({
          page: p,
          y0: p === m.start_page ? m.start_y : 0,
          y1: p === m.end_page ? m.end_y : 1,
          tone: m.selected ? 'procedure' : 'candidate',
          label: p === m.start_page ? (m.selected ? 'Procedure start' : `Candidate #${m.rank + 1}`) : undefined
        })
      }
    }
    return { bands: b, rects: r }
  }, [job, view])

  const sources: Array<{ key: string; label: string; target: ViewTarget; tone: string }> = [
    { key: 'schedule', label: `Schedule · ${job.schedule_document} p.${job.schedule_page_no}`, target: { documentId: job.document_id, page: job.schedule_page_no, matchId: null }, tone: 'accent' },
    ...job.matches
      .filter((m) => m.status !== 'rejected')
      .map((m) => ({
        key: `m${m.id}`,
        label: `${m.selected ? 'Procedure' : `Candidate #${m.rank + 1}`} · ${m.document_name} p.${pageRange(m.start_page, m.end_page)}`,
        target: { documentId: m.document_id, page: m.start_page, matchId: m.id },
        tone: m.selected ? 'ok' : 'warn'
      }))
  ]
  const busyMatch = select.isPending || reject.isPending || manual.isPending || review.isPending
  const showPage = (p: number): void => {
    const sel = job.long?.procedure_match_id ? job.matches.find((m) => m.id === job.long?.procedure_match_id) : undefined
    setView({ documentId: sel?.document_id ?? view.documentId, page: p, matchId: sel?.id ?? view.matchId })
  }

  return (
    <div className="flex h-full min-h-0">
      {/* Left: source document */}
      <div className="flex min-w-0 flex-1 flex-col border-r border-line">
        <div className="flex shrink-0 gap-1 overflow-x-auto border-b border-line bg-surface px-2 py-1.5">
          {sources.map((s) => {
            const active = s.target.documentId === view.documentId && s.target.matchId === view.matchId && (s.key !== 'schedule' || view.page === job.schedule_page_no)
            return (
              <button
                key={s.key}
                onClick={() => setView(s.target)}
                className={cn(
                  'flex h-7 shrink-0 items-center gap-1.5 rounded-md border px-2 text-xs',
                  active ? 'border-accent bg-accent-soft text-accent-text' : 'border-line text-muted hover:border-line-strong hover:text-text'
                )}
              >
                <span className={cn('size-1.5 rounded-full', s.tone === 'ok' ? 'bg-ok' : s.tone === 'warn' ? 'bg-warn' : 'bg-accent')} />
                {s.label}
              </button>
            )
          })}
        </div>
        <div className="min-h-0 flex-1">
          {doc ? (
            <PdfViewer
              key={doc.id}
              documentId={doc.id}
              pageCount={doc.page_count}
              page={Math.min(view.page, doc.page_count)}
              onPageChange={(p) => setView((v) => ({ ...v, page: p }))}
              bands={bands}
              rects={rects}
              title={doc.filename}
            />
          ) : (
            <Spinner />
          )}
        </div>
      </div>

      {/* Right: extracted data */}
      <div className="flex w-[46%] min-w-[460px] max-w-[720px] flex-col bg-surface">
        <div className="shrink-0 border-b border-line px-5 py-3">
          <div className="flex items-center gap-2">
            <Button size="icon-sm" variant="ghost" disabled={!prevId} onClick={() => prevId && navigate(`/review/${prevId}`)} aria-label="Previous job (K)">
              <ChevronLeft />
            </Button>
            <span className="tabular text-xs text-muted">
              {job.code}
              {idx >= 0 ? ` · ${idx + 1} of ${queue.length} in queue` : ''}
            </span>
            <Button size="icon-sm" variant="ghost" disabled={!nextId} onClick={() => nextId && navigate(`/review/${nextId}`)} aria-label="Next job (J)">
              <ChevronRight />
            </Button>
            <span className="flex-1" />
            <StatusBadge status={job.review_status} />
          </div>
          <h1 className="selectable mt-1.5 text-[16px] font-semibold leading-snug">{job.title}</h1>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            <StatusBadge status={job.procedure_status} kind="proc" />
            <StatusBadge status={job.description_status} kind="desc" />
            {job.stale ? <Badge tone="warn">No longer found by re-extraction</Badge> : null}
            {job.extraction_method === 'llm' ? <Badge tone="info">Found by AI model</Badge> : null}
          </div>
          {job.attention.length ? (
            <div className="mt-2 flex flex-wrap gap-1">
              {job.attention.map((a) => (
                <Badge key={a} tone="warn">
                  <TriangleAlert className="size-3" /> {ATTENTION_LABELS[a] ?? a}
                </Badge>
              ))}
            </div>
          ) : null}
          <div className="mt-3 flex items-center gap-2">
            <Tooltip content={needsConfirmation && !viewedCandidate ? 'Confirm a procedure first, or view a candidate to approve with it' : 'Approve (A)'}>
              <span>
                <Button variant="success" onClick={approve} loading={review.isPending && review.variables?.action === 'approve'} disabled={needsConfirmation && !viewedCandidate}>
                  <Check /> {needsConfirmation && viewedCandidate ? `Approve with candidate #${viewedCandidate.rank + 1}` : 'Approve'}
                </Button>
              </span>
            </Tooltip>
            <Button onClick={() => review.mutate({ action: 'needs_changes' })}>Needs changes</Button>
            <Button variant="danger-outline" onClick={() => review.mutate({ action: 'reject' })}>
              <ThumbsDown /> Reject job
            </Button>
            <span className="flex-1" />
            <Menu>
              <MenuTrigger asChild>
                <Button variant="ghost" size="icon" aria-label="More actions">
                  <MoreHorizontal />
                </Button>
              </MenuTrigger>
              <MenuContent>
                <MenuItem onSelect={() => review.mutate({ action: 'mark_frequency_uncertain' })}>
                  <TriangleAlert /> Mark frequency uncertain
                </MenuItem>
                <MenuItem onSelect={() => review.mutate({ action: 'request_reprocess' })}>
                  <ListRestart /> Re-run procedure search
                </MenuItem>
                <MenuItem onSelect={() => review.mutate({ action: 'reset' })}>
                  <History /> Reset review status
                </MenuItem>
              </MenuContent>
            </Menu>
          </div>
          <Textarea value={comment} onChange={(e) => setComment(e.target.value)} rows={1} placeholder="Optional review comment (saved with the next decision)" className="mt-2 min-h-8 resize-y py-1.5 text-xs" />
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto">
          {job.extra_fields.source_task_id || job.extra_fields.schedule_comments ? (
            <Section title="Schedule details">
              <dl className="grid grid-cols-[130px_1fr] gap-y-1 text-[13px]">
                {job.extra_fields.source_task_id ? (<><dt className="text-muted">Maker task ID</dt><dd className="selectable tabular">{String(job.extra_fields.source_task_id)}</dd></>) : null}
                {job.extra_fields.system ? (<><dt className="text-muted">System</dt><dd className="selectable">{String(job.extra_fields.system)}</dd></>) : null}
                {job.extra_fields.schedule ? (<><dt className="text-muted">Schedule</dt><dd className="selectable">{String(job.extra_fields.schedule)}</dd></>) : null}
                {job.cross_reference ? (<><dt className="text-muted">Schedule Doc ID</dt><dd className="selectable">{job.cross_reference}</dd></>) : null}
              </dl>
              {Array.isArray(job.extra_fields.schedule_comments) && job.extra_fields.schedule_comments.length ? (
                <ul className="mt-2 space-y-1 rounded-md border border-info/20 bg-info-soft px-3 py-2 text-xs text-info">
                  {(job.extra_fields.schedule_comments as string[]).map((c) => (
                    <li key={c} className="selectable">{c}</li>
                  ))}
                </ul>
              ) : null}
            </Section>
          ) : null}

          <Section title="Checklist fields">
            <JobFieldsForm job={job} saving={save.isPending} onSave={(c) => save.mutate(c)} />
          </Section>

          <Section title="Procedure" right={<span className="text-xs text-muted">{job.matches.filter((m) => m.status !== 'rejected').length} candidate(s)</span>}>
            <ProcedureMatches
              job={job}
              docs={docs}
              viewingMatchId={view.matchId}
              busy={busyMatch}
              onView={(m) => setView({ documentId: m.document_id, page: m.start_page, matchId: m.id })}
              onSelect={(m) => select.mutate(m)}
              onReject={(m) => reject.mutate(m)}
              onManual={(v) => manual.mutate(v)}
            />
          </Section>

          <Section title="Job descriptions">
            <div className="space-y-5">
              <DescriptionSection
                kind="short"
                d={job.short}
                provisional={job.description_status === 'provisional'}
                busy={regenShort.isPending || (editDesc.isPending && editDesc.variables?.kind === 'short')}
                regenerateLabel="Regenerate"
                onRegenerate={() => regenShort.mutate()}
                onSave={(content) => editDesc.mutate({ kind: 'short', content })}
              />
              <DescriptionSection
                kind="long"
                d={job.long}
                provisional={job.description_status === 'provisional'}
                onPage={showPage}
                busy={regen.isPending || (editDesc.isPending && editDesc.variables?.kind === 'long')}
                regenerateLabel="Re-extract"
                onRegenerate={() => (job.long?.created_by === 'human' ? setConfirmForce(true) : regen.mutate(false))}
                onSave={(content) => editDesc.mutate({ kind: 'long', content })}
              />
            </div>
          </Section>
        </div>
      </div>
      <ConfirmDialog
        open={confirmForce}
        onOpenChange={setConfirmForce}
        title="Replace the edited description?"
        description="The long description was edited by a reviewer. Re-extracting creates a new version from the procedure pages; the edited version stays in the history."
        confirmLabel="Re-extract"
        loading={regen.isPending}
        onConfirm={() => regen.mutate(true, { onSettled: () => setConfirmForce(false) })}
      />
    </div>
  )
}

export function ReviewWorkspacePage(): ReactNode {
  const { jobId = '' } = useParams()
  const { data: job, error, refetch } = useQuery({ queryKey: ['job', jobId], queryFn: () => api.job(jobId) })
  const { data: queue } = useQuery({
    queryKey: ['review-queue', job?.project_id],
    queryFn: () => api.reviewQueue(job!.project_id),
    enabled: !!job
  })
  // Keep queue order stable while reviewing so approvals do not shift the position unexpectedly.
  const [order, setOrder] = useState<string[]>([])
  useEffect(() => {
    if (queue && (order.length === 0 || !order.includes(jobId))) setOrder(queue.jobs.map((j) => j.id))
  }, [queue, jobId, order])
  if (error) return <ErrorState error={error} onRetry={() => void refetch()} />
  if (!job) return <Spinner />
  return <Workspace job={job} queue={order} />
}
