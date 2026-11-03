from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import Document, Job, Page, ProcessingError, ProcessingRun, ProcessingTask, Project
from ..db.session import get_session
from ..processing.engine import engine
from .serializers import iso, run_dict

router = APIRouter()

STAGE_LABELS = {
    "extract_pages": "Extracting page text",
    "embed_pages": "Building semantic index",
    "extract_jobs": "Identifying maintenance jobs",
    "discover_procedures": "Locating procedures",
    "describe_jobs": "Extracting job descriptions",
}


def _project(s: Session, project_id: str) -> Project:
    p = s.get(Project, project_id)
    if p is None:
        raise HTTPException(404, "Project not found")
    return p


@router.post("/projects/{project_id}/processing/start")
def start(project_id: str) -> dict:
    try:
        run_id = engine.start_project(project_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"run_id": run_id}


@router.post("/projects/{project_id}/processing/pause")
def pause(project_id: str) -> dict:
    engine.pause_project(project_id)
    return {"ok": True}


@router.post("/projects/{project_id}/processing/cancel")
def cancel(project_id: str) -> dict:
    engine.cancel_project(project_id)
    return {"ok": True}


@router.post("/projects/{project_id}/processing/retry-failed")
def retry_failed(project_id: str, s: Session = Depends(get_session)) -> dict:
    _project(s, project_id)
    n = engine.retry_failed(project_id)
    return {"reset": n}


@router.get("/projects/{project_id}/processing/status")
def status(project_id: str, s: Session = Depends(get_session)) -> dict:
    p = _project(s, project_id)
    doc_ids = select(Document.id).where(Document.project_id == project_id)
    pages = dict(s.execute(select(Page.status, func.count()).where(Page.document_id.in_(doc_ids)).group_by(Page.status)).all())
    tasks = s.execute(
        select(ProcessingTask.kind, ProcessingTask.status, func.count())
        .where(ProcessingTask.project_id == project_id, ProcessingTask.status.in_(["pending", "running"]))
        .group_by(ProcessingTask.kind, ProcessingTask.status)
    ).all()
    running = s.scalars(select(ProcessingTask).where(ProcessingTask.project_id == project_id, ProcessingTask.status == "running")).all()
    live = engine.running_progress()
    jobs_proc = dict(s.execute(select(Job.procedure_status, func.count()).where(Job.project_id == project_id).group_by(Job.procedure_status)).all())
    jobs_desc = dict(s.execute(select(Job.description_status, func.count()).where(Job.project_id == project_id).group_by(Job.description_status)).all())
    run = s.scalar(select(ProcessingRun).where(ProcessingRun.project_id == project_id).order_by(ProcessingRun.started_at.desc()))
    total = sum(pages.values())
    done = pages.get("completed", 0) + pages.get("needs_review", 0) + pages.get("skipped", 0) + pages.get("failed", 0)
    stage = None
    if running:
        stage = STAGE_LABELS.get(sorted(running, key=lambda t: t.priority)[0].kind)
    elif tasks:
        stage = STAGE_LABELS.get(sorted(tasks, key=lambda t: t[0])[0][0])
    return {
        "state": p.processing_state,
        "stage": stage,
        "pages": {"total": total, "done": done, **pages},
        "jobs_procedure": jobs_proc,
        "jobs_description": jobs_desc,
        "queue": [{"kind": k, "label": STAGE_LABELS.get(k, k), "status": st, "count": n} for k, st, n in tasks],
        "running": [
            {"id": t.id, "kind": t.kind, "label": STAGE_LABELS.get(t.kind, t.kind), "message": live.get(t.id, ""),
             "started_at": iso(t.started_at), "attempts": t.attempts}
            for t in running
        ],  # fmt: skip
        "run": run_dict(run) if run else None,
        "memory_wait": engine.memory_wait,
    }


@router.get("/processing/runs")
def runs(project_id: str | None = None, limit: int = Query(100, le=500), s: Session = Depends(get_session)) -> list[dict]:
    q = select(ProcessingRun).order_by(ProcessingRun.started_at.desc()).limit(limit)
    if project_id:
        q = q.where(ProcessingRun.project_id == project_id)
    names = {p.id: p.name for p in s.scalars(select(Project))}
    return [{**run_dict(r), "project_name": names.get(r.project_id, "")} for r in s.scalars(q)]


@router.get("/processing/tasks")
def tasks(project_id: str, run_id: str | None = None, limit: int = Query(300, le=2000), s: Session = Depends(get_session)) -> list[dict]:
    q = select(ProcessingTask).where(ProcessingTask.project_id == project_id).order_by(ProcessingTask.id.desc()).limit(limit)
    if run_id:
        q = q.where(ProcessingTask.run_id == run_id)
    return [
        {"id": t.id, "kind": t.kind, "label": STAGE_LABELS.get(t.kind, t.kind), "status": t.status,
         "attempts": t.attempts, "max_attempts": t.max_attempts, "message": t.progress_message,
         "error": t.last_error, "document_id": t.document_id, "created_at": iso(t.created_at),
         "started_at": iso(t.started_at), "finished_at": iso(t.finished_at)}
        for t in s.scalars(q)
    ]  # fmt: skip


@router.get("/processing/errors")
def errors(project_id: str | None = None, category: str | None = None, limit: int = Query(300, le=2000), s: Session = Depends(get_session)) -> list[dict]:
    q = select(ProcessingError).order_by(ProcessingError.id.desc()).limit(limit)
    if project_id:
        q = q.where(ProcessingError.project_id == project_id)
    if category:
        q = q.where(ProcessingError.category == category)
    docs = {d.id: d.filename for d in s.scalars(select(Document))}
    return [
        {"id": e.id, "project_id": e.project_id, "document_id": e.document_id, "document_name": docs.get(e.document_id or "", ""),
         "page_no": e.page_no, "category": e.category, "stage": e.stage, "message": e.message, "detail": e.detail,
         "created_at": iso(e.created_at)}
        for e in s.scalars(q)
    ]  # fmt: skip
