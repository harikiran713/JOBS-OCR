"""Persistent processing queue, worker pool and pipeline coordinator.

* Tasks live in SQLite (``processing_tasks``); nothing is held only in memory,
  so a crash or shutdown loses at most the batch in flight.
* On startup, ``running`` tasks and ``processing`` pages are returned to
  ``pending`` – processing resumes where it stopped.
* The coordinator derives the next work from database state (pages pending →
  extract; text complete → jobs; all documents ready → discovery; matches →
  descriptions). It is idempotent and safe to call at any time.
* Concurrency is bounded (``processing.concurrency``); CPU-heavy MuPDF/OCR work
  runs in a process pool because MuPDF is not thread-safe.
* Before claiming work the engine checks free RAM and waits if it is low.
"""

from __future__ import annotations

import logging
import multiprocessing
import threading
import time
import traceback
from concurrent.futures import Future, ProcessPoolExecutor, ThreadPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from datetime import timedelta
from typing import Any

import psutil
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from ..ai.providers import try_provider
from ..db.models import Document, Job, Page, ProcessingError, ProcessingRun, ProcessingTask, Project, utcnow
from ..db.session import session_scope
from ..pdf.inspect import PdfProblem
from ..services import settings as settings_service
from .handlers import HANDLERS, TaskCancelled, TaskContext

log = logging.getLogger(__name__)

ACTIVE = ("pending", "running")
PRIORITY = {"extract_pages": 10, "embed_pages": 20, "extract_jobs": 30, "discover_procedures": 40, "describe_jobs": 50}


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------


def _enqueue(s: Session, project_id: str, kind: str, dedupe: str, payload: dict, document_id: str | None, run_id: str | None, max_attempts: int) -> bool:
    exists = s.scalar(
        select(func.count()).select_from(ProcessingTask).where(ProcessingTask.dedupe_key == dedupe, ProcessingTask.status.in_(ACTIVE))
    )
    if exists:
        return False
    s.add(
        ProcessingTask(
            project_id=project_id, document_id=document_id, run_id=run_id, kind=kind, payload=payload,
            dedupe_key=dedupe, priority=PRIORITY[kind], max_attempts=max_attempts,
        )
    )  # fmt: skip
    return True


def _chunks(items: list, n: int) -> list[list]:
    return [items[i : i + n] for i in range(0, len(items), n)]


def _queued_job_ids(s: Session, project_id: str, kind: str) -> set[str]:
    """Jobs already covered by a queued or running batch (batches must never overlap)."""
    ids: set[str] = set()
    for payload in s.scalars(
        select(ProcessingTask.payload).where(
            ProcessingTask.project_id == project_id, ProcessingTask.kind == kind, ProcessingTask.status.in_(ACTIVE)
        )
    ):
        ids.update((payload or {}).get("job_ids", []))
    return ids


def advance_project(s: Session, project_id: str) -> int:
    """Enqueue whatever work is now possible. Returns number of tasks added."""
    project = s.get(Project, project_id)
    if project is None or project.processing_state != "running":
        return 0
    cfg = settings_service.get_all(s)
    run = s.scalar(
        select(ProcessingRun).where(ProcessingRun.project_id == project_id, ProcessingRun.status == "running").order_by(ProcessingRun.started_at.desc())
    )
    run_id = run.id if run else None
    batch = max(1, int(cfg["processing.batch_size"]))
    max_attempts = int(cfg["processing.max_attempts"])
    embeddings_on = cfg["ai.provider"] != "none" and bool(cfg["ai.embedding_model"])
    added = 0

    docs = s.scalars(select(Document).where(Document.project_id == project_id, Document.status != "error")).all()
    all_ready = True
    for doc in docs:
        counts = dict(
            s.execute(select(Page.status, func.count()).where(Page.document_id == doc.id).group_by(Page.status)).all()
        )
        pending = counts.get("pending", 0) + counts.get("processing", 0)
        if pending:
            all_ready = False
            doc.status = "processing"
            doc.text_status = "partial" if counts.get("completed") or counts.get("needs_review") else "pending"
            pending_nos = s.scalars(
                select(Page.page_no).where(Page.document_id == doc.id, Page.status == "pending").order_by(Page.page_no)
            ).all()
            for chunk in _chunks(list(pending_nos), batch):
                key = f"extract:{doc.id}:{chunk[0]}-{chunk[-1]}"
                added += _enqueue(s, project_id, "extract_pages", key, {"page_nos": chunk}, doc.id, run_id, max_attempts)
            continue

        doc.text_status = "complete"
        ocr_pages = s.scalar(select(func.count()).select_from(Page).where(Page.document_id == doc.id, Page.text_source == "ocr"))
        ocr_problem = s.scalar(
            select(func.count()).select_from(Page).where(Page.document_id == doc.id, Page.status_reason.like("%OCR%"))
        )
        doc.ocr_status = "unavailable" if ocr_problem and not ocr_pages else "complete" if ocr_pages else "not_needed"
        text_pages = counts.get("completed", 0) + counts.get("needs_review", 0)
        total = sum(counts.values()) or 1
        doc.content_type = "scanned" if ocr_pages / total > 0.8 else "mixed" if ocr_pages else "digital"
        if not text_pages:
            doc.jobs_status = "complete"

        if doc.jobs_status != "complete":
            all_ready = False
            added += _enqueue(s, project_id, "extract_jobs", f"jobs:{doc.id}", {}, doc.id, run_id, max_attempts)
        if embeddings_on and doc.embed_status != "complete":
            done_emb = s.scalar(
                select(func.count()).select_from(ProcessingTask).where(
                    ProcessingTask.dedupe_key.like(f"embed:{doc.id}:%"), ProcessingTask.status.in_(ACTIVE)
                )
            )
            if not done_emb and doc.embed_status == "queued":
                doc.embed_status = "complete"
            elif doc.embed_status == "pending":
                ids = s.scalars(select(Page.id).where(Page.document_id == doc.id, Page.char_count > 20).order_by(Page.page_no)).all()
                for chunk in _chunks(list(ids), 64):
                    added += _enqueue(s, project_id, "embed_pages", f"embed:{doc.id}:{chunk[0]}", {"page_ids": chunk}, doc.id, run_id, max_attempts)
                doc.embed_status = "queued"
                all_ready = False
            else:
                all_ready = False
        if doc.jobs_status == "complete" and (not embeddings_on or doc.embed_status == "complete"):
            doc.status = "ready"

    # Cross-manual stages need every document's text and jobs first.
    if all_ready and docs:
        pending_jobs = s.scalars(
            select(Job.id).where(Job.project_id == project_id, Job.procedure_status == "pending", Job.review_status != "rejected").order_by(Job.seq)
        ).all()
        in_flight = _queued_job_ids(s, project_id, "discover_procedures")
        pending_jobs = [j for j in pending_jobs if j not in in_flight]
        for chunk in _chunks(list(pending_jobs), 20):
            added += _enqueue(s, project_id, "discover_procedures", f"discover:{chunk[0]}", {"job_ids": chunk}, None, run_id, max_attempts)
        if not pending_jobs:
            describe = s.scalars(
                select(Job.id).where(
                    Job.project_id == project_id, Job.description_status == "pending", Job.review_status != "rejected",
                    Job.procedure_status.in_(["verified", "needs_confirmation", "confirmed"]),
                ).order_by(Job.seq)
            ).all()  # fmt: skip
            in_flight = _queued_job_ids(s, project_id, "describe_jobs")
            describe = [j for j in describe if j not in in_flight]
            for chunk in _chunks(list(describe), 10):
                added += _enqueue(s, project_id, "describe_jobs", f"describe:{chunk[0]}", {"job_ids": chunk}, None, run_id, max_attempts)

    s.flush()
    active = s.scalar(
        select(func.count()).select_from(ProcessingTask).where(ProcessingTask.project_id == project_id, ProcessingTask.status.in_(ACTIVE))
    )
    if not active:
        project.processing_state = "idle"
        if run is not None:
            errors = s.scalar(
                select(func.count()).select_from(ProcessingError).where(
                    ProcessingError.project_id == project_id, ProcessingError.created_at >= run.started_at
                )
            )
            run.status = "completed_with_errors" if errors else "completed"
            run.finished_at = utcnow()
            run.message = f"Finished with {errors} recorded page/task issue(s)." if errors else "All stages completed."
    if run is not None:
        totals = dict(
            s.execute(
                select(ProcessingTask.status, func.count()).where(ProcessingTask.run_id == run.id).group_by(ProcessingTask.status)
            ).all()
        )
        run.tasks_total = sum(totals.values())
        run.tasks_done = totals.get("completed", 0)
        run.tasks_failed = totals.get("failed", 0)
    return added


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


def _worker_init() -> None:  # pragma: no cover - runs in child processes
    logging.basicConfig(level=logging.WARNING)


class Engine:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._loop_thread: threading.Thread | None = None
        self._threads = ThreadPoolExecutor(max_workers=16, thread_name_prefix="mmi-task")
        self._proc: ProcessPoolExecutor | None = None
        self._proc_size = 0
        self._proc_lock = threading.Lock()
        self._inflight: dict[int, Future] = {}
        self._cancel_projects: set[str] = set()
        self._progress: dict[int, str] = {}
        self.memory_wait = False
        self.last_error: str = ""

    # -- lifecycle -----------------------------------------------------------
    def start(self) -> None:
        self.recover()
        self._loop_thread = threading.Thread(target=self._loop, name="mmi-engine", daemon=True)
        self._loop_thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._loop_thread:
            self._loop_thread.join(timeout=5)
        self._threads.shutdown(wait=False, cancel_futures=True)
        with self._proc_lock:
            if self._proc:
                self._proc.shutdown(wait=False, cancel_futures=True)
                self._proc = None

    def wake(self) -> None:
        self._wake.set()

    def recover(self) -> None:
        """Return interrupted work to the queue after an unclean shutdown."""
        with session_scope() as s:
            n_tasks = s.execute(update(ProcessingTask).where(ProcessingTask.status == "running").values(status="pending")).rowcount
            n_pages = s.execute(update(Page).where(Page.status == "processing").values(status="pending")).rowcount
            for pid in s.scalars(select(Project.id).where(Project.processing_state == "running")):
                advance_project(s, pid)
        if n_tasks or n_pages:
            log.info("Recovered %s interrupted tasks and %s pages", n_tasks, n_pages)

    # -- process pool ----------------------------------------------------------
    def _pool(self, size: int) -> ProcessPoolExecutor:
        with self._proc_lock:
            if self._proc is None or self._proc_size != size:
                if self._proc is not None:
                    self._proc.shutdown(wait=False)
                self._proc = ProcessPoolExecutor(
                    max_workers=size, mp_context=multiprocessing.get_context("spawn"), initializer=_worker_init
                )
                self._proc_size = size
            return self._proc

    def run_cpu(self, size: int, fn, *args):  # noqa: ANN001, ANN201
        for attempt in (1, 2):
            try:
                return self._pool(size).submit(fn, *args).result()
            except BrokenProcessPool:
                # A worker crashed (e.g. a native fault on a malformed page). Rebuild once.
                with self._proc_lock:
                    self._proc = None
                if attempt == 2:
                    raise
        return None

    # -- main loop -------------------------------------------------------------
    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:  # noqa: BLE001 - the loop must never die
                log.exception("Engine tick failed")
            self._wake.wait(timeout=1.0)
            self._wake.clear()

    def _tick(self) -> None:
        self._inflight = {k: f for k, f in self._inflight.items() if not f.done()}
        with session_scope() as s:
            cfg = settings_service.get_all(s)
        concurrency = max(1, min(int(cfg["processing.concurrency"]), 8))
        capacity = concurrency - len(self._inflight)
        if capacity <= 0:
            return
        free_mb = psutil.virtual_memory().available / 1024**2
        if free_mb < int(cfg["processing.min_free_memory_mb"]) and self._inflight:
            self.memory_wait = True
            return
        self.memory_wait = False
        for task_id in self._claim(capacity):
            self._inflight[task_id] = self._threads.submit(self._run_task, task_id, cfg, concurrency)

    def _claim(self, n: int) -> list[int]:
        claimed: list[int] = []
        with session_scope() as s:
            now = utcnow()
            rows = s.scalars(
                select(ProcessingTask.id)
                .join(Project, Project.id == ProcessingTask.project_id)
                .where(
                    ProcessingTask.status == "pending",
                    Project.processing_state == "running",
                    or_(ProcessingTask.not_before.is_(None), ProcessingTask.not_before <= now),
                    ProcessingTask.id.not_in(list(self._inflight) or [-1]),
                )
                .order_by(ProcessingTask.priority, ProcessingTask.id)
                .limit(n)
            ).all()
            for tid in rows:
                ok = s.execute(
                    update(ProcessingTask)
                    .where(and_(ProcessingTask.id == tid, ProcessingTask.status == "pending"))
                    .values(status="running", started_at=now, attempts=ProcessingTask.attempts + 1)
                ).rowcount
                if ok:
                    claimed.append(tid)
        return claimed

    def _run_task(self, task_id: int, cfg: dict[str, Any], concurrency: int) -> None:
        with session_scope() as s:
            t = s.get(ProcessingTask, task_id)
            ctx_data = (t.kind, t.project_id, t.document_id, dict(t.payload or {}), t.attempts, t.max_attempts)
        kind, project_id, document_id, payload, attempts, max_attempts = ctx_data

        def progress(msg: str) -> None:
            self._progress[task_id] = msg

        def should_stop() -> bool:
            if self._stop.is_set() or project_id in self._cancel_projects:
                return True
            with session_scope() as s2:
                state = s2.scalar(select(Project.processing_state).where(Project.id == project_id))
            return state != "running"

        ctx = TaskContext(
            task_id=task_id, project_id=project_id, document_id=document_id, payload=payload, settings=cfg,
            provider=try_provider(cfg), run_cpu=lambda fn, *a: self.run_cpu(concurrency, fn, *a),
            progress=progress, should_stop=should_stop,
        )  # fmt: skip
        status, message, error = "completed", "", ""
        try:
            message = HANDLERS[kind](ctx)
        except TaskCancelled:
            status, message = "pending", "Paused"
        except PdfProblem as exc:
            status, error = "failed", exc.message
            self._log_error(project_id, document_id, task_id, "document", kind, exc.message, "")
        except Exception as exc:  # noqa: BLE001
            tb = traceback.format_exc()
            log.error("Task %s (%s) failed: %s", task_id, kind, exc)
            self.last_error = f"{kind}: {exc}"
            error = f"{type(exc).__name__}: {exc}"
            status = "pending" if attempts < max_attempts else "failed"
            self._log_error(project_id, document_id, task_id, "application", kind, error, tb)
        finally:
            self._progress.pop(task_id, None)
        with session_scope() as s:
            t = s.get(ProcessingTask, task_id)
            if t is not None:
                t.status = status
                t.progress_message = message[:1000]
                t.last_error = error[:2000]
                if status == "pending" and error:
                    t.not_before = utcnow() + timedelta(seconds=min(300, 10 * 2 ** attempts))
                if status in ("completed", "failed"):
                    t.finished_at = utcnow()
                if status == "pending" and not error:
                    t.attempts = max(0, t.attempts - 1)  # pausing is not a failed attempt
                if status == "failed" and kind == "extract_pages" and document_id:
                    s.execute(
                        update(Page)
                        .where(Page.document_id == document_id, Page.page_no.in_(payload.get("page_nos", [])), Page.status == "processing")
                        .values(status="failed", status_reason=error[:500])
                    )
                if status == "failed" and kind == "extract_jobs" and document_id:
                    doc = s.get(Document, document_id)
                    if doc:
                        doc.jobs_status = "complete"
                        doc.warnings = list(doc.warnings or []) + [f"Job extraction failed: {error[:200]}"]
                if status == "failed" and kind in ("discover_procedures", "describe_jobs"):
                    col = Job.procedure_status if kind == "discover_procedures" else Job.description_status
                    s.execute(update(Job).where(Job.id.in_(payload.get("job_ids", [])), col == "pending").values({col.key: "failed"}))
                if status == "pending" and kind == "extract_pages" and document_id:
                    s.execute(
                        update(Page)
                        .where(Page.document_id == document_id, Page.page_no.in_(payload.get("page_nos", [])), Page.status == "processing")
                        .values(status="pending")
                    )
            advance_project(s, project_id)
        self._wake.set()

    def _log_error(self, project_id: str, document_id: str | None, task_id: int, category: str, stage: str, message: str, detail: str) -> None:
        with session_scope() as s:
            s.add(ProcessingError(project_id=project_id, document_id=document_id, task_id=task_id, category=category, stage=stage, message=message[:2000], detail=detail[:8000]))

    # -- controls --------------------------------------------------------------
    def start_project(self, project_id: str, trigger: str = "user") -> str:
        with session_scope() as s:
            project = s.get(Project, project_id)
            if project is None:
                raise LookupError("Project not found")
            if project.status == "archived":
                raise ValueError("Archived projects cannot be processed; restore the project first.")
            self._cancel_projects.discard(project_id)
            run = s.scalar(select(ProcessingRun).where(ProcessingRun.project_id == project_id, ProcessingRun.status.in_(["running", "paused"])))
            if run is None:
                run = ProcessingRun(project_id=project_id, trigger=trigger, status="running")
                s.add(run)
            run.status = "running"
            project.processing_state = "running"
            s.flush()
            s.execute(update(ProcessingTask).where(ProcessingTask.project_id == project_id, ProcessingTask.status.in_(ACTIVE)).values(run_id=run.id))
            advance_project(s, project_id)
            run_id = run.id
        self.wake()
        return run_id

    def pause_project(self, project_id: str) -> None:
        with session_scope() as s:
            project = s.get(Project, project_id)
            if project and project.processing_state == "running":
                project.processing_state = "paused"
                run = s.scalar(select(ProcessingRun).where(ProcessingRun.project_id == project_id, ProcessingRun.status == "running"))
                if run:
                    run.status = "paused"

    def cancel_project(self, project_id: str) -> None:
        self._cancel_projects.add(project_id)
        with session_scope() as s:
            s.execute(
                update(ProcessingTask).where(ProcessingTask.project_id == project_id, ProcessingTask.status == "pending").values(status="cancelled", finished_at=utcnow())
            )
            project = s.get(Project, project_id)
            if project:
                project.processing_state = "idle"
            for run in s.scalars(select(ProcessingRun).where(ProcessingRun.project_id == project_id, ProcessingRun.status.in_(["running", "paused"]))):
                run.status = "cancelled"
                run.finished_at = utcnow()
                run.message = "Cancelled by user. Completed work is kept; start again to continue."

    def retry_failed(self, project_id: str) -> int:
        with session_scope() as s:
            doc_ids = select(Document.id).where(Document.project_id == project_id)
            n = s.execute(update(Page).where(Page.document_id.in_(doc_ids), Page.status == "failed").values(status="pending", attempts=0)).rowcount
            n += s.execute(update(Job).where(Job.project_id == project_id, Job.procedure_status == "failed").values(procedure_status="pending")).rowcount
            n += s.execute(update(Job).where(Job.project_id == project_id, Job.description_status == "failed").values(description_status="pending")).rowcount
        return n

    def running_progress(self) -> dict[int, str]:
        return dict(self._progress)


engine = Engine()
