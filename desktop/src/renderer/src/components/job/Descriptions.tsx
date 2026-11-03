import { AlertTriangle, Image as ImageIcon, Pencil, RefreshCw, ShieldAlert, Wrench } from 'lucide-react'
import { useEffect, useState, type ReactNode } from 'react'
import { Button } from '@/components/ui/button'
import { Badge, ProvenanceTag, StatusBadge } from '@/components/ui/display'
import { Textarea } from '@/components/ui/form'
import type { Description, DescriptionBlock } from '@/lib/types'
import { cn } from '@/lib/utils'

function PageChip({ page, onClick }: { page: number; onClick?: (p: number) => void }): ReactNode {
  return (
    <button
      onClick={() => onClick?.(page)}
      className="tabular shrink-0 rounded border border-line bg-surface px-1 text-2xs text-muted hover:border-accent hover:text-accent-text"
      title={`Show page ${page}`}
    >
      p.{page}
    </button>
  )
}

function Block({ b, onPage }: { b: DescriptionBlock; onPage?: (p: number) => void }): ReactNode {
  const body = <span className="selectable whitespace-pre-wrap">{b.text}</span>
  if (b.type === 'heading') return <h4 className="flex items-start gap-2 pt-1 text-[13px] font-semibold"><span className="flex-1">{b.text}</span><PageChip page={b.page} onClick={onPage} /></h4>
  if (b.type === 'safety')
    return (
      <div className={cn('flex items-start gap-2 rounded border px-2 py-1.5', b.level === 'NOTE' || b.level === 'NOTICE' ? 'border-info/25 bg-info-soft' : 'border-danger/25 bg-danger-soft')}>
        <ShieldAlert className={cn('mt-0.5 size-3.5 shrink-0', b.level === 'NOTE' || b.level === 'NOTICE' ? 'text-info' : 'text-danger')} />
        <div className="flex-1">{body}</div>
        <PageChip page={b.page} onClick={onPage} />
      </div>
    )
  if (b.type === 'table')
    return (
      <div className="flex items-start gap-2">
        <pre className="selectable flex-1 overflow-x-auto rounded border border-line bg-surface-2 p-2 font-mono text-2xs">{b.text}</pre>
        <PageChip page={b.page} onClick={onPage} />
      </div>
    )
  if (b.type === 'figure')
    return (
      <div className="flex items-center gap-2 rounded border border-dashed border-line-strong px-2 py-1.5 text-muted" title="Figure in the manual (open the page to see it)">
        <ImageIcon className="size-3.5 shrink-0 text-subtle" aria-label="Figure" />
        <span className="selectable flex-1 italic">{b.text}</span>
        <PageChip page={b.page} onClick={onPage} />
      </div>
    )
  return (
    <div className="flex items-start gap-2">
      {b.type === 'tools' || b.type === 'materials' ? <Wrench className="mt-0.5 size-3.5 shrink-0 text-subtle" /> : null}
      <div className="flex-1">
        {body}
        {b.refs?.length ? (
          <span className="ml-1.5 inline-flex flex-wrap gap-1 align-middle">
            {b.refs.map((r) => (
              <Badge key={r} tone="info">
                ref: {r}
              </Badge>
            ))}
          </span>
        ) : null}
      </div>
      <PageChip page={b.page} onClick={onPage} />
    </div>
  )
}

function Issues({ d }: { d: Description }): ReactNode {
  const v = d.validation
  const items = [...(v.issues ?? []), ...(v.warnings ?? []), ...(v.outdated ? [v.outdated] : []), ...(v.source_rejected ? [v.source_rejected] : [])]
  const notes = v.notes ?? []
  if (!items.length && !notes.length) return null
  return (
    <div className="space-y-1">
      {items.map((t, i) => (
        <p key={i} className="flex gap-1.5 text-xs text-warn">
          <AlertTriangle className="mt-px size-3.5 shrink-0" /> {t}
        </p>
      ))}
      {notes.map((t, i) => (
        <p key={`n${i}`} className="text-xs text-muted">
          {t}
        </p>
      ))}
    </div>
  )
}

export function DescriptionSection({
  kind,
  d,
  provisional,
  onPage,
  onSave,
  onRegenerate,
  regenerateLabel,
  busy
}: {
  kind: 'long' | 'short'
  d: Description | null
  provisional: boolean
  onPage?: (p: number) => void
  onSave: (content: string) => void
  onRegenerate: () => void
  regenerateLabel: string
  busy: boolean
}): ReactNode {
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState(d?.content ?? '')
  useEffect(() => {
    setText(d?.content ?? '')
    setEditing(false)
  }, [d?.id, d?.content])
  const hasBlocks = kind === 'long' && d?.blocks?.length && d.created_by === 'machine'

  return (
    <section>
      <div className="mb-2 flex items-center gap-2">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-subtle">{kind === 'long' ? 'Long description' : 'Short description'}</h3>
        {d ? <ProvenanceTag human={d.created_by === 'human'} method={d.method} /> : null}
        {d ? <StatusBadge status={d.status} kind="desc" /> : null}
        {d && d.version > 1 ? <span className="text-2xs text-subtle">v{d.version}</span> : null}
        <span className="flex-1" />
        {!editing ? (
          <>
            <Button size="sm" variant="ghost" onClick={onRegenerate} loading={busy} title={regenerateLabel}>
              <RefreshCw /> {regenerateLabel}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setEditing(true)}>
              <Pencil /> Edit
            </Button>
          </>
        ) : null}
      </div>
      {provisional && d ? (
        <p className="mb-2 text-xs text-warn">Provisional: extracted from an unconfirmed procedure candidate. Confirm the procedure to finalise.</p>
      ) : null}
      {d ? <Issues d={d} /> : null}
      {editing ? (
        <div className="space-y-2">
          <Textarea value={text} onChange={(e) => setText(e.target.value)} rows={kind === 'long' ? 16 : 6} className="font-sans" />
          <p className="text-2xs text-muted">Your edit is saved as a new version; the extracted text remains in the history.</p>
          <div className="flex justify-end gap-2">
            <Button size="sm" variant="ghost" onClick={() => { setEditing(false); setText(d?.content ?? '') }}>
              Cancel
            </Button>
            <Button size="sm" variant="primary" disabled={text === (d?.content ?? '')} loading={busy} onClick={() => onSave(text)}>
              Save {kind} description
            </Button>
          </div>
        </div>
      ) : !d || !d.content ? (
        <p className="rounded-md border border-dashed border-line px-3 py-4 text-center text-xs text-subtle">
          {kind === 'long' ? 'No procedure text yet. Confirm or select the procedure pages first.' : 'No short description yet.'}
        </p>
      ) : hasBlocks ? (
        <div className="space-y-1.5 rounded-md border border-line bg-surface p-3 text-[13px] leading-relaxed">
          {d.blocks.map((b, i) => (
            <Block key={i} b={b} onPage={onPage} />
          ))}
        </div>
      ) : (
        <p className="selectable whitespace-pre-wrap rounded-md border border-line bg-surface p-3 text-[13px] leading-relaxed">{d.content}</p>
      )}
    </section>
  )
}
