import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, FileStack, Info, ListChecks, Lock, MoreHorizontal, Pencil, RefreshCw, Trash2 } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { toast } from 'sonner'
import { ImportDropzone } from '@/components/project/ImportDropzone'
import { Button } from '@/components/ui/button'
import { Badge, EmptyState, ErrorState, Spinner, StatusBadge } from '@/components/ui/display'
import { Field, Input } from '@/components/ui/form'
import { ConfirmDialog, Dialog, DialogContent, Menu, MenuContent, MenuItem, MenuTrigger, Tooltip } from '@/components/ui/overlay'
import { api } from '@/lib/api'
import type { DocumentInfo, Project } from '@/lib/types'
import { formatBytes, formatDate, formatNumber, pct } from '@/lib/utils'

const TEXT_LABEL: Record<string, string> = { pending: 'Pending', partial: 'In progress', complete: 'Complete', unavailable: 'Unavailable' }
const OCR_LABEL: Record<string, string> = { pending: '—', not_needed: 'Not needed', complete: 'Done', partial: 'In progress', unavailable: 'Unavailable' }

function DocInfoDialog({ doc, onClose }: { doc: DocumentInfo | null; onClose: () => void }): ReactNode {
  const qc = useQueryClient()
  const [equipment, setEquipment] = useState('')
  const save = useMutation({
    mutationFn: () => api.updateDocument(doc!.id, equipment),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['documents'] })
      toast.success('Equipment name saved. It applies to jobs extracted from now on.')
      onClose()
    },
    onError: (e: Error) => toast.error(e.message)
  })
  if (!doc) return null
  const rows: Array<[string, ReactNode]> = [
    ['File', doc.filename],
    ['Original location', <span className="break-all">{doc.original_path}</span>],
    ['Size', formatBytes(doc.size_bytes)],
    ['Pages', formatNumber(doc.page_count)],
    ['Content', doc.content_type],
    ['PDF title', doc.title || '—'],
    ['Author', doc.metadata.author || '—'],
    ['Producer', doc.metadata.producer || '—'],
    ['Encrypted', doc.encrypted ? 'Yes (opened with empty password)' : 'No'],
    ['SHA-256', <span className="break-all font-mono text-2xs">{doc.sha256}</span>],
    ['Imported', formatDate(doc.imported_at)]
  ]
  return (
    <Dialog open={!!doc} onOpenChange={(o) => !o && onClose()}>
      <DialogContent
        title="Document details"
        className="w-[620px]"
        footer={
          <>
            <Button onClick={onClose}>Close</Button>
            <Button variant="primary" loading={save.isPending} disabled={!equipment.trim() || equipment === doc.equipment_name} onClick={() => save.mutate()}>
              Save equipment name
            </Button>
          </>
        }
      >
        <Field label="Equipment covered by this manual" hint="Used as the default equipment for jobs found in this manual.">
          <Input defaultValue={doc.equipment_name} onChange={(e) => setEquipment(e.target.value)} placeholder="e.g. Main Engine" />
        </Field>
        <dl className="mt-4 grid grid-cols-[150px_1fr] gap-x-4 gap-y-1.5 text-[13px]">
          {rows.map(([k, v]) => (
            <div key={k} className="contents">
              <dt className="text-muted">{k}</dt>
              <dd className="selectable">{v}</dd>
            </div>
          ))}
        </dl>
        {doc.warnings.length ? (
          <div className="mt-4 rounded-md border border-warn/25 bg-warn-soft p-3 text-xs text-warn">
            {doc.warnings.map((w, i) => (
              <p key={i}>{w}</p>
            ))}
          </div>
        ) : null}
      </DialogContent>
    </Dialog>
  )
}

/** Manuals of a project with import, details, re-process and remove actions (shown on the project page). */
export function ManualsTable({ project }: { project: Project }): ReactNode {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [info, setInfo] = useState<DocumentInfo | null>(null)
  const [removing, setRemoving] = useState<DocumentInfo | null>(null)
  const [reprocessing, setReprocessing] = useState<DocumentInfo | null>(null)
  const { data: docs, isLoading, error, refetch } = useQuery({
    queryKey: ['documents', project.id],
    queryFn: () => api.documents(project.id),
    refetchInterval: project.processing_state === 'running' ? 2000 : 8000
  })
  const remove = useMutation({
    mutationFn: (d: DocumentInfo) => api.deleteDocument(d.id),
    onSuccess: (r) => {
      void qc.invalidateQueries({ queryKey: ['documents', project.id] })
      void qc.invalidateQueries({ queryKey: ['project', project.id] })
      void qc.invalidateQueries({ queryKey: ['jobs'] })
      setRemoving(null)
      toast.success(`Document removed${r.removed_jobs ? ` with ${r.removed_jobs} jobs` : ''}`)
    },
    onError: (e: Error) => toast.error(e.message)
  })
  const reprocess = useMutation({
    mutationFn: async (d: DocumentInfo) => {
      await api.reprocessDocument(d.id)
      return api.startProcessing(project.id)
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['documents', project.id] })
      void qc.invalidateQueries({ queryKey: ['project', project.id] })
      void qc.invalidateQueries({ queryKey: ['processing-status', project.id] })
      setReprocessing(null)
      toast.success('Re-processing started')
    },
    onError: (e: Error) => toast.error(e.message)
  })
  const archived = project.status === 'archived'

  return (
    <div>
      <div>
        {error ? (
          <ErrorState error={error} onRetry={() => void refetch()} />
        ) : isLoading ? (
          <Spinner />
        ) : !docs?.length ? (
          archived ? (
            <EmptyState icon={<FileStack />} title="No manuals" description="Restore the project to import manuals." />
          ) : (
            <ImportDropzone projectId={project.id} />
          )
        ) : (
          <ImportDropzone projectId={project.id}>
            <div className="overflow-hidden rounded-lg border border-line bg-surface shadow-sm">
              <table className="w-full text-[13px]">
                <thead className="bg-surface-2">
                  <tr className="border-b border-line text-left text-xs text-muted">
                    <th className="px-4 py-2 font-medium">Manual</th>
                    <th className="px-3 py-2 font-medium">Size</th>
                    <th className="px-3 py-2 font-medium">Pages</th>
                    <th className="px-3 py-2 font-medium">Type</th>
                    <th className="px-3 py-2 font-medium">Imported</th>
                    <th className="px-3 py-2 font-medium">Status</th>
                    <th className="px-3 py-2 font-medium">Text</th>
                    <th className="px-3 py-2 font-medium">OCR</th>
                    <th className="px-3 py-2 font-medium">Jobs</th>
                    <th className="px-3 py-2 font-medium">Issues</th>
                    <th className="w-10" />
                  </tr>
                </thead>
                <tbody>
                  {docs.map((d) => {
                    const pages = d.stats.pages
                    const done = (pages.completed ?? 0) + (pages.needs_review ?? 0) + (pages.failed ?? 0) + (pages.skipped ?? 0)
                    const issues = (pages.failed ?? 0) + (pages.needs_review ?? 0) + d.warnings.length
                    const usable = d.status !== 'error'
                    return (
                      <tr
                        key={d.id}
                        className={usable ? 'cursor-pointer border-b border-line last:border-0 hover:bg-surface-2' : 'border-b border-line last:border-0'}
                        onClick={() => usable && navigate(`/documents/${d.id}`)}
                      >
                        <td className="max-w-[340px] px-4 py-2.5">
                          <div className="flex items-center gap-1.5 font-medium">
                            <span className="truncate">{d.filename}</span>
                            {d.encrypted ? (
                              <Tooltip content="Encrypted PDF">
                                <Lock className="size-3.5 shrink-0 text-subtle" />
                              </Tooltip>
                            ) : null}
                          </div>
                          <div className="truncate text-xs text-subtle">{d.status === 'error' ? <span className="text-danger">{d.error_message}</span> : d.equipment_name || 'Equipment not identified'}</div>
                        </td>
                        <td className="tabular whitespace-nowrap px-3 text-muted">{formatBytes(d.size_bytes)}</td>
                        <td className="tabular px-3">{formatNumber(d.page_count)}</td>
                        <td className="px-3 capitalize text-muted">{d.content_type === 'unknown' ? '—' : d.content_type}</td>
                        <td className="whitespace-nowrap px-3 text-xs text-muted">{formatDate(d.imported_at, false)}</td>
                        <td className="px-3">
                          <StatusBadge status={d.status} kind="doc" />
                        </td>
                        <td className="whitespace-nowrap px-3 text-xs">
                          {d.text_status === 'partial' ? (
                            <span className="tabular text-info">{pct(done, d.page_count)}%</span>
                          ) : (
                            <span className="text-muted">{TEXT_LABEL[d.text_status] ?? d.text_status}</span>
                          )}
                        </td>
                        <td className="whitespace-nowrap px-3 text-xs text-muted">
                          {d.stats.ocr_pages ? `${d.stats.ocr_pages} pages` : OCR_LABEL[d.ocr_status] ?? d.ocr_status}
                        </td>
                        <td className="tabular px-3" onClick={(e) => e.stopPropagation()}>
                          {d.stats.jobs ? (
                            <Tooltip content="Show the jobs extracted from this document">
                              <button className="text-accent underline-offset-2 hover:underline" onClick={() => navigate(`/projects/${project.id}/jobs?doc=${d.id}`)}>
                                {formatNumber(d.stats.jobs)}
                              </button>
                            </Tooltip>
                          ) : (
                            0
                          )}
                        </td>
                        <td className="px-3">
                          {issues ? (
                            <Tooltip content={`${pages.failed ?? 0} failed, ${pages.needs_review ?? 0} need review${d.warnings.length ? `, ${d.warnings.length} warnings` : ''}`}>
                              <span>
                                <Badge tone={pages.failed ? 'danger' : 'warn'}>
                                  <AlertTriangle className="size-3" /> {issues}
                                </Badge>
                              </span>
                            </Tooltip>
                          ) : (
                            <span className="text-xs text-subtle">—</span>
                          )}
                        </td>
                        <td className="pr-2" onClick={(e) => e.stopPropagation()}>
                          <Menu>
                            <MenuTrigger asChild>
                              <Button variant="ghost" size="icon-sm" aria-label="Document actions">
                                <MoreHorizontal />
                              </Button>
                            </MenuTrigger>
                            <MenuContent>
                              <MenuItem disabled={!d.stats.jobs} onSelect={() => navigate(`/projects/${project.id}/jobs?doc=${d.id}`)}>
                                <ListChecks /> View jobs from this document
                              </MenuItem>
                              <MenuItem onSelect={() => setInfo(d)}>
                                <Info /> Details & metadata
                              </MenuItem>
                              <MenuItem onSelect={() => setInfo(d)}>
                                <Pencil /> Set equipment name
                              </MenuItem>
                              <MenuItem disabled={d.status === 'error' || archived} onSelect={() => setReprocessing(d)}>
                                <RefreshCw /> Re-process document
                              </MenuItem>
                              <MenuItem danger onSelect={() => setRemoving(d)}>
                                <Trash2 /> Remove from project
                              </MenuItem>
                            </MenuContent>
                          </Menu>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
            <p className="mt-2 text-xs text-subtle">Drag PDF files onto this list to add more manuals.</p>
          </ImportDropzone>
        )}
      </div>
      <DocInfoDialog doc={info} onClose={() => setInfo(null)} />
      <ConfirmDialog
        open={!!reprocessing}
        onOpenChange={(o) => !o && setReprocessing(null)}
        title={`Re-process ${reprocessing?.filename ?? ''}?`}
        description="All pages are extracted again and jobs are re-identified with the current extraction logic. Jobs you have reviewed or edited are kept; unreviewed jobs are replaced by the new results."
        confirmLabel="Re-process"
        loading={reprocess.isPending}
        onConfirm={() => reprocessing && reprocess.mutate(reprocessing)}
      />
      <ConfirmDialog
        open={!!removing}
        onOpenChange={(o) => !o && setRemoving(null)}
        title={`Remove ${removing?.filename ?? ''}?`}
        description={
          <>
            The project's copy of this manual and its extracted pages are deleted, together with the <b>{removing?.stats.jobs ?? 0} jobs</b> found in it (including any review work on them). Your original file is not touched.
          </>
        }
        confirmLabel="Remove document"
        destructive
        loading={remove.isPending}
        onConfirm={() => removing && remove.mutate(removing)}
      />
    </div>
  )
}
