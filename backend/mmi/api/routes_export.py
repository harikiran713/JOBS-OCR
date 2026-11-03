from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import Document, ExportRecord, ExportTemplate, Job, JobDescription, ProcedureMatch, Project
from ..db.session import get_session
from ..export.excel import BUILTIN_TEMPLATES, FIELDS, build_preview, write_workbook
from ..services import settings as settings_service
from ..services.jobs import audit
from .routes_jobs import build_rows
from .serializers import iso

router = APIRouter()

Scope = Literal["all", "approved", "selected", "unresolved"]


class Column(BaseModel):
    field: str = Field(min_length=1, max_length=80)
    header: str = Field(min_length=1, max_length=120)


class ExportOptions(BaseModel):
    scope: Scope = "approved"
    job_ids: list[str] = Field(default_factory=list, max_length=100_000)
    template_id: str | None = None
    columns: list[Column] | None = None
    description_layout: Literal["same_sheet", "separate_sheets"] | None = None
    include_rejected: bool = False
    unresolved_sheet: bool = True


class ExportRun(ExportOptions):
    file_path: str = Field(min_length=5, max_length=1000)


def _templates(s: Session) -> list[dict]:
    custom = [
        {"id": t.id, "name": t.name, "description": t.description, "columns": t.columns,
         "description_layout": t.description_layout, "builtin": False}
        for t in s.scalars(select(ExportTemplate).order_by(ExportTemplate.name))
    ]  # fmt: skip
    return [{**t, "builtin": True} for t in BUILTIN_TEMPLATES] + custom


def _resolve_template(s: Session, opts: ExportOptions) -> tuple[str, list[dict], str]:
    tpl = next((t for t in _templates(s) if t["id"] == (opts.template_id or "builtin-standard")), None)
    if tpl is None:
        raise HTTPException(404, "Export template not found")
    columns = [c.model_dump() for c in opts.columns] if opts.columns else tpl["columns"]
    custom_keys = {f["key"] for f in settings_service.get(s, "extraction.custom_fields") or [] if isinstance(f, dict)}
    for c in columns:
        f = c["field"]
        if f not in FIELDS and not (f.startswith("extra.") and f[6:] in custom_keys):
            raise HTTPException(422, f"Unknown export field '{f}'")
    return tpl["name"], columns, opts.description_layout or tpl["description_layout"]


def _rows(s: Session, project_id: str, opts: ExportOptions) -> tuple[list[dict], list[dict]]:
    rows = build_rows(s, project_id)
    if not opts.include_rejected:
        rows = [r for r in rows if r["review_status"] != "rejected"]
    unresolved = [r for r in rows if r["review_status"] != "approved"]
    if opts.scope == "approved":
        rows = [r for r in rows if r["review_status"] == "approved"]
    elif opts.scope == "selected":
        wanted = set(opts.job_ids)
        rows = [r for r in rows if r["id"] in wanted]
    elif opts.scope == "unresolved":
        rows = unresolved
    ids = [r["id"] for r in rows]
    jobs = {j.id: j for j in s.scalars(select(Job).where(Job.id.in_(ids)))} if ids else {}
    descs = {}
    if ids:
        for d in s.scalars(select(JobDescription).where(JobDescription.job_id.in_(ids), JobDescription.is_current.is_(True))):
            descs[(d.job_id, d.kind)] = d
    docs = {d.id: d.filename for d in s.scalars(select(Document).where(Document.project_id == project_id))}
    for r in rows:
        j = jobs[r["id"]]
        r["original_title"] = j.original_title
        long_d, short_d = descs.get((r["id"], "long")), descs.get((r["id"], "short"))
        r["long"] = long_d.content if long_d else ""
        r["short"] = short_d.content if short_d else ""
        r["short_method"] = short_d.method if short_d else ""
        # Only confirmed/verified procedures are exported as sources — never a guess.
        m = s.scalar(select(ProcedureMatch).where(ProcedureMatch.job_id == j.id, ProcedureMatch.selected.is_(True)))
        if m is not None and r["procedure_status"] in ("verified", "confirmed"):
            r["proc_doc"], r["proc_start"], r["proc_end"] = docs.get(m.document_id, ""), m.start_page, m.end_page
        else:
            r["proc_doc"], r["proc_start"], r["proc_end"] = "", None, None
        r["verification"] = {
            "verified": "Procedure verified automatically", "confirmed": "Procedure confirmed by reviewer",
            "needs_confirmation": "Procedure not confirmed", "not_found": "Procedure not found",
        }.get(r["procedure_status"], r["procedure_status"]) + f" · Job {r['review_status']}"  # fmt: skip
    return rows, unresolved


@router.get("/export/fields")
def export_fields(s: Session = Depends(get_session)) -> list[dict]:
    custom = settings_service.get(s, "extraction.custom_fields") or []
    return [{"field": k, "label": v[0]} for k, v in FIELDS.items()] + [
        {"field": f"extra.{f['key']}", "label": f.get("label", f["key"])} for f in custom if isinstance(f, dict) and "key" in f
    ]


@router.get("/export/templates")
def list_templates(s: Session = Depends(get_session)) -> list[dict]:
    return _templates(s)


class TemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    columns: list[Column] = Field(min_length=1, max_length=200)
    description_layout: Literal["same_sheet", "separate_sheets"] = "same_sheet"


@router.post("/export/templates", status_code=201)
def create_template(body: TemplateIn, s: Session = Depends(get_session)) -> dict:
    t = ExportTemplate(name=body.name, description=body.description, columns=[c.model_dump() for c in body.columns], description_layout=body.description_layout)
    s.add(t)
    s.flush()
    return {"id": t.id}


@router.put("/export/templates/{template_id}")
def update_template(template_id: str, body: TemplateIn, s: Session = Depends(get_session)) -> dict:
    t = s.get(ExportTemplate, template_id)
    if t is None:
        raise HTTPException(404, "Template not found (built-in templates cannot be modified; save a copy instead)")
    t.name, t.description, t.columns, t.description_layout = body.name, body.description, [c.model_dump() for c in body.columns], body.description_layout
    return {"id": t.id}


@router.delete("/export/templates/{template_id}")
def delete_template(template_id: str, s: Session = Depends(get_session)) -> dict:
    t = s.get(ExportTemplate, template_id)
    if t is None:
        raise HTTPException(404, "Template not found")
    s.delete(t)
    return {"ok": True}


@router.post("/projects/{project_id}/export/preview")
def preview(project_id: str, body: ExportOptions, s: Session = Depends(get_session)) -> dict:
    if s.get(Project, project_id) is None:
        raise HTTPException(404, "Project not found")
    name, columns, layout = _resolve_template(s, body)
    rows, unresolved = _rows(s, project_id, body)
    p = build_preview(rows, columns, layout)
    return {**p, "template": name, "layout": layout, "unresolved_count": len(unresolved)}


@router.post("/projects/{project_id}/export")
def run_export(project_id: str, body: ExportRun, s: Session = Depends(get_session)) -> dict:
    project = s.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    path = Path(body.file_path)
    if path.suffix.lower() != ".xlsx" or not path.is_absolute():
        raise HTTPException(422, "Export path must be an absolute .xlsx path chosen in the save dialog")
    if not path.parent.exists():
        raise HTTPException(422, "Destination folder does not exist")
    name, columns, layout = _resolve_template(s, body)
    rows, unresolved = _rows(s, project_id, body)
    if not rows:
        raise HTTPException(409, "No jobs match the selected export scope.")
    meta = {"project": project.name, "vessel": project.vessel_name, "scope": body.scope, "template": name}
    try:
        result = write_workbook(path, rows, columns, layout, meta, unresolved if body.unresolved_sheet and body.scope != "unresolved" else None)
    except PermissionError as exc:
        raise HTTPException(409, "The file is open in another program or the folder is not writable.") from exc
    rec = ExportRecord(
        project_id=project_id, file_path=str(path), template_name=name, scope=body.scope, job_count=result.rows,
        options=body.model_dump(exclude={"file_path"}), warnings=result.warnings[:500],
        status="completed_with_warnings" if result.warnings else "completed",
    )  # fmt: skip
    s.add(rec)
    audit(s, "project", project_id, "exported", None, {"path": str(path), "jobs": result.rows, "scope": body.scope}, "user")
    return {"id": rec.id, "path": result.path, "rows": result.rows, "sheets": result.sheets, "warnings": result.warnings[:200]}


@router.get("/projects/{project_id}/exports")
def export_history(project_id: str, s: Session = Depends(get_session)) -> list[dict]:
    return [
        {"id": e.id, "file_path": e.file_path, "template_name": e.template_name, "scope": e.scope, "job_count": e.job_count,
         "warnings": e.warnings, "status": e.status, "created_at": iso(e.created_at), "file_exists": Path(e.file_path).exists()}
        for e in s.scalars(select(ExportRecord).where(ExportRecord.project_id == project_id).order_by(ExportRecord.created_at.desc()))
    ]  # fmt: skip


@router.get("/exports")
def all_exports(s: Session = Depends(get_session)) -> list[dict]:
    names = {p.id: p.name for p in s.scalars(select(Project))}
    return [
        {"id": e.id, "project_id": e.project_id, "project_name": names.get(e.project_id, ""), "file_path": e.file_path,
         "template_name": e.template_name, "scope": e.scope, "job_count": e.job_count, "warnings": e.warnings,
         "status": e.status, "created_at": iso(e.created_at), "file_exists": Path(e.file_path).exists()}
        for e in s.scalars(select(ExportRecord).order_by(ExportRecord.created_at.desc()).limit(500))
    ]  # fmt: skip


class PmsExportIn(BaseModel):
    template: Literal["ocr", "capital"]
    description: Literal["long", "short"]
    file_path: str = Field(min_length=5, max_length=1000)
    document_id: str | None = None  # only jobs extracted from this document; None = whole project


@router.post("/projects/{project_id}/export/pms")
def export_pms(project_id: str, body: PmsExportIn, s: Session = Depends(get_session)) -> dict:
    """OCR Template (1 sheet) or Capital Template (works / tasks / Documents)."""
    from ..export.pms_templates import write_capital_template, write_ocr_template

    project = s.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    path = Path(body.file_path)
    if path.suffix.lower() != ".xlsx" or not path.is_absolute() or not path.parent.exists():
        raise HTTPException(422, "Choose an .xlsx file in an existing folder")
    rows = [r for r in build_rows(s, project_id) if r["review_status"] != "rejected"]
    if body.document_id:
        rows = [r for r in rows if r["document_id"] == body.document_id]
    if not rows:
        raise HTTPException(409, "There are no jobs to export.")
    ids = [r["id"] for r in rows]
    descs = {(d.job_id, d.kind): d for d in s.scalars(select(JobDescription).where(JobDescription.job_id.in_(ids), JobDescription.is_current.is_(True)))}
    matches = {m.id: m for m in s.scalars(select(ProcedureMatch).where(ProcedureMatch.job_id.in_(ids)))}
    selected = {m.job_id: m for m in matches.values() if m.selected}
    docs = {d.id: d.filename for d in s.scalars(select(Document).where(Document.project_id == project_id))}
    jobs = []
    for r in rows:
        long_d, short_d = descs.get((r["id"], "long")), descs.get((r["id"], "short"))
        # Pages of the procedure the description was taken from (so text and page reference agree).
        m = matches.get(long_d.procedure_match_id) if long_d and long_d.procedure_match_id else selected.get(r["id"])
        pages = "" if m is None else (str(m.start_page) if m.start_page == m.end_page else f"{m.start_page}-{m.end_page}")
        manual = docs.get(m.document_id if m else r["document_id"], "")
        jobs.append({**r, "long": long_d.content if long_d else "", "short": short_d.content if short_d else "",
                     "pages": pages, "manual_name": Path(manual).stem})  # fmt: skip
    try:
        n = (write_ocr_template if body.template == "ocr" else write_capital_template)(path, jobs, body.description)
    except PermissionError as exc:
        raise HTTPException(409, "The file is open in another program or the folder is not writable.") from exc
    name = "OCR Template" if body.template == "ocr" else "Capital Template"
    rec = ExportRecord(project_id=project_id, file_path=str(path), template_name=f"{name} ({'Long JD' if body.description == 'long' else 'Short Summary'})",
                       scope="document" if body.document_id else "all", job_count=n, options=body.model_dump(exclude={"file_path"}), status="completed")  # fmt: skip
    s.add(rec)
    audit(s, "project", project_id, "exported", None, {"path": str(path), "jobs": n, "template": name}, "user")
    return {"id": rec.id, "path": str(path), "rows": n, "warnings": []}
