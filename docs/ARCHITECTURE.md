# Architecture

## Process model

```
┌───────────────────────────── Electron ─────────────────────────────┐
│ Main process (src/main)                                            │
│  • starts/stops the backend, random per-launch API token           │
│  • owns all file dialogs and path validation (import / export)     │
│  • narrow IPC handlers only                                        │
│                                                                    │
│ Renderer (src/renderer) — sandboxed, contextIsolation, no Node     │
│  • React UI; talks to the backend over HTTP with the token         │
│  • preload exposes ~12 specific functions (window.mmi)             │
└────────────────────────────────┬───────────────────────────────────┘
                                 │ http://127.0.0.1:<random port>  (Bearer token)
┌────────────────────────────────▼───────────────────────────────────┐
│ Backend (backend/mmi) — FastAPI + uvicorn, single process          │
│  • API routes (api/*)                                              │
│  • Engine thread: claims tasks from SQLite, bounded concurrency    │
│  • Process pool (spawn): MuPDF + OCR work (MuPDF isn't thread-safe)│
│  • SQLite (WAL) + FTS5; PDFs and caches on disk                    │
└────────────────────────────────────────────────────────────────────┘
```

The backend binds only to loopback on an OS-assigned port, announces `MMI_READY port=N` on stdout, reads its token from the environment (not argv), and exits if the Electron process disappears (`--parent-pid` watchdog).

## Backend modules (independent, with typed interfaces)

| Module | Responsibility |
|---|---|
| `pdf/` | Validation, streamed copy + SHA-256, inspection, per-page extraction (worker side), table cells from positioned words, rendering cache |
| `ocr/` | `OcrEngine` protocol; RapidOCR (default) and PaddleOCR adapters |
| `extraction/` | `layout` (headings, rows), `classify` (page types from structure), `frequency` (interval parser), `jobs` (table / matrix / line strategies) |
| `retrieval/` | `store` (pages, FTS5, embeddings), `text_sim`, `boundaries`, `discovery` (6-stage pipeline) |
| `descriptions/` | `long` (verbatim structured extraction), `short` (extractive or validated LLM) |
| `ai/` | Provider abstraction (Ollama, OpenAI-compatible), grounded task prompts, hardware + model suggestions |
| `processing/` | Persistent queue, engine, coordinator, stage handlers |
| `services/` | Settings, document import/removal, job persistence + corrections + audit |
| `export/` | Excel writer (write-only mode), templates |
| `api/` | Routes and serializers (contract mirrored in `desktop/src/renderer/src/lib/types.ts`) |

## Pipeline

1. **Import**: the file is validated, stream-copied into the managed library while being hashed, and inspected. A row is created for every page with status `pending`.
2. **extract_pages**: runs in batches in worker processes. Digital text and layout are extracted first. When a page has too little text, or its text is garbled, the page is OCR'd instead. Then headings are detected and the page is classified. Results are cached by (document hash, page, extractor + OCR options).
3. **extract_jobs**: runs per document, on pages that look like schedules. Tables are parsed with column roles inferred from both headers and cell statistics, along with interval matrices and line or bullet layouts. When an LLM is configured, it may add jobs, but only ones whose quotes are found on the page.
4. **embed_pages**: optional, and only runs when an embedding model is set.
5. **discover_procedures**: runs only after *every* document in the project is ready, so matches can cross manuals.
6. **describe_jobs**: the long description is copied from the procedure's pages, then the short description is selected from it. Both are copy-only: no words or punctuation are added, nothing is paraphrased, and no model writes text. Every word of the long description must be found on its source pages and every short-description line must appear word for word in the long description; anything that fails the check is removed and reported.

The **coordinator** (`advance_project`) derives the next work from database state after every task. That is why resuming after a crash, pause, or cancel needs no special code: interrupted `running` tasks and `processing` pages are reset to `pending` at startup.

**Error classes:** a `document` error means a page or file is unreadable and is recorded without stopping the project; an `application` error is an exception, retried with backoff up to the configured limit.

## Procedure discovery and verification

The candidate score combines how well each heading matches the job (component or object, action family, and title coverage), text coverage, equipment compatibility, the page's procedure structure, BM25 rank, exact phrase hits, and semantic similarity. A schedule cross-reference such as `909-1.2` that matches a heading adds a strong boost.

A match is **verified automatically** only in two cases. Either a cross-reference matches the heading and the heading names the component. Or all of the following hold: the heading names the same object and action, the page is procedure-like, and its score leads the next candidate by a clear margin. A grounded LLM "match" can also verify, but only on top of a moderate rule score. In every other case the candidates stay `needs_confirmation`, and descriptions extracted from them are marked *provisional*. When no candidate heading names the job's object, the status is `not_found`, but the nearest candidates are still kept so a reviewer can pick one manually.

Boundary detection runs from the anchor heading until it reaches a heading of the same or higher rank. Continuing onto further pages requires positive evidence: a "continued" marker, step numbering that continues, procedure-like content, or a figure page. Procedures interrupted by unrelated pages are rejoined when the step numbering resumes. A configurable maximum length prevents runaway ranges.

## Data model and review safety

- `jobs` holds the machine values and is never overwritten by reviewers. Human corrections go in `job_edits` (one row per field), and the effective value is computed on read. Every change is written to `audit_log`.
- `job_descriptions` is versioned (`version`, `is_current`, `created_by`). Edits and regenerations add new versions.
- Re-extraction upserts jobs by a stable `job_key` (document hash, page, normalised title, interval, component). A job a reviewer has touched is never deleted; if extraction no longer finds it, it is flagged `stale` instead.
- When a reviewer selects or adjusts a procedure, descriptions are regenerated automatically only if they were machine-generated. Human-edited text is kept and flagged as possibly outdated.
- Export writes procedure source pages only for `verified` or `confirmed` matches.

## Schema migrations

A fresh database is created from the current models and stamped with `SCHEMA_VERSION`. An existing database is first copied to `backups/`, then upgraded by applying each numbered migration in `db/session.py::MIGRATIONS` in order. A database newer than the application is refused, and its data is left untouched.

## Security

- Renderer: `sandbox`, `contextIsolation`, no `nodeIntegration`, a strict CSP in production, navigation and new windows blocked, all permission requests denied.
- The renderer has no filesystem access. Import and export paths come from native dialogs or dropped files in the main process, which validates them (`.pdf` that exists, or an `.xlsx` the app wrote) before calling the backend.
- The backend accepts loopback connections only and requires the per-launch token, compared in constant time. The token can be passed as a query parameter only on GET requests, so that `<img>` page renders work.
- AI endpoints are restricted to loopback unless `ai.allow_remote` is enabled.
- Text written to Excel cells that starts with `= + - @` gets a leading apostrophe, so it is never evaluated as a formula.
