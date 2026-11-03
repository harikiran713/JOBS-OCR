import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { RotateCcw } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { useParams, useSearchParams } from 'react-router-dom'
import { toast } from 'sonner'
import { PdfViewer } from '@/components/viewer/PdfViewer'
import { Button } from '@/components/ui/button'
import { Badge, ErrorState, Spinner, StatusBadge } from '@/components/ui/display'
import { NativeSelect } from '@/components/ui/form'
import { api } from '@/lib/api'
import { cn } from '@/lib/utils'

const KIND_LABEL: Record<string, string> = {
  schedule: 'Schedule',
  procedure: 'Procedure',
  parts_list: 'Parts list',
  toc: 'Contents',
  general: 'General',
  blank: 'Blank',
  unknown: '—'
}

/** Page registry side panel: every page with its explicit processing state. */
function PageRegistry({ documentId, current, onSelect }: { documentId: string; current: number; onSelect: (p: number) => void }): ReactNode {
  const [status, setStatus] = useState('')
  const [kind, setKind] = useState('')
  const qc = useQueryClient()
  const { data } = useQuery({
    queryKey: ['pages', documentId, status, kind],
    queryFn: () => api.pages(documentId, { status: status || undefined, kind: kind || undefined, limit: 1000 }),
    refetchInterval: 5000
  })
  const reprocess = useMutation({
    mutationFn: (p: number) => api.reprocessPage(documentId, p),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['pages', documentId] })
      toast.success('Page queued for reprocessing. Start processing on the project to run it.')
    },
    onError: (e: Error) => toast.error(e.message)
  })
  return (
    <aside className="flex w-[300px] shrink-0 flex-col border-l border-line bg-surface">
      <div className="space-y-2 border-b border-line p-3">
        <div className="text-[13px] font-semibold">Page registry</div>
        <div className="flex gap-2">
          <NativeSelect value={status} onChange={(e) => setStatus(e.target.value)} className="h-7 flex-1 text-xs" aria-label="Filter by status">
            <option value="">All statuses</option>
            <option value="completed">Completed</option>
            <option value="needs_review">Needs review</option>
            <option value="failed">Failed</option>
            <option value="pending">Pending</option>
          </NativeSelect>
          <NativeSelect value={kind} onChange={(e) => setKind(e.target.value)} className="h-7 flex-1 text-xs" aria-label="Filter by page type">
            <option value="">All types</option>
            <option value="schedule">Schedules</option>
            <option value="procedure">Procedures</option>
            <option value="parts_list">Parts lists</option>
            <option value="general">General</option>
          </NativeSelect>
        </div>
        <div className="text-2xs text-subtle">{data ? `${data.total} pages` : '…'}</div>
      </div>
      <ul className="min-h-0 flex-1 overflow-y-auto">
        {data?.items.map((p) => (
          <li key={p.page_no}>
            <button
              onClick={() => onSelect(p.page_no)}
              className={cn('group flex w-full items-center gap-2 border-b border-line px-3 py-1.5 text-left text-xs hover:bg-surface-2', p.page_no === current && 'bg-accent-soft')}
            >
              <span className="tabular w-9 shrink-0 text-muted">p.{p.page_no}</span>
              <span className="w-16 shrink-0 truncate text-muted">{KIND_LABEL[p.page_kind] ?? p.page_kind}</span>
              <span className="min-w-0 flex-1 truncate">
                {p.job_count ? <Badge tone="accent">{p.job_count} jobs</Badge> : null}
                {p.text_source === 'ocr' ? <Badge tone="info" className="ml-1">OCR</Badge> : null}
              </span>
              <StatusBadge status={p.status} kind="page" />
            </button>
            {p.page_no === current && p.status_reason ? (
              <div className="flex items-start gap-2 border-b border-line bg-surface-2 px-3 py-2 text-2xs text-muted">
                <span className="flex-1">{p.status_reason}</span>
                {p.status === 'failed' || p.status === 'needs_review' ? (
                  <Button size="sm" variant="ghost" className="h-6" onClick={() => reprocess.mutate(p.page_no)}>
                    <RotateCcw /> Reprocess
                  </Button>
                ) : null}
              </div>
            ) : null}
          </li>
        ))}
      </ul>
    </aside>
  )
}

export function DocumentViewerPage(): ReactNode {
  const { documentId = '' } = useParams()
  const [params, setParams] = useSearchParams()
  const page = Math.max(1, Number(params.get('page') ?? 1))
  const { data: doc, error, refetch } = useQuery({ queryKey: ['document', documentId], queryFn: () => api.document(documentId) })
  if (error) return <ErrorState error={error} onRetry={() => void refetch()} />
  if (!doc) return <Spinner />
  return (
    <div className="flex h-full">
      <div className="min-w-0 flex-1">
        <PdfViewer
          documentId={doc.id}
          pageCount={doc.page_count}
          page={Math.min(page, doc.page_count)}
          onPageChange={(p) => setParams({ page: String(p) }, { replace: true })}
          title={doc.filename}
          defaultThumbnails
          initialSearch={params.get('q') ?? undefined}
        />
      </div>
      <PageRegistry documentId={doc.id} current={page} onSelect={(p) => setParams({ page: String(p) }, { replace: true })} />
    </div>
  )
}
