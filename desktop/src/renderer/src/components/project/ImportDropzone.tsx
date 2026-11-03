import { useMutation, useQueryClient } from '@tanstack/react-query'
import { FileUp, Upload } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import type { ImportResult } from '@/lib/types'
import { cn } from '@/lib/utils'

function report(res: ImportResult | null): void {
  if (!res) return
  const ok = res.results.filter((r) => r.status === 'imported')
  const dup = res.results.filter((r) => r.status === 'duplicate')
  const bad = res.results.filter((r) => r.status === 'rejected' || r.status === 'error')
  if (ok.length) toast.success(`${ok.length} manual${ok.length > 1 ? 's' : ''} imported`, { description: ok.map((r) => r.filename).join(', ') })
  if (dup.length) toast.info(`${dup.length} already in this project`, { description: dup.map((r) => r.filename).join(', ') })
  for (const r of bad) toast.error(`${r.filename}: ${r.message ?? 'could not be imported'}`, { duration: 10000 })
}

export function useImport(projectId: string) {
  const qc = useQueryClient()
  const done = (res: ImportResult | null): void => {
    report(res)
    void qc.invalidateQueries({ queryKey: ['documents', projectId] })
    void qc.invalidateQueries({ queryKey: ['project', projectId] })
    void qc.invalidateQueries({ queryKey: ['processing-status', projectId] })
    void qc.invalidateQueries({ queryKey: ['projects'] })
  }
  const pick = useMutation({
    mutationFn: () => window.mmi.pickAndImport(projectId) as Promise<ImportResult | null>,
    onSuccess: done,
    onError: (e: Error) => toast.error(e.message)
  })
  const drop = useMutation({
    mutationFn: (files: File[]) => window.mmi.importDropped(projectId, files) as Promise<ImportResult>,
    onSuccess: done,
    onError: (e: Error) => toast.error(e.message)
  })
  return { pick, drop, busy: pick.isPending || drop.isPending }
}

/** Wraps content so PDFs can be dropped anywhere on it. */
export function ImportDropzone({ projectId, children, compact }: { projectId: string; children?: ReactNode; compact?: boolean }): ReactNode {
  const [over, setOver] = useState(false)
  const { pick, drop, busy } = useImport(projectId)
  return (
    <div
      className="relative h-full"
      onDragOver={(e) => {
        if (e.dataTransfer.types.includes('Files')) {
          e.preventDefault()
          setOver(true)
        }
      }}
      onDragLeave={(e) => {
        if (e.currentTarget === e.target || !e.currentTarget.contains(e.relatedTarget as Node)) setOver(false)
      }}
      onDrop={(e) => {
        e.preventDefault()
        setOver(false)
        const files = Array.from(e.dataTransfer.files)
        if (files.length) drop.mutate(files)
      }}
    >
      {children ?? (
        <div className={cn('flex flex-col items-center justify-center rounded-lg border-2 border-dashed border-line-strong bg-surface text-center', compact ? 'px-6 py-8' : 'px-6 py-16')}>
          <div className="mb-3 flex size-11 items-center justify-center rounded-lg bg-accent-soft text-accent-text">
            <FileUp className="size-5" />
          </div>
          <p className="text-[14px] font-semibold">Drop PDF manuals here</p>
          <p className="mt-1 max-w-sm text-[13px] text-muted">Original files are copied into the project library and never modified. Large manuals are inspected without loading them into memory.</p>
          <Button className="mt-4" variant="primary" onClick={() => pick.mutate()} loading={busy}>
            <Upload /> Choose files
          </Button>
        </div>
      )}
      {over ? (
        <div className="pointer-events-none absolute inset-0 z-30 flex items-center justify-center rounded-lg border-2 border-dashed border-accent bg-accent-soft/80">
          <div className="flex items-center gap-2 text-[14px] font-semibold text-accent-text">
            <FileUp className="size-5" /> Drop to import into this project
          </div>
        </div>
      ) : null}
    </div>
  )
}
