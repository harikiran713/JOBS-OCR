import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs))
}

export function formatBytes(bytes: number): string {
  if (!bytes) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1)
  return `${(bytes / 1024 ** i).toFixed(i === 0 ? 0 : 1)} ${units[i]}`
}

export function formatNumber(n: number | null | undefined): string {
  return (n ?? 0).toLocaleString()
}

export function formatDate(iso: string | null | undefined, withTime = true): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return withTime
    ? d.toLocaleString(undefined, { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
    : d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
}

export function relativeTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const diff = (Date.now() - new Date(iso).getTime()) / 1000
  if (diff < 45) return 'just now'
  if (diff < 3600) return `${Math.round(diff / 60)} min ago`
  if (diff < 86400) return `${Math.round(diff / 3600)} h ago`
  if (diff < 86400 * 7) return `${Math.round(diff / 86400)} d ago`
  return formatDate(iso, false)
}

export function pct(part: number, total: number): number {
  return total > 0 ? Math.round((part / total) * 100) : 0
}

export function pageRange(start?: number | null, end?: number | null): string {
  if (!start) return '—'
  return end && end !== start ? `${start}–${end}` : String(start)
}

export const ATTENTION_LABELS: Record<string, string> = {
  extraction_needs_review: 'Extraction uncertain',
  frequency_uncertain: 'Frequency uncertain',
  frequency_missing: 'No frequency in source',
  procedure_needs_confirmation: 'Confirm procedure',
  procedure_not_found: 'Procedure not found',
  procedure_failed: 'Procedure search failed',
  description_needs_review: 'Description needs review',
  description_provisional: 'Description provisional',
  description_failed: 'Description failed',
  stale: 'No longer found in source'
}
