import type {
  Dashboard,
  DocumentInfo,
  ExportOptions,
  ExportPreview,
  ExportRecord,
  ExportTemplate,
  HardwareInfo,
  JobDetail,
  JobRow,
  PageRow,
  PageTextLayer,
  ProcessingError,
  ProcessingRun,
  ProcessingStatus,
  ProcessingTask,
  Project,
  SearchResults,
  Settings,
  SystemStatus
} from './types'

let base = ''
let token = ''

export function configureApi(url: string, apiToken: string): void {
  base = url
  token = apiToken
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number
  ) {
    super(message)
  }
}

async function request<T>(path: string, init: { method?: string; body?: unknown; signal?: AbortSignal } = {}): Promise<T> {
  if (!base) throw new ApiError('Processing engine not connected', 0)
  let res: Response
  try {
    res = await fetch(`${base}/api${path}`, {
      method: init.method ?? 'GET',
      headers: { Authorization: `Bearer ${token}`, ...(init.body !== undefined ? { 'Content-Type': 'application/json' } : {}) },
      body: init.body !== undefined ? JSON.stringify(init.body) : undefined,
      signal: init.signal
    })
  } catch (e) {
    throw new ApiError(`Cannot reach the processing engine: ${(e as Error).message}`, 0)
  }
  if (!res.ok) {
    const data = (await res.json().catch(() => null)) as { detail?: unknown } | null
    const detail = data?.detail
    const msg =
      typeof detail === 'string'
        ? detail
        : Array.isArray(detail)
          ? detail.map((d: { msg?: string }) => d.msg).join('; ')
          : `Request failed (${res.status})`
    throw new ApiError(msg, res.status)
  }
  return (await res.json()) as T
}

const qs = (params: Record<string, string | number | boolean | undefined | null>): string => {
  const p = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== '') p.set(k, String(v))
  const s = p.toString()
  return s ? `?${s}` : ''
}

/** URL for <img> tags, which cannot send headers; the backend accepts the token as a query parameter for GET only. */
export const pageImageUrl = (documentId: string, pageNo: number, scale: number): string =>
  `${base}/api/documents/${documentId}/pages/${pageNo}/image${qs({ scale, token })}`

export const api = {
  health: () => request<{ ok: boolean; version: string }>('/health'),
  system: () => request<SystemStatus>('/system/status'),
  dashboard: () => request<Dashboard>('/dashboard'),

  projects: () => request<Project[]>('/projects'),
  project: (id: string) => request<Project>(`/projects/${id}`),
  createProject: (body: Partial<Project>) => request<Project>('/projects', { method: 'POST', body }),
  updateProject: (id: string, body: Partial<Project>) => request<Project>(`/projects/${id}`, { method: 'PATCH', body }),
  deleteProject: (id: string) => request<{ deleted: boolean; documents: number; jobs: number }>(`/projects/${id}`, { method: 'DELETE' }),
  archiveProject: (id: string) => request<Project>(`/projects/${id}/archive`, { method: 'POST' }),
  restoreProject: (id: string) => request<Project>(`/projects/${id}/restore`, { method: 'POST' }),

  documents: (projectId: string) => request<DocumentInfo[]>(`/projects/${projectId}/documents`),
  document: (id: string) => request<DocumentInfo>(`/documents/${id}`),
  updateDocument: (id: string, equipment_name: string) =>
    request<DocumentInfo>(`/documents/${id}`, { method: 'PATCH', body: { equipment_name } }),
  deleteDocument: (id: string) =>
    request<{ removed_jobs: number; removed_reviewed_jobs: number }>(`/documents/${id}`, { method: 'DELETE' }),
  pages: (id: string, params: { status?: string; kind?: string; offset?: number; limit?: number } = {}) =>
    request<{ total: number; items: PageRow[] }>(`/documents/${id}/pages${qs(params)}`),
  pageText: (id: string, pageNo: number) => request<PageTextLayer>(`/documents/${id}/pages/${pageNo}/text`),
  reprocessDocument: (id: string) => request<{ pages: number }>(`/documents/${id}/reprocess`, { method: 'POST' }),
  reprocessPage: (id: string, pageNo: number) => request(`/documents/${id}/pages/${pageNo}/reprocess`, { method: 'POST' }),
  searchDocument: (id: string, q: string) =>
    request<{ query: string; hits: Array<{ page_no: number; snippet: string }> }>(`/documents/${id}/search${qs({ q })}`),
  highlights: (id: string, pageNo: number, q: string) =>
    request<{ rects: number[][] }>(`/documents/${id}/pages/${pageNo}/highlights${qs({ q })}`),

  startProcessing: (projectId: string) => request<{ run_id: string }>(`/projects/${projectId}/processing/start`, { method: 'POST' }),
  pauseProcessing: (projectId: string) => request(`/projects/${projectId}/processing/pause`, { method: 'POST' }),
  cancelProcessing: (projectId: string) => request(`/projects/${projectId}/processing/cancel`, { method: 'POST' }),
  retryFailed: (projectId: string) => request<{ reset: number }>(`/projects/${projectId}/processing/retry-failed`, { method: 'POST' }),
  processingStatus: (projectId: string) => request<ProcessingStatus>(`/projects/${projectId}/processing/status`),
  runs: (projectId?: string) => request<ProcessingRun[]>(`/processing/runs${qs({ project_id: projectId })}`),
  tasks: (projectId: string, runId?: string) => request<ProcessingTask[]>(`/processing/tasks${qs({ project_id: projectId, run_id: runId })}`),
  errors: (projectId?: string) => request<ProcessingError[]>(`/processing/errors${qs({ project_id: projectId })}`),

  jobs: (projectId: string, params: Record<string, string | number | boolean | undefined> = {}) =>
    request<{ total: number; items: JobRow[] }>(`/projects/${projectId}/jobs${qs({ limit: 5000, ...params })}`),
  job: (id: string) => request<JobDetail>(`/jobs/${id}`),
  editJob: (id: string, changes: Record<string, unknown>, reviewer: string) =>
    request<JobDetail>(`/jobs/${id}`, { method: 'PATCH', body: { changes, reviewer } }),
  review: (id: string, action: string, reviewer: string, extra: { comment?: string; match_id?: number } = {}) =>
    request<JobDetail>(`/jobs/${id}/review`, { method: 'POST', body: { action, reviewer, ...extra } }),
  bulkReview: (job_ids: string[], action: string, reviewer: string) =>
    request<{ updated: number; skipped: Array<{ id: string; reason: string }> }>('/jobs/bulk-review', {
      method: 'POST',
      body: { job_ids, action, reviewer }
    }),
  selectMatch: (jobId: string, matchId: number, reviewer: string) =>
    request<JobDetail>(`/jobs/${jobId}/matches/${matchId}/select`, { method: 'POST', body: { reviewer } }),
  rejectMatch: (jobId: string, matchId: number, reviewer: string) =>
    request<JobDetail>(`/jobs/${jobId}/matches/${matchId}/reject`, { method: 'POST', body: { reviewer } }),
  manualMatch: (
    jobId: string,
    body: { document_id: string; start_page: number; end_page: number; reviewer: string; match_id?: number; heading?: string }
  ) => request<JobDetail>(`/jobs/${jobId}/matches/manual`, { method: 'POST', body }),
  regenerate: (jobId: string, force: boolean, reviewer: string) =>
    request<JobDetail>(`/jobs/${jobId}/descriptions/regenerate`, { method: 'POST', body: { force, reviewer } }),
  regenerateShort: (jobId: string, reviewer: string) =>
    request<JobDetail>(`/jobs/${jobId}/descriptions/short/regenerate`, { method: 'POST', body: { reviewer } }),
  editDescription: (jobId: string, kind: 'long' | 'short', content: string, reviewer: string) =>
    request<JobDetail>(`/jobs/${jobId}/descriptions/${kind}`, { method: 'PUT', body: { content, reviewer } }),
  reviewQueue: (projectId: string) =>
    request<{ jobs: JobRow[]; pages: Array<{ document_id: string; document_name: string; page_no: number; status: string; status_reason: string; page_kind: string }> }>(
      `/projects/${projectId}/review-queue`
    ),

  search: (projectId: string, q: string) => request<SearchResults>(`/projects/${projectId}/search${qs({ q })}`),

  exportFields: () => request<Array<{ field: string; label: string }>>('/export/fields'),
  exportTemplates: () => request<ExportTemplate[]>('/export/templates'),
  createTemplate: (body: Omit<ExportTemplate, 'id' | 'builtin'>) => request<{ id: string }>('/export/templates', { method: 'POST', body }),
  updateTemplate: (id: string, body: Omit<ExportTemplate, 'id' | 'builtin'>) =>
    request<{ id: string }>(`/export/templates/${id}`, { method: 'PUT', body }),
  deleteTemplate: (id: string) => request(`/export/templates/${id}`, { method: 'DELETE' }),
  exportPreview: (projectId: string, body: ExportOptions) =>
    request<ExportPreview>(`/projects/${projectId}/export/preview`, { method: 'POST', body }),
  exportHistory: (projectId: string) => request<ExportRecord[]>(`/projects/${projectId}/exports`),
  allExports: () => request<ExportRecord[]>('/exports'),

  settings: () => request<Settings>('/settings'),
  updateSettings: (changes: Partial<Settings>) => request<Settings>('/settings', { method: 'PUT', body: { changes } }),
  hardware: () => request<HardwareInfo>('/ai/hardware'),
  aiModels: () => request<{ models: Array<{ name: string; size_bytes: number | null }>; message?: string }>('/ai/models'),
  aiTest: () => request<Record<string, string>>('/ai/test', { method: 'POST' }),
  aiPull: (model: string) => request<{ model: string; status: string; completed: number; total: number; message: string }>('/ai/pull', { method: 'POST', body: { model } }),
  aiPullStatus: () => request<Array<{ model: string; status: string; completed: number; total: number; message: string }>>('/ai/pull'),
  ocrTest: () => request<{ engine: string; text: string; confidence: number }>('/ocr/test', { method: 'POST' })
}
