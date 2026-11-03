from __future__ import annotations

import shutil
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import __version__
from ..ai.providers import AIUnavailable, get_provider
from ..config import get_paths
from ..db.models import Document, Job, JobDescription, Page, ProcessingError, ProcessingRun, ProcessingTask, Project
from ..db.session import get_session
from ..ocr.engines import available_engines
from ..processing.engine import engine
from ..services import settings as settings_service
from ..services.jobs import audit
from .serializers import iso, project_dict, run_dict

router = APIRouter()


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    vessel_name: str = Field(default="", max_length=200)
    vessel_imo: str = Field(default="", max_length=40)
    vessel_type: str = Field(default="", max_length=100)
    owner: str = Field(default="", max_length=200)
    notes: str = Field(default="", max_length=5000)


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    vessel_name: str | None = Field(default=None, max_length=200)
    vessel_imo: str | None = Field(default=None, max_length=40)
    vessel_type: str | None = Field(default=None, max_length=100)
    owner: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=5000)


def project_stats(s: Session, project_id: str) -> dict[str, Any]:
    doc_ids = select(Document.id).where(Document.project_id == project_id)
    pages = dict(s.execute(select(Page.status, func.count()).where(Page.document_id.in_(doc_ids)).group_by(Page.status)).all())
    jobs = dict(s.execute(select(Job.review_status, func.count()).where(Job.project_id == project_id).group_by(Job.review_status)).all())
    described = s.scalar(
        select(func.count(func.distinct(JobDescription.job_id))).join(Job, Job.id == JobDescription.job_id).where(
            Job.project_id == project_id, JobDescription.kind == "long", JobDescription.is_current.is_(True)
        )
    )
    return {
        "documents": s.scalar(select(func.count()).select_from(Document).where(Document.project_id == project_id)) or 0,
        "pages_total": sum(pages.values()),
        "pages_done": pages.get("completed", 0) + pages.get("needs_review", 0) + pages.get("skipped", 0),
        "pages_failed": pages.get("failed", 0),
        "pages_review": pages.get("needs_review", 0),
        "jobs_total": sum(jobs.values()),
        "jobs_approved": jobs.get("approved", 0),
        "jobs_rejected": jobs.get("rejected", 0),
        "jobs_pending_review": jobs.get("pending", 0) + jobs.get("needs_changes", 0),
        "jobs_described": described or 0,
    }


@router.get("/projects")
def list_projects(include_archived: bool = True, s: Session = Depends(get_session)) -> list[dict]:
    q = select(Project).order_by(Project.updated_at.desc())
    if not include_archived:
        q = q.where(Project.status == "active")
    return [project_dict(p, project_stats(s, p.id)) for p in s.scalars(q)]


@router.post("/projects", status_code=201)
def create_project(body: ProjectIn, s: Session = Depends(get_session)) -> dict:
    p = Project(**body.model_dump())
    s.add(p)
    s.flush()
    audit(s, "project", p.id, "created", None, body.model_dump(), "user")
    return project_dict(p, project_stats(s, p.id))


def _get_project(s: Session, project_id: str) -> Project:
    p = s.get(Project, project_id)
    if p is None:
        raise HTTPException(404, "Project not found")
    return p


@router.get("/projects/{project_id}")
def get_project(project_id: str, s: Session = Depends(get_session)) -> dict:
    return project_dict(_get_project(s, project_id), project_stats(s, project_id))


@router.patch("/projects/{project_id}")
def update_project(project_id: str, body: ProjectPatch, s: Session = Depends(get_session)) -> dict:
    p = _get_project(s, project_id)
    changes = body.model_dump(exclude_none=True)
    before = {k: getattr(p, k) for k in changes}
    for k, v in changes.items():
        setattr(p, k, v)
    audit(s, "project", p.id, "updated", before, changes, "user")
    return project_dict(p, project_stats(s, project_id))


@router.post("/projects/{project_id}/archive")
def archive_project(project_id: str, s: Session = Depends(get_session)) -> dict:
    p = _get_project(s, project_id)
    if p.processing_state == "running":
        raise HTTPException(409, "Pause or cancel processing before archiving the project.")
    p.status = "archived"
    audit(s, "project", p.id, "archived", None, None, "user")
    return project_dict(p, project_stats(s, project_id))


@router.post("/projects/{project_id}/restore")
def restore_project(project_id: str, s: Session = Depends(get_session)) -> dict:
    p = _get_project(s, project_id)
    p.status = "active"
    audit(s, "project", p.id, "restored", None, None, "user")
    return project_dict(p, project_stats(s, project_id))


@router.get("/dashboard")
def dashboard(s: Session = Depends(get_session)) -> dict:
    pages = dict(s.execute(select(Page.status, func.count()).group_by(Page.status)).all())
    jobs = dict(s.execute(select(Job.review_status, func.count()).group_by(Job.review_status)).all())
    described = s.scalar(
        select(func.count(func.distinct(JobDescription.job_id))).where(JobDescription.kind == "long", JobDescription.is_current.is_(True))
    )
    recent = s.scalars(select(Project).where(Project.status == "active").order_by(Project.updated_at.desc()).limit(6)).all()
    runs = s.scalars(select(ProcessingRun).order_by(ProcessingRun.started_at.desc()).limit(8)).all()
    names = {p.id: p.name for p in s.scalars(select(Project))}
    return {
        "projects": s.scalar(select(func.count()).select_from(Project)) or 0,
        "documents": s.scalar(select(func.count()).select_from(Document)) or 0,
        "pages_total": sum(pages.values()),
        "pages_processed": pages.get("completed", 0) + pages.get("needs_review", 0) + pages.get("skipped", 0),
        "pages_failed": pages.get("failed", 0),
        "pages_review": pages.get("needs_review", 0),
        "jobs_total": sum(jobs.values()),
        "jobs_described": described or 0,
        "jobs_awaiting_review": jobs.get("pending", 0) + jobs.get("needs_changes", 0),
        "jobs_approved": jobs.get("approved", 0),
        "running_tasks": s.scalar(select(func.count()).select_from(ProcessingTask).where(ProcessingTask.status == "running")) or 0,
        "queued_tasks": s.scalar(select(func.count()).select_from(ProcessingTask).where(ProcessingTask.status == "pending")) or 0,
        "recent_projects": [project_dict(p, project_stats(s, p.id)) for p in recent],
        "recent_runs": [{**run_dict(r), "project_name": names.get(r.project_id, "")} for r in runs],
        "recent_errors": [
            {"id": e.id, "project_id": e.project_id, "category": e.category, "stage": e.stage, "message": e.message,
             "page_no": e.page_no, "created_at": iso(e.created_at)}
            for e in s.scalars(select(ProcessingError).order_by(ProcessingError.id.desc()).limit(5))
        ],  # fmt: skip
    }


def _dir_size(path) -> int:  # noqa: ANN001
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            continue
    return total


_storage_cache: dict[str, Any] = {}


@router.get("/system/status")
def system_status(s: Session = Depends(get_session)) -> dict:
    import time

    cfg = settings_service.get_all(s)
    paths = get_paths()
    if not _storage_cache or time.time() - _storage_cache.get("at", 0) > 60:
        usage = shutil.disk_usage(paths.root)
        _storage_cache.update(
            at=time.time(),
            data={
                "data_dir": str(paths.root),
                "app_bytes": _dir_size(paths.root),
                "documents_bytes": _dir_size(paths.documents),
                "disk_free_bytes": usage.free,
                "disk_total_bytes": usage.total,
            },
        )
    ai: dict[str, Any] = {"provider": cfg["ai.provider"], "chat_model": cfg["ai.chat_model"],
                          "embedding_model": cfg["ai.embedding_model"], "vision_model": cfg["ai.vision_model"]}  # fmt: skip
    if cfg["ai.provider"] == "none":
        ai.update(state="disabled", message="Local AI is off. Rule-based extraction is active.")
    else:
        try:
            provider = get_provider(cfg)
            models = {m["name"] for m in provider.list_models()}
            missing = [m for m in (cfg["ai.chat_model"], cfg["ai.embedding_model"]) if m and m not in models and f"{m}:latest" not in models]
            ai.update(state="degraded" if missing else "ready", message=f"Missing model(s): {', '.join(missing)}" if missing else "Connected")
        except AIUnavailable as exc:
            ai.update(state="unavailable", message=str(exc))
        except Exception as exc:  # noqa: BLE001
            ai.update(state="unavailable", message=f"Inference server error: {exc}")
    return {
        "version": __version__,
        "ai": ai,
        "ocr": {"enabled": cfg["ocr.enabled"], "engine": cfg["ocr.engine"], "available": available_engines()},
        "storage": _storage_cache["data"],
        "engine": {"memory_wait": engine.memory_wait, "last_error": engine.last_error},
    }


@router.delete("/projects/{project_id}")
def delete_project(project_id: str, s: Session = Depends(get_session)) -> dict:
    """Permanently delete a project: its manual copies, pages, jobs, descriptions and history.

    The user's original PDF files are never touched (only the app's own copies are removed).
    """
    from pathlib import Path

    from sqlalchemy import text

    p = _get_project(s, project_id)
    if p.processing_state == "running":
        raise HTTPException(409, "Pause or cancel processing before deleting the project.")
    docs = s.scalars(select(Document).where(Document.project_id == project_id)).all()
    stored = [Path(d.stored_path) for d in docs]
    jobs = s.scalar(select(func.count()).select_from(Job).where(Job.project_id == project_id)) or 0
    s.execute(text("DELETE FROM pages_fts WHERE project_id = :p"), {"p": project_id})
    s.execute(text("DELETE FROM descriptions_fts WHERE project_id = :p"), {"p": project_id})
    audit(s, "project", p.id, "deleted", {"name": p.name, "documents": len(docs), "jobs": jobs}, None, "user")
    s.delete(p)  # documents, pages, jobs, matches, descriptions, tasks and exports cascade
    s.flush()
    library = get_paths().documents
    for f in stored:
        try:
            if f.is_relative_to(library):
                f.unlink(missing_ok=True)
                if f.parent != library and not any(f.parent.iterdir()):
                    f.parent.rmdir()
        except OSError:
            pass  # a locked file is left behind; it no longer belongs to any project
    return {"deleted": True, "documents": len(docs), "jobs": jobs}
