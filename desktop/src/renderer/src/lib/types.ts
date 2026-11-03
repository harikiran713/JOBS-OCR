/** Typed API contract — mirrors backend/mmi/api/serializers.py and route responses. */

export type ProcessingState = 'idle' | 'running' | 'paused'
export type ReviewStatus = 'pending' | 'approved' | 'rejected' | 'needs_changes'
export type ProcedureStatus = 'pending' | 'verified' | 'needs_confirmation' | 'confirmed' | 'not_found' | 'failed'
export type DescriptionStatus = 'pending' | 'generated' | 'provisional' | 'needs_review' | 'failed' | 'not_available'
export type PageStatus = 'pending' | 'processing' | 'completed' | 'needs_review' | 'failed' | 'skipped'
export type FrequencyType =
  | 'calendar'
  | 'operating_hours'
  | 'running_hours'
  | 'engine_hours'
  | 'hours_unqualified'
  | 'cycle'
  | 'event'
  | 'condition'
  | 'unspecified'

export interface ProjectStats {
  documents: number
  pages_total: number
  pages_done: number
  pages_failed: number
  pages_review: number
  jobs_total: number
  jobs_approved: number
  jobs_rejected: number
  jobs_pending_review: number
  jobs_described: number
}

export interface Project {
  id: string
  name: string
  vessel_name: string
  vessel_imo: string
  vessel_type: string
  owner: string
  notes: string
  status: 'active' | 'archived'
  processing_state: ProcessingState
  created_at: string
  updated_at: string
  stats: ProjectStats
}

export interface DocumentStats {
  pages: Partial<Record<PageStatus, number>>
  kinds: Record<string, number>
  ocr_pages: number
  jobs: number
  errors: number
}

export interface DocumentInfo {
  id: string
  project_id: string
  filename: string
  original_path: string
  size_bytes: number
  page_count: number
  title: string
  metadata: Record<string, string>
  content_type: 'digital' | 'scanned' | 'mixed' | 'unknown'
  equipment_name: string
  status: 'imported' | 'processing' | 'ready' | 'error'
  error_message: string
  warnings: string[]
  encrypted: boolean
  text_status: string
  ocr_status: string
  jobs_status: string
  embed_status: string
  imported_at: string
  sha256: string
  stats: DocumentStats
}

export interface PageRow {
  page_no: number
  status: PageStatus
  status_reason: string
  text_source: 'digital' | 'ocr' | 'vlm' | 'none'
  char_count: number
  image_count: number
  ocr_confidence: number | null
  page_kind: string
  kind_scores: Record<string, number>
  job_count: number
  attempts: number
  updated_at: string
}

export interface ProcedureSummary {
  match_id: number
  document_id: string
  document_name: string
  start_page: number
  end_page: number
  selected: boolean
  status: string
  score: number
}

export interface JobRow {
  id: string
  code: string
  seq: number
  project_id: string
  document_id: string
  title: string
  equipment: string
  component: string
  action: string
  frequency_original: string
  frequency_value: number | null
  frequency_unit: string
  frequency_type: FrequencyType
  frequency_type_label: string
  frequency_label: string
  frequency_uncertain: boolean
  schedule_page_no: number
  schedule_document: string
  procedure: ProcedureSummary | null
  extraction_method: string
  extraction_confidence: number
  extraction_status: 'extracted' | 'needs_review'
  procedure_status: ProcedureStatus
  description_status: DescriptionStatus
  review_status: ReviewStatus
  stale: boolean
  edited_fields: string[]
  extra_fields: Record<string, unknown>
  has_short: boolean
  updated_at: string
  attention: string[]
  priority?: number
}

export interface Match {
  id: number
  document_id: string
  document_name: string
  start_page: number
  end_page: number
  start_y: number
  end_y: number
  heading: string
  rank: number
  score: number
  score_breakdown: Record<string, number | string>
  evidence: {
    heading?: string
    boundary?: string[]
    figure_pages?: number[]
    skipped_pages?: number[]
    snippet?: string
    verification?: string[]
    notes?: string[]
    llm_check?: { verdict: string; reason: string; quote: string; grounded: boolean }
    original_bounds?: Record<string, unknown>
    manual?: boolean
  }
  stages: string[]
  status: 'candidate' | 'verified' | 'selected' | 'rejected'
  selected: boolean
  created_by: 'machine' | 'human'
  created_at: string
}

export interface DescriptionBlock {
  type: 'step' | 'safety' | 'tools' | 'materials' | 'table' | 'figure' | 'heading' | 'text' | 'reference'
  text: string
  page: number
  number?: string
  level?: string
  refs?: string[]
  values?: string[]
}

export interface Description {
  id: number
  kind: 'long' | 'short'
  version: number
  content: string
  blocks: DescriptionBlock[]
  method: string
  status: string
  validation: { issues?: string[]; notes?: string[]; warnings?: string[]; pages?: number[]; outdated?: string; source_rejected?: string; line_count?: number }
  created_by: 'machine' | 'human'
  procedure_match_id: number | null
  created_at: string
}

export interface SourceRef {
  id: number
  document_id: string
  document_name: string
  page_no: number
  ref_type: 'schedule' | 'procedure' | 'cross_reference' | 'evidence_image'
  quote: string
  bbox: number[]
  image_path: string
}

export interface JobDetail extends JobRow {
  machine: {
    title: string
    original_title: string
    equipment: string
    component: string
    frequency_original: string
    frequency_value: number | null
    frequency_unit: string
    frequency_type: FrequencyType
    frequency_uncertain: boolean
    frequency_notes: string
    frequency_alternatives: Array<{ original: string; label: string }>
    extra_fields: Record<string, unknown>
  }
  source_text: string
  source_bbox: number[]
  cross_reference: string
  extraction_notes: string[]
  matches: Match[]
  long: Description | null
  short: Description | null
  description_history: Array<{ id: number; kind: string; version: number; method: string; created_by: string; status: string; is_current: boolean; created_at: string }>
  references: SourceRef[]
  decisions: Array<{ id: number; decision: string; payload: Record<string, unknown>; comment: string; reviewer: string; created_at: string }>
  audit: Array<{ id: number; action: string; before: unknown; after: unknown; actor: string; at: string }>
  custom_fields: Array<{ key: string; label: string }>
}

export interface ProcessingRun {
  id: string
  project_id: string
  project_name?: string
  trigger: string
  status: string
  message: string
  tasks_total: number
  tasks_done: number
  tasks_failed: number
  started_at: string
  finished_at: string | null
}

export interface ProcessingStatus {
  state: ProcessingState
  stage: string | null
  pages: { total: number; done: number } & Partial<Record<PageStatus, number>>
  jobs_procedure: Partial<Record<ProcedureStatus, number>>
  jobs_description: Partial<Record<DescriptionStatus, number>>
  queue: Array<{ kind: string; label: string; status: string; count: number }>
  running: Array<{ id: number; kind: string; label: string; message: string; started_at: string; attempts: number }>
  run: ProcessingRun | null
  memory_wait: boolean
}

export interface ProcessingError {
  id: number
  project_id: string
  document_id: string | null
  document_name: string
  page_no: number | null
  category: 'document' | 'application'
  stage: string
  message: string
  detail: string
  created_at: string
}

export interface ProcessingTask {
  id: number
  kind: string
  label: string
  status: string
  attempts: number
  max_attempts: number
  message: string
  error: string
  document_id: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
}

export interface Dashboard {
  projects: number
  documents: number
  pages_total: number
  pages_processed: number
  pages_failed: number
  pages_review: number
  jobs_total: number
  jobs_described: number
  jobs_awaiting_review: number
  jobs_approved: number
  running_tasks: number
  queued_tasks: number
  recent_projects: Project[]
  recent_runs: ProcessingRun[]
  recent_errors: Array<{ id: number; project_id: string; category: string; stage: string; message: string; page_no: number | null; created_at: string }>
}

export interface SystemStatus {
  version: string
  ai: { provider: string; chat_model: string; embedding_model: string; vision_model: string; state: 'disabled' | 'ready' | 'degraded' | 'unavailable'; message: string }
  ocr: { enabled: boolean; engine: string; available: Record<string, boolean> }
  storage: { data_dir: string; app_bytes: number; documents_bytes: number; disk_free_bytes: number; disk_total_bytes: number }
  engine: { memory_wait: boolean; last_error: string }
}

export type Settings = Record<string, unknown> & {
  'processing.concurrency': number
  'processing.batch_size': number
  'processing.max_attempts': number
  'processing.min_free_memory_mb': number
  'processing.max_procedure_pages': number
  'ocr.enabled': boolean
  'ocr.engine': string
  'ocr.min_chars': number
  'ocr.dpi': number
  'ocr.min_confidence': number
  'ai.provider': 'none' | 'ollama' | 'openai_compatible'
  'ai.base_url': string
  'ai.chat_model': string
  'ai.embedding_model': string
  'ai.vision_model': string
  'ai.allow_remote': boolean
  'ai.timeout_s': number
  'ai.use_for_job_extraction': boolean
  'ai.use_for_procedure_verification': boolean
  'ai.use_for_short_descriptions': boolean
  'ai.use_vision_for_unreadable_pages': boolean
  'extraction.custom_fields': Array<{ key: string; label: string }>
  'ui.theme': 'light' | 'dark' | 'system'
  'ui.reviewer_name': string
}

export interface ExportTemplate {
  id: string
  name: string
  description: string
  columns: Array<{ field: string; header: string }>
  description_layout: 'same_sheet' | 'separate_sheets'
  builtin: boolean
}

export interface ExportOptions {
  scope: 'all' | 'approved' | 'selected' | 'unresolved'
  job_ids?: string[]
  template_id?: string
  columns?: Array<{ field: string; header: string }>
  description_layout?: 'same_sheet' | 'separate_sheets'
  include_rejected?: boolean
  unresolved_sheet?: boolean
}

export interface ExportPreview {
  headers: string[]
  rows: unknown[][]
  total_rows: number
  warnings: string[]
  template: string
  layout: string
  unresolved_count: number
}

export interface ExportRecord {
  id: string
  project_id?: string
  project_name?: string
  file_path: string
  template_name: string
  scope: string
  job_count: number
  warnings: string[]
  status: string
  created_at: string
  file_exists: boolean
}

export interface SearchResults {
  query: string
  jobs: Array<{ job: JobRow; matched_fields: string[] }>
  pages: Array<{ document_id: string; document_name: string; page_no: number; page_kind: string; snippet: string; provenance: 'source_text' }>
  descriptions: Array<{ job_id: string; job_code: string; job_title: string; kind: 'long' | 'short'; snippet: string; provenance: 'extracted_source_text' | 'extractive_summary' | 'ai_generated_summary' | 'reviewer_edited'; method: string }>
  error?: string
}

export interface ImportResult {
  results: Array<{ path: string; filename: string; status: 'imported' | 'duplicate' | 'rejected' | 'error'; message?: string; document_id?: string }>
}

export interface PageTextLayer {
  page_no: number
  geometry: { width: number; height: number; rotation: number }
  words: Array<{ t: string; b: [number, number, number, number] }>
  text: string
  headings: Array<{ t: string; y: number }>
  source: string
  status: PageStatus
  status_reason: string
  page_kind: string
}

export interface HardwareInfo {
  hardware: { cpu_count: number; physical_cores: number; ram_total_gb: number; ram_available_gb: number; gpus: Array<{ vendor: string; name: string; vram_mb: number }> }
  models: Array<{ role: string; name: string; approx_download_gb: number; min_ram_gb: number; licence: string; notes: string; fits: boolean; gpu_offload: boolean }>
  suggested: Record<string, string | null>
  disclaimer: string
}
