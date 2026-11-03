import { AlertTriangle, Loader2, RotateCw } from 'lucide-react'
import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'
import { Button } from './button'

/* ------------------------------------------------------------------- Badge */
export type Tone = 'neutral' | 'accent' | 'ok' | 'warn' | 'danger' | 'info' | 'human'

const toneClass: Record<Tone, string> = {
  neutral: 'bg-surface-3 text-muted border-line',
  accent: 'bg-accent-soft text-accent-text border-accent/20',
  ok: 'bg-ok-soft text-ok border-ok/20',
  warn: 'bg-warn-soft text-warn border-warn/25',
  danger: 'bg-danger-soft text-danger border-danger/20',
  info: 'bg-info-soft text-info border-info/20',
  human: 'bg-human-soft text-human border-human/25'
}

export function Badge({ tone = 'neutral', children, className, dot }: { tone?: Tone; children: ReactNode; className?: string; dot?: boolean }): ReactNode {
  return (
    <span className={cn('inline-flex h-5 items-center gap-1 whitespace-nowrap rounded border px-1.5 text-2xs font-medium', toneClass[tone], className)}>
      {dot ? <span className="size-1.5 rounded-full bg-current" /> : null}
      {children}
    </span>
  )
}

const STATUS: Record<string, [string, Tone]> = {
  // review
  pending: ['Pending review', 'neutral'],
  approved: ['Approved', 'ok'],
  rejected: ['Rejected', 'danger'],
  needs_changes: ['Needs changes', 'warn'],
  // procedure
  'proc:pending': ['Not searched', 'neutral'],
  'proc:verified': ['Verified match', 'ok'],
  'proc:confirmed': ['Confirmed by reviewer', 'human'],
  'proc:needs_confirmation': ['Needs confirmation', 'warn'],
  'proc:not_found': ['Not found', 'danger'],
  'proc:failed': ['Search failed', 'danger'],
  // descriptions
  'desc:pending': ['Not generated', 'neutral'],
  'desc:generated': ['Generated', 'ok'],
  'desc:provisional': ['Provisional', 'warn'],
  'desc:needs_review': ['Needs review', 'warn'],
  'desc:failed': ['Failed', 'danger'],
  'desc:not_available': ['No procedure', 'neutral'],
  // pages
  'page:pending': ['Pending', 'neutral'],
  'page:processing': ['Processing', 'info'],
  'page:completed': ['Completed', 'ok'],
  'page:needs_review': ['Needs review', 'warn'],
  'page:failed': ['Failed', 'danger'],
  'page:skipped': ['Skipped', 'neutral'],
  // documents
  'doc:imported': ['Imported', 'neutral'],
  'doc:processing': ['Processing', 'info'],
  'doc:ready': ['Ready', 'ok'],
  'doc:error': ['Error', 'danger'],
  // runs
  'run:running': ['Running', 'info'],
  'run:paused': ['Paused', 'warn'],
  'run:completed': ['Completed', 'ok'],
  'run:completed_with_errors': ['Completed with issues', 'warn'],
  'run:cancelled': ['Cancelled', 'neutral'],
  // tasks
  'task:pending': ['Queued', 'neutral'],
  'task:running': ['Running', 'info'],
  'task:completed': ['Done', 'ok'],
  'task:failed': ['Failed', 'danger'],
  'task:cancelled': ['Cancelled', 'neutral']
}

export function StatusBadge({ status, kind }: { status: string; kind?: 'proc' | 'desc' | 'page' | 'doc' | 'run' | 'task' }): ReactNode {
  const [label, tone] = STATUS[kind ? `${kind}:${status}` : status] ?? [status.replace(/_/g, ' '), 'neutral']
  return (
    <Badge tone={tone} dot>
      {label}
    </Badge>
  )
}

/* -------------------------------------------------------------- Layout bits */
export function PageHeader({ title, description, actions, children }: { title: ReactNode; description?: ReactNode; actions?: ReactNode; children?: ReactNode }): ReactNode {
  return (
    <div className="border-b border-line bg-surface px-6 pb-0 pt-5">
      <div className="flex items-start justify-between gap-6 pb-4">
        <div className="min-w-0">
          <h1 className="truncate text-[19px] font-semibold tracking-tight text-text">{title}</h1>
          {description ? <p className="mt-0.5 text-[13px] text-muted">{description}</p> : null}
        </div>
        {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
      </div>
      {children}
    </div>
  )
}

export function Panel({ title, actions, children, className, bodyClassName }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string; bodyClassName?: string }): ReactNode {
  return (
    <section className={cn('rounded-lg border border-line bg-surface shadow-sm', className)}>
      {title || actions ? (
        <header className="flex min-h-11 items-center justify-between gap-3 border-b border-line px-4 py-2">
          <h2 className="text-[13px] font-semibold text-text">{title}</h2>
          {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
        </header>
      ) : null}
      <div className={cn('p-4', bodyClassName)}>{children}</div>
    </section>
  )
}

export function EmptyState({ icon, title, description, action, className }: { icon: ReactNode; title: string; description?: ReactNode; action?: ReactNode; className?: string }): ReactNode {
  return (
    <div className={cn('flex flex-col items-center justify-center px-6 py-14 text-center', className)}>
      <div className="mb-3 flex size-11 items-center justify-center rounded-lg border border-line bg-surface-2 text-subtle [&_svg]:size-5">{icon}</div>
      <h3 className="text-[14px] font-semibold text-text">{title}</h3>
      {description ? <p className="mt-1 max-w-md text-[13px] text-muted">{description}</p> : null}
      {action ? <div className="mt-4">{action}</div> : null}
    </div>
  )
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }): ReactNode {
  return (
    <div className="m-6 flex items-start gap-3 rounded-lg border border-danger/25 bg-danger-soft px-4 py-3 text-[13px] text-danger">
      <AlertTriangle className="mt-0.5 size-4 shrink-0" />
      <div className="flex-1">
        <p className="font-medium">Could not load data</p>
        <p className="mt-0.5 opacity-90">{error instanceof Error ? error.message : String(error)}</p>
      </div>
      {onRetry ? (
        <Button size="sm" variant="secondary" onClick={onRetry}>
          <RotateCw /> Retry
        </Button>
      ) : null}
    </div>
  )
}

export function Spinner({ className, label }: { className?: string; label?: string }): ReactNode {
  return (
    <div className={cn('flex items-center justify-center gap-2 py-10 text-[13px] text-muted', className)}>
      <Loader2 className="size-4 animate-spin" />
      {label ?? 'Loading…'}
    </div>
  )
}

export function Skeleton({ className }: { className?: string }): ReactNode {
  return <div className={cn('animate-pulse rounded bg-surface-3', className)} />
}

export function Progress({ value, indeterminate, tone = 'accent', className }: { value?: number; indeterminate?: boolean; tone?: 'accent' | 'ok' | 'warn'; className?: string }): ReactNode {
  const color = tone === 'ok' ? 'bg-ok' : tone === 'warn' ? 'bg-warn' : 'bg-accent'
  return (
    <div className={cn('relative h-1.5 w-full overflow-hidden rounded-full bg-surface-3', className)} role="progressbar" aria-valuenow={value} aria-valuemin={0} aria-valuemax={100}>
      {indeterminate ? (
        <div className={cn('absolute inset-y-0 w-2/5 rounded-full animate-indeterminate', color)} />
      ) : (
        <div className={cn('h-full rounded-full transition-[width] duration-500', color)} style={{ width: `${Math.max(0, Math.min(100, value ?? 0))}%` }} />
      )}
    </div>
  )
}

export function Stat({ label, value, hint, tone, icon }: { label: string; value: ReactNode; hint?: ReactNode; tone?: Tone; icon?: ReactNode }): ReactNode {
  const accent = tone === 'warn' ? 'text-warn' : tone === 'danger' ? 'text-danger' : tone === 'ok' ? 'text-ok' : 'text-text'
  return (
    <div className="flex flex-col gap-1 rounded-lg border border-line bg-surface px-4 py-3 shadow-sm">
      <div className="flex items-center justify-between text-xs text-muted">
        <span>{label}</span>
        {icon ? <span className="text-subtle [&_svg]:size-4">{icon}</span> : null}
      </div>
      <div className={cn('tabular text-[22px] font-semibold leading-tight tracking-tight', accent)}>{value}</div>
      {hint ? <div className="text-xs text-subtle">{hint}</div> : null}
    </div>
  )
}

export function Kbd({ children }: { children: ReactNode }): ReactNode {
  return <kbd className="rounded border border-line bg-surface-2 px-1 font-mono text-2xs text-muted">{children}</kbd>
}

/** Visual marker distinguishing reviewer-corrected values from machine output. */
export function ProvenanceTag({ human, method }: { human: boolean; method?: string }): ReactNode {
  if (human) return <Badge tone="human">Reviewer edit</Badge>
  if (method?.startsWith('llm-condensed')) return <Badge tone="info">Copied from source · AI-shortened</Badge>
  if (method?.startsWith('llm')) return <Badge tone="info">AI-generated</Badge>
  if (method === 'extractive') return <Badge tone="neutral">Extracted from source</Badge>
  return <Badge tone="neutral">Machine</Badge>
}
