import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useVirtualizer } from '@tanstack/react-virtual'
import { Building2, ExternalLink, FileSpreadsheet, FileText, ListChecks, Search, X } from 'lucide-react'
import { useDeferredValue, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { DescriptionSection } from '@/components/job/Descriptions'
import { PdfViewer, type HighlightBand, type HighlightRect } from '@/components/viewer/PdfViewer'
import { Button } from '@/components/ui/button'
import { Badge, EmptyState, ErrorState, Spinner, StatusBadge } from '@/components/ui/display'
import { NativeSelect } from '@/components/ui/form'
import { Dialog, DialogContent, Tooltip } from '@/components/ui/overlay'
import { toast } from 'sonner'
import { useJobMutation, useReviewer } from '@/hooks/queries'
import { api } from '@/lib/api'
import type { JobDetail } from '@/lib/types'
import { cn, formatNumber, pageRange } from '@/lib/utils'
import { useApp } from '@/stores/app'

const FILTERS: Array<[string, string, Record<string, string>]> = [
  ['all', 'All jobs', {}],
  ['verified', 'Procedure verified', { procedure_status: 'verified,confirmed' }],
  ['confirm', 'Procedure to confirm', { procedure_status: 'needs_confirmation' }],
  ['pending', 'Not yet reviewed', { review_status: 'pending,needs_changes' }],
  ['approved', 'Approved', { review_status: 'approved' }]
]

function Field({ label, children }: { label: string; children: ReactNode }): ReactNode {
  return (
    <div className="min-w-0">
      <dt className="text-2xs uppercase tracking-wide text-subtle">{label}</dt>
      <dd className="selectable mt-0.5 truncate text-[13px]">{children}</dd>
    </div>
  )
}

function procedureTarget(job: JobDetail): { documentId: string; page: number; matchId: number | null } {
  const m = job.matches.find((x) => x.selected) ?? job.matches.find((x) => x.status !== 'rejected')
  if (m) return { documentId: m.document_id, page: m.start_page, matchId: m.id }
  return { documentId: job.document_id, page: job.schedule_page_no, matchId: null }
}

/** Right-hand side: job summary plus editable short and long descriptions; optional source PDF on the left. */
function JobPanel({ jobId, sourceOpen, onSourceOpenChange }: { jobId: string; sourceOpen: boolean; onSourceOpenChange: (open: boolean) => void }): ReactNode {
  const navigate = useNavigate()
  const reviewer = useReviewer()
  const { data: job, error } = useQuery({ queryKey: ['job', jobId], queryFn: () => api.job(jobId) })
  const edit = useJobMutation((v: { kind: 'long' | 'short'; content: string }) => api.editDescription(jobId, v.kind, v.content, reviewer), 'Description saved')
  const regenShort = useJobMutation(() => api.regenerateShort(jobId, reviewer), 'Short description regenerated from the long description')
  const regenLong = useJobMutation(() => api.regenerate(jobId, false, reviewer), 'Descriptions re-extracted from the manual')
  // Stay on the procedure/source page when the pane opens — never flash page 1.
  const [view, setView] = useState<{ documentId: string; page: number; matchId: number | null } | null>(null)
  useEffect(() => {
    if (job && sourceOpen) setView(procedureTarget(job))
  }, [job?.id, sourceOpen]) // eslint-disable-line react-hooks/exhaustive-deps
  const activeView = view ?? (job ? procedureTarget(job) : null)
  const { data: docs = [] } = useQuery({
    queryKey: ['documents', job?.project_id],
    queryFn: () => api.documents(job!.project_id),
    enabled: !!job && sourceOpen
  })
  const doc = activeView ? docs.find((d) => d.id === activeView.documentId) : undefined
  const { bands, rects } = useMemo(() => {
    const b: HighlightBand[] = []
    const r: HighlightRect[] = []
    if (!job || !activeView) return { bands: b, rects: r }
    if (activeView.documentId === job.document_id && job.source_bbox?.length === 4) {
      r.push({ page: job.schedule_page_no, rect: job.source_bbox, tone: 'schedule' })
    }
    const m = job.matches.find((x) => x.id === activeView.matchId)
    if (m && m.document_id === activeView.documentId) {
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
  }, [job, activeView])

  if (error) return <ErrorState error={error} />
  if (!job) return <Spinner />
  const proc = job.matches.find((m) => m.selected) ?? job.matches.find((m) => m.status !== 'rejected')
  const extra = job.extra_fields as Record<string, unknown>
  const showPage = (p: number): void => {
    const sel = job.long?.procedure_match_id ? job.matches.find((m) => m.id === job.long?.procedure_match_id) : undefined
    const documentId = sel?.document_id ?? proc?.document_id ?? activeView?.documentId ?? job.document_id
    setView({ documentId, page: p, matchId: sel?.id ?? activeView?.matchId ?? proc?.id ?? null })
    onSourceOpenChange(true)
  }
  const details = (
    <div className={cn('min-h-0 flex-1 overflow-y-auto', sourceOpen && 'border-l border-line')}>
      <div className="sticky top-0 z-10 border-b border-line bg-surface px-6 py-4">
        <div className="flex items-start gap-3">
          <div className="min-w-0 flex-1">
            <div className="tabular text-xs text-muted">
              {job.code}
              {extra.source_task_id ? ` · Task ID ${String(extra.source_task_id)}` : ''}
            </div>
            <h2 className="selectable mt-0.5 text-[16px] font-semibold leading-snug">{job.title}</h2>
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              <StatusBadge status={job.review_status} />
              <StatusBadge status={job.procedure_status} kind="proc" />
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <Tooltip content={sourceOpen ? 'Hide source PDF (Ctrl+P)' : 'Show source PDF beside this job (Ctrl+P)'}>
              <Button size="sm" variant={sourceOpen ? 'secondary' : 'primary'} onClick={() => onSourceOpenChange(!sourceOpen)} aria-pressed={sourceOpen}>
                <ExternalLink /> {sourceOpen ? 'Hide source PDF' : 'Review with source PDF'}
              </Button>
            </Tooltip>
            {sourceOpen ? (
              <Tooltip content="Open full review workspace">
                <Button size="sm" variant="ghost" onClick={() => navigate(`/review/${job.id}`)} aria-label="Open full review workspace">
                  Full review
                </Button>
              </Tooltip>
            ) : null}
          </div>
        </div>
        <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-2 lg:grid-cols-4">
          <Field label="Interval">
            {job.frequency_label}
            {job.frequency_uncertain ? <Badge tone="warn" className="ml-1">uncertain</Badge> : null}
          </Field>
          <Field label="Equipment">{job.equipment || '—'}</Field>
          <Field label="Component">{job.component || '—'}</Field>
          <Field label="Procedure source">{proc ? `${proc.document_name} p.${pageRange(proc.start_page, proc.end_page)}` : '—'}</Field>
        </dl>
        {Array.isArray(extra.schedule_comments) && extra.schedule_comments.length ? (
          <ul className="mt-3 space-y-0.5 rounded-md border border-info/20 bg-info-soft px-3 py-1.5 text-xs text-info">
            {(extra.schedule_comments as string[]).map((c) => (
              <li key={c}>{c}</li>
            ))}
          </ul>
        ) : null}
      </div>
      <div className="max-w-[920px] space-y-6 px-6 py-5">
        <DescriptionSection
          kind="short"
          d={job.short}
          provisional={job.description_status === 'provisional'}
          busy={regenShort.isPending || (edit.isPending && edit.variables?.kind === 'short')}
          regenerateLabel="Regenerate"
          onRegenerate={() => regenShort.mutate()}
          onSave={(content) => edit.mutate({ kind: 'short', content })}
        />
        <DescriptionSection
          kind="long"
          d={job.long}
          provisional={job.description_status === 'provisional'}
          busy={regenLong.isPending || (edit.isPending && edit.variables?.kind === 'long')}
          regenerateLabel="Re-extract"
          onPage={showPage}
          onRegenerate={() => regenLong.mutate()}
          onSave={(content) => edit.mutate({ kind: 'long', content })}
        />
      </div>
    </div>
  )
  if (!sourceOpen) return details
  return (
    <div className="flex h-full min-h-0 flex-1">
      <div className="flex min-h-0 min-w-0 flex-[1.4] flex-col bg-surface-3">
        {doc && activeView ? (
          <PdfViewer
            key={`${doc.id}-${activeView.matchId ?? 'src'}`}
            documentId={doc.id}
            pageCount={doc.page_count}
            page={Math.min(activeView.page, doc.page_count)}
            onPageChange={(p) => setView((v) => ({ ...(v ?? activeView), page: p }))}
            bands={bands}
            rects={rects}
            title={doc.filename}
            continuous
            toolbarExtra={
              <Button variant="ghost" size="icon-sm" onClick={() => onSourceOpenChange(false)} aria-label="Close source PDF">
                <X />
              </Button>
            }
          />
        ) : (
          <Spinner />
        )}
      </div>
      <div className="flex min-h-0 w-[min(42%,520px)] min-w-[340px] shrink-0 flex-col bg-surface">{details}</div>
    </div>
  )
}

type Template = 'ocr' | 'capital'

/** "OCR Template" and "Capital Template" Excel exports, each asking Long JD or Short Summary first.
 *  With a document selected, only the jobs extracted from that document are exported. */
function TemplateExports({ projectId, projectName, documentId, disabled }: { projectId: string; projectName: string; documentId: string; disabled: boolean }): ReactNode {
  const qc = useQueryClient()
  const [template, setTemplate] = useState<Template | null>(null)
  const [kind, setKind] = useState<'long' | 'short'>('long')
  const { data: docs } = useQuery({ queryKey: ['documents', projectId], queryFn: () => api.documents(projectId) })
  const usable = (docs ?? []).filter((d) => d.status !== 'error')
  const doc = documentId ? usable.find((d) => d.id === documentId) : usable.length === 1 ? usable[0] : undefined
  const baseName = doc ? doc.filename.replace(/\.pdf$/i, '') : projectName
  const run = useMutation({
    mutationFn: () => window.mmi.exportPms(projectId, template!, kind, baseName, documentId || null) as Promise<{ path: string; rows: number } | null>,
    onSuccess: (r) => {
      setTemplate(null)
      if (!r) return
      void qc.invalidateQueries({ queryKey: ['exports'] })
      toast.success(`Exported ${formatNumber(r.rows)} jobs`, { description: r.path, action: { label: 'Show file', onClick: () => void window.mmi.showExport(r.path) } })
    },
    onError: (e: Error) => toast.error(e.message)
  })
  const name = template === 'ocr' ? 'OCR Template' : 'Capital Template'
  return (
    <>
      <Button size="sm" disabled={disabled} onClick={() => setTemplate('ocr')}>
        <FileSpreadsheet /> OCR Template
      </Button>
      <Button size="sm" disabled={disabled} onClick={() => setTemplate('capital')}>
        <Building2 /> Capital Template
      </Button>
      <Dialog open={template !== null} onOpenChange={(o) => !o && setTemplate(null)}>
        <DialogContent
          title={`Export ${name}`}
          description={template === 'ocr' ? 'One sheet, one row per job.' : 'Three linked sheets: works, tasks and Documents.'}
          footer={
            <>
              <Button onClick={() => setTemplate(null)}>Cancel</Button>
              <Button variant="primary" loading={run.isPending} onClick={() => run.mutate()}>
                Export…
              </Button>
            </>
          }
        >
          <p className="mb-2 text-[13px] font-medium">Which description should go into the file?</p>
          <div className="grid gap-2">
            {(
              [
                ['long', 'Long JD', 'The full procedure text. If a job has no long description, its short summary is used.'],
                ['short', 'Short Summary', 'The short summary. If a job has none, the points of the long description are used.']
              ] as const
            ).map(([v, label, hint]) => (
              <label key={v} className="flex cursor-pointer items-start gap-2 rounded-md border border-line px-3 py-2 has-[:checked]:border-accent has-[:checked]:bg-accent-soft/50">
                <input type="radio" name="jd-kind" checked={kind === v} onChange={() => setKind(v)} className="mt-0.5 accent-[var(--accent)]" />
                <span>
                  <span className="block text-[13px] font-medium">{label}</span>
                  <span className="block text-xs text-muted">{hint}</span>
                </span>
              </label>
            ))}
          </div>
          <p className="mt-3 text-xs text-subtle">
            {documentId && doc ? `Jobs from ${doc.filename} only` : 'Jobs from all documents'} · file name: {baseName}_{template === 'ocr' ? 'OCRTemplate' : 'CapitalTemplate'}.xlsx · rejected jobs are left out.
          </p>
        </DialogContent>
      </Dialog>
    </>
  )
}

export function JobsPage(): ReactNode {
  const { projectId = '' } = useParams()
  const setProject = useApp((s) => s.setProject)
  const current = useApp((s) => s.currentProjectId)
  useEffect(() => {
    if (projectId && current !== projectId) setProject(projectId)
  }, [projectId, current, setProject])

  const [search, setSearch] = useState('')
  const q = useDeferredValue(search)
  const [filter, setFilter] = useState('all')
  const [selected, setSelected] = useState<string | null>(null)
  const [sourceOpen, setSourceOpen] = useState(false)
  const { data: project } = useQuery({ queryKey: ['project', projectId], queryFn: () => api.project(projectId) })
  const params = FILTERS.find((f) => f[0] === filter)?.[2] ?? {}
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['jobs', projectId, 'jobs-view', q, filter],
    queryFn: () => api.jobs(projectId, { q, ...params }),
    placeholderData: (p) => p
  })
  const projectRows = data?.items ?? []
  // Document-wise view: ?doc=<id> shows only the jobs extracted from that document.
  const [searchParams, setSearchParams] = useSearchParams()
  const docFilter = searchParams.get('doc') ?? ''
  const setDocFilter = (id: string): void =>
    setSearchParams((p) => {
      const n = new URLSearchParams(p)
      if (id) n.set('doc', id)
      else n.delete('doc')
      return n
    }, { replace: true })
  const { data: docs = [] } = useQuery({ queryKey: ['documents', projectId], queryFn: () => api.documents(projectId) })
  const docJobCounts = useMemo(() => {
    const m = new Map<string, number>()
    for (const j of projectRows) m.set(j.document_id, (m.get(j.document_id) ?? 0) + 1)
    return m
  }, [projectRows])
  const allRows = useMemo(() => (docFilter ? projectRows.filter((j) => j.document_id === docFilter) : projectRows), [projectRows, docFilter])
  const [interval, setIntervalFilter] = useState('')
  // Intervals present in the current list (after search/status/document filters), in a natural order.
  const intervals = useMemo(() => {
    const counts = new Map<string, { n: number; key: [number, number] }>()
    const days: Record<string, number> = { day: 1, week: 7, month: 30, year: 365 }
    const group: Record<string, number> = { calendar: 0, operating_hours: 1, running_hours: 1, engine_hours: 1, hours_unqualified: 1, cycle: 2, event: 3, condition: 4, unspecified: 5 }
    for (const j of allRows) {
      const v = j.frequency_value ?? 0
      const key: [number, number] = [group[j.frequency_type] ?? 6, j.frequency_type === 'calendar' ? v * (days[j.frequency_unit] ?? 1) : v]
      const e = counts.get(j.frequency_label)
      counts.set(j.frequency_label, { n: (e?.n ?? 0) + 1, key })
    }
    return [...counts.entries()].sort((a, b) => a[1].key[0] - b[1].key[0] || a[1].key[1] - b[1].key[1] || a[0].localeCompare(b[0]))
  }, [allRows])
  const rows = interval ? allRows.filter((j) => j.frequency_label === interval) : allRows
  const active = selected && rows.some((r) => r.id === selected) ? selected : rows[0]?.id ?? null
  const listRef = useRef<HTMLDivElement>(null)
  const virt = useVirtualizer({ count: rows.length, getScrollElement: () => listRef.current, estimateSize: () => 44, overscan: 15 })
  const listWidth = useApp((s) => s.jobsListWidth)
  const setListWidth = useApp((s) => s.setJobsListWidth)
  const splitRef = useRef<HTMLDivElement>(null)
  // Drag the divider to resize the job list; the width is remembered.
  const startResize = (e: React.PointerEvent<HTMLDivElement>): void => {
    e.preventDefault()
    const left = splitRef.current?.getBoundingClientRect().left ?? 0
    const total = splitRef.current?.clientWidth ?? 1200
    const move = (ev: PointerEvent): void => setListWidth(Math.round(Math.max(240, Math.min(total * 0.75, ev.clientX - left))))
    const up = (): void => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
      document.body.style.cursor = ''
    }
    document.body.style.cursor = 'col-resize'
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }

  // Arrow keys move through the list; Ctrl/Cmd+P toggles the source PDF beside the job description.
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if ((e.target as HTMLElement).closest('input, textarea, select, [contenteditable]')) return
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'p') {
        e.preventDefault()
        if (active) setSourceOpen((o) => !o)
        return
      }
      if (sourceOpen || (e.key !== 'ArrowDown' && e.key !== 'ArrowUp')) return
      const i = rows.findIndex((r) => r.id === active)
      const next = rows[Math.max(0, Math.min(rows.length - 1, i + (e.key === 'ArrowDown' ? 1 : -1)))]
      if (next) {
        e.preventDefault()
        setSelected(next.id)
        virt.scrollToIndex(rows.indexOf(next), { align: 'auto' })
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [rows, active, virt, sourceOpen])

  return (
    <div className="flex h-full flex-col">
      {error ? (
        <ErrorState error={error} onRetry={() => void refetch()} />
      ) : (
        <div ref={splitRef} className="flex min-h-0 flex-1">
          {!sourceOpen ? (
            <>
              <div className="flex shrink-0 flex-col bg-surface" style={{ width: listWidth }}>
                <div className="space-y-2 border-b border-line p-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <TemplateExports projectId={projectId} projectName={project?.name ?? 'jobs'} documentId={docFilter} disabled={!rows.length} />
                  </div>
                  <div className="flex items-baseline justify-between gap-2">
                    <h1 className="text-[15px] font-semibold">Jobs</h1>
                    <span className="tabular truncate text-xs text-muted">
                      {formatNumber(rows.length)} jobs{project ? ` · ${project.name}` : ''}
                    </span>
                  </div>
                  {docs.length > 1 || docFilter ? (
                    <div className="relative">
                      <FileText className="pointer-events-none absolute left-2.5 top-1/2 z-10 size-4 -translate-y-1/2 text-subtle" />
                      <NativeSelect value={docFilter} onChange={(e) => setDocFilter(e.target.value)} className="w-full min-w-0 pl-8" aria-label="Show jobs from document">
                        <option value="">All documents ({formatNumber(projectRows.length)} jobs)</option>
                        {docs.map((d) => (
                          <option key={d.id} value={d.id}>
                            {d.filename} ({formatNumber(docJobCounts.get(d.id) ?? 0)})
                          </option>
                        ))}
                        {docFilter && !docs.some((d) => d.id === docFilter) ? <option value={docFilter}>Removed document (0)</option> : null}
                      </NativeSelect>
                    </div>
                  ) : null}
                  <div className="relative">
                    <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-subtle" />
                    <input
                      value={search}
                      onChange={(e) => setSearch(e.target.value)}
                      placeholder="Search jobs…"
                      aria-label="Search jobs"
                      className="h-8 w-full rounded-md border border-line bg-surface pl-8 pr-2 text-[13px] focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/15"
                    />
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                  <NativeSelect value={filter} onChange={(e) => setFilter(e.target.value)} className="w-full min-w-0" aria-label="Filter jobs">
                    {FILTERS.map(([v, l]) => (
                      <option key={v} value={v}>
                        {l}
                      </option>
                    ))}
                  </NativeSelect>
                  <NativeSelect value={interval} onChange={(e) => setIntervalFilter(e.target.value)} className="w-full min-w-0" aria-label="Filter by interval">
                    <option value="">All intervals</option>
                    {intervals.map(([label, { n }]) => (
                      <option key={label} value={label}>
                        {label} ({n})
                      </option>
                    ))}
                    {interval && !intervals.some(([l]) => l === interval) ? <option value={interval}>{interval} (0)</option> : null}
                  </NativeSelect>
                  </div>
                </div>
                {isLoading ? (
                  <Spinner />
                ) : rows.length === 0 ? (
                  <EmptyState icon={<ListChecks />} title="No jobs" description="Jobs appear here after the manuals have been processed." />
                ) : (
                  <div ref={listRef} className="min-h-0 flex-1 overflow-y-auto" role="listbox" aria-label="Jobs">
                    <div style={{ height: virt.getTotalSize(), position: 'relative' }}>
                      {virt.getVirtualItems().map((vi) => {
                        const j = rows[vi.index]
                        return (
                          <button
                            key={j.id}
                            role="option"
                            aria-selected={active === j.id}
                            onClick={() => setSelected(j.id)}
                            className={cn(
                              'absolute left-0 w-full border-b border-l-2 border-b-line border-l-transparent px-3 py-1 text-left hover:bg-surface-2',
                              active === j.id && 'border-l-accent bg-accent-soft hover:bg-accent-soft'
                            )}
                            style={{ top: vi.start, height: vi.size }}
                            title={`${j.code} · ${j.title} · ${j.frequency_label} · ${j.equipment} · ${j.schedule_document} p.${j.schedule_page_no}`}
                          >
                            <div className="flex items-center gap-2">
                              <span className="min-w-0 flex-1 truncate text-[13px] font-medium leading-5">{j.title}</span>
                              <span
                                className={cn(
                                  'size-2 shrink-0 rounded-full',
                                  j.review_status === 'approved' ? 'bg-ok' : j.procedure_status === 'needs_confirmation' ? 'bg-warn' : 'bg-line-strong'
                                )}
                                aria-label={j.review_status === 'approved' ? 'Approved' : j.procedure_status === 'needs_confirmation' ? 'Procedure to confirm' : 'Pending review'}
                              />
                            </div>
                            <div className="flex gap-2 truncate text-2xs leading-4 text-muted">
                              <span className="tabular shrink-0 text-subtle">{j.code}</span>
                              <span className="max-w-[55%] shrink-0 truncate">{j.frequency_label}</span>
                              {!docFilter && docs.length > 1 ? <span className="truncate text-subtle">· {j.schedule_document}</span> : null}
                            </div>
                          </button>
                        )
                      })}
                    </div>
                  </div>
                )}
              </div>
              <div
                role="separator"
                aria-orientation="vertical"
                aria-label="Resize job list"
                title="Drag to resize · double-click to reset"
                onPointerDown={startResize}
                onDoubleClick={() => setListWidth(380)}
                className="group relative w-1.5 shrink-0 cursor-col-resize border-x border-line bg-surface-2 hover:bg-accent/30 active:bg-accent/40"
              >
                <span className="absolute left-1/2 top-1/2 h-8 w-0.5 -translate-x-1/2 -translate-y-1/2 rounded bg-line-strong group-hover:bg-accent" />
              </div>
            </>
          ) : null}
          <div className="flex min-w-0 flex-1 flex-col bg-bg">
            {active ? <JobPanel key={active} jobId={active} sourceOpen={sourceOpen} onSourceOpenChange={setSourceOpen} /> : null}
          </div>
        </div>
      )}
    </div>
  )
}
