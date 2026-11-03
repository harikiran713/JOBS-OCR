import { CheckCircle2, ChevronDown, ChevronRight, Eye, FileSearch, Ruler, XCircle } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/display'
import { Field, Input, NativeSelect } from '@/components/ui/form'
import { Tooltip } from '@/components/ui/overlay'
import type { DocumentInfo, JobDetail, Match } from '@/lib/types'
import { cn, pageRange } from '@/lib/utils'

const STAGE_LABEL: Record<string, string> = { exact: 'Exact', lexical: 'Lexical', semantic: 'Semantic' }

function BoundaryEditor({
  docs,
  initial,
  onSubmit,
  onCancel,
  busy,
  label
}: {
  docs: DocumentInfo[]
  initial: { document_id: string; start_page: number; end_page: number }
  onSubmit: (v: { document_id: string; start_page: number; end_page: number }) => void
  onCancel: () => void
  busy: boolean
  label: string
}): ReactNode {
  const [docId, setDocId] = useState(initial.document_id)
  const [start, setStart] = useState(String(initial.start_page))
  const [end, setEnd] = useState(String(initial.end_page))
  const max = docs.find((d) => d.id === docId)?.page_count ?? 1
  const s = Number(start)
  const e = Number(end)
  const error = !s || !e ? 'Enter page numbers' : s > e ? 'Start must be before end' : e > max ? `Document has ${max} pages` : ''
  return (
    <div className="space-y-2 rounded-md border border-accent/30 bg-accent-soft/50 p-3">
      <Field label="Manual">
        <NativeSelect value={docId} onChange={(ev) => setDocId(ev.target.value)}>
          {docs.filter((d) => d.status !== 'error').map((d) => (
            <option key={d.id} value={d.id}>
              {d.filename}
            </option>
          ))}
        </NativeSelect>
      </Field>
      <div className="grid grid-cols-2 gap-2">
        <Field label="Start page">
          <Input value={start} onChange={(ev) => setStart(ev.target.value.replace(/\D/g, ''))} inputMode="numeric" />
        </Field>
        <Field label="End page" error={error || undefined}>
          <Input value={end} onChange={(ev) => setEnd(ev.target.value.replace(/\D/g, ''))} inputMode="numeric" />
        </Field>
      </div>
      <p className="text-2xs text-muted">Descriptions extracted by the application are re-extracted from the new pages. Reviewer-edited text is kept and flagged.</p>
      <div className="flex justify-end gap-2">
        <Button size="sm" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
        <Button size="sm" variant="primary" disabled={!!error} loading={busy} onClick={() => onSubmit({ document_id: docId, start_page: s, end_page: e })}>
          {label}
        </Button>
      </div>
    </div>
  )
}

function MatchCard({
  m,
  onView,
  onSelect,
  onReject,
  onAdjust,
  busy,
  viewing
}: {
  m: Match
  onView: () => void
  onSelect: () => void
  onReject: () => void
  onAdjust: () => void
  busy: boolean
  viewing: boolean
}): ReactNode {
  const [open, setOpen] = useState(m.selected || m.rank === 0)
  const rejected = m.status === 'rejected'
  return (
    <div className={cn('rounded-md border', m.selected ? 'border-ok/40 bg-ok-soft/40' : rejected ? 'border-line bg-surface-2 opacity-70' : 'border-line bg-surface', viewing && 'ring-2 ring-accent/30')}>
      <div className="flex items-start gap-2 px-3 py-2">
        <button onClick={() => setOpen((o) => !o)} className="mt-0.5 text-subtle" aria-label="Toggle evidence">
          {open ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
        </button>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="truncate text-[13px] font-medium">{m.heading || 'Untitled section'}</span>
            {m.selected ? (
              <Badge tone={m.created_by === 'human' || m.status === 'selected' ? 'human' : 'ok'}>{m.status === 'verified' ? 'Verified' : 'Confirmed'}</Badge>
            ) : rejected ? (
              <Badge tone="danger">Marked incorrect</Badge>
            ) : (
              <Badge tone="warn">Candidate #{m.rank + 1}</Badge>
            )}
            {m.created_by === 'human' ? <Badge tone="human">Set by reviewer</Badge> : null}
          </div>
          <div className="mt-0.5 flex flex-wrap items-center gap-x-2 text-xs text-muted">
            <span>{m.document_name}</span>
            <span>p.{pageRange(m.start_page, m.end_page)}</span>
            {m.created_by === 'machine' ? <span className="tabular">score {m.score.toFixed(2)}</span> : null}
            {m.stages.map((s) => (
              <span key={s} className="rounded bg-surface-3 px-1 text-2xs">
                {STAGE_LABEL[s] ?? s}
              </span>
            ))}
          </div>
        </div>
        <Tooltip content="Show in viewer">
          <Button size="icon-sm" variant={viewing ? 'secondary' : 'ghost'} onClick={onView} aria-label="Show in viewer">
            <Eye />
          </Button>
        </Tooltip>
      </div>
      {open ? (
        <div className="space-y-2 border-t border-line px-3 py-2 text-xs">
          {m.evidence.verification?.map((v, i) => (
            <p key={i} className="flex gap-1.5 text-ok">
              <CheckCircle2 className="mt-px size-3.5 shrink-0" /> {v}
            </p>
          ))}
          {m.evidence.llm_check ? (
            <p className="text-muted">
              Model check: <b>{m.evidence.llm_check.verdict}</b> — {m.evidence.llm_check.reason}
              {!m.evidence.llm_check.grounded ? ' (quote not found in source; not used for verification)' : ''}
            </p>
          ) : null}
          {m.evidence.boundary?.length ? (
            <ul className="list-disc space-y-0.5 pl-4 text-muted">
              {m.evidence.boundary.map((b, i) => (
                <li key={i}>{b}</li>
              ))}
            </ul>
          ) : null}
          {m.evidence.notes?.map((n, i) => (
            <p key={i} className="text-warn">{n}</p>
          ))}
          {m.evidence.snippet ? <p className="selectable line-clamp-4 whitespace-pre-wrap rounded bg-surface-2 p-2 font-mono text-2xs text-muted">{m.evidence.snippet}</p> : null}
          {m.created_by === 'machine' && Object.keys(m.score_breakdown).length ? (
            <details className="text-2xs text-subtle">
              <summary className="cursor-pointer">Score breakdown</summary>
              <div className="mt-1 grid grid-cols-3 gap-x-3 gap-y-0.5">
                {Object.entries(m.score_breakdown).map(([k, v]) => (
                  <span key={k}>
                    {k.replace(/_/g, ' ')}: <span className="tabular">{typeof v === 'number' ? v.toFixed(2) : v}</span>
                  </span>
                ))}
              </div>
            </details>
          ) : null}
          <div className="flex flex-wrap gap-2 pt-1">
            {!m.selected ? (
              <Button size="sm" variant="primary" onClick={onSelect} loading={busy}>
                <CheckCircle2 /> Use this procedure
              </Button>
            ) : (
              <Button size="sm" onClick={onAdjust}>
                <Ruler /> Adjust pages
              </Button>
            )}
            {!rejected ? (
              <Button size="sm" variant="danger-outline" onClick={onReject} disabled={busy}>
                <XCircle /> Mark incorrect
              </Button>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  )
}

export function ProcedureMatches({
  job,
  docs,
  viewingMatchId,
  onView,
  onSelect,
  onReject,
  onManual,
  busy
}: {
  job: JobDetail
  docs: DocumentInfo[]
  viewingMatchId: number | null
  onView: (m: Match) => void
  onSelect: (m: Match) => void
  onReject: (m: Match) => void
  onManual: (v: { document_id: string; start_page: number; end_page: number; match_id?: number }) => void
  busy: boolean
}): ReactNode {
  const [editing, setEditing] = useState<{ match?: Match } | null>(null)
  const active = job.matches.filter((m) => m.status !== 'rejected')
  const rejected = job.matches.filter((m) => m.status === 'rejected')
  return (
    <div className="space-y-2">
      {job.procedure_status === 'needs_confirmation' ? (
        <p className="rounded-md border border-warn/25 bg-warn-soft px-3 py-2 text-xs text-warn">
          The procedure could not be verified automatically. Check the candidates against the source pages and confirm the correct one — or mark them incorrect and choose pages manually.
        </p>
      ) : job.procedure_status === 'not_found' ? (
        <p className="rounded-md border border-line bg-surface-2 px-3 py-2 text-xs text-muted">
          No procedure matching this job was found in the project's manuals. If it exists, select its pages manually.
        </p>
      ) : null}
      {active.map((m) =>
        editing?.match?.id === m.id ? (
          <BoundaryEditor
            key={m.id}
            docs={docs}
            initial={m}
            busy={busy}
            label="Save pages"
            onCancel={() => setEditing(null)}
            onSubmit={(v) => {
              onManual({ ...v, match_id: m.id })
              setEditing(null)
            }}
          />
        ) : (
          <MatchCard
            key={m.id}
            m={m}
            busy={busy}
            viewing={viewingMatchId === m.id}
            onView={() => onView(m)}
            onSelect={() => onSelect(m)}
            onReject={() => onReject(m)}
            onAdjust={() => setEditing({ match: m })}
          />
        )
      )}
      {editing && !editing.match ? (
        <BoundaryEditor
          docs={docs}
          initial={{ document_id: active[0]?.document_id ?? docs[0]?.id ?? '', start_page: active[0]?.start_page ?? 1, end_page: active[0]?.end_page ?? 1 }}
          busy={busy}
          label="Use these pages"
          onCancel={() => setEditing(null)}
          onSubmit={(v) => {
            onManual(v)
            setEditing(null)
          }}
        />
      ) : (
        <Button size="sm" variant="ghost" onClick={() => setEditing({})}>
          <FileSearch /> Select procedure pages manually
        </Button>
      )}
      {rejected.length ? (
        <details className="text-xs text-muted">
          <summary className="cursor-pointer">{rejected.length} marked incorrect</summary>
          <div className="mt-2 space-y-2">
            {rejected.map((m) => (
              <MatchCard key={m.id} m={m} busy={busy} viewing={viewingMatchId === m.id} onView={() => onView(m)} onSelect={() => onSelect(m)} onReject={() => undefined} onAdjust={() => undefined} />
            ))}
          </div>
        </details>
      ) : null}
    </div>
  )
}
