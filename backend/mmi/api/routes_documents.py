from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from ..db.models import Document, Job, Page, PageText, ProcessingError, Project
from ..db.session import get_session
from ..pdf.inspect import PdfProblem
from ..pdf.render import page_geometry, page_words, render_page, search_page
from ..services.documents import import_documents, remove_document
from ..services.jobs import audit, reset_for_rediscovery
from .serializers import document_dict, iso

router = APIRouter()


class ImportIn(BaseModel):
    paths: list[str] = Field(min_length=1, max_length=500)


class DocumentPatch(BaseModel):
    equipment_name: str = Field(max_length=300)


def _doc(s: Session, document_id: str) -> Document:
    d = s.get(Document, document_id)
    if d is None:
        raise HTTPException(404, "Document not found")
    return d


def doc_stats(s: Session, doc_id: str) -> dict:
    pages = dict(s.execute(select(Page.status, func.count()).where(Page.document_id == doc_id).group_by(Page.status)).all())
    kinds = dict(s.execute(select(Page.page_kind, func.count()).where(Page.document_id == doc_id).group_by(Page.page_kind)).all())
    ocr = s.scalar(select(func.count()).select_from(Page).where(Page.document_id == doc_id, Page.text_source == "ocr")) or 0
    return {
        "pages": pages,
        "kinds": kinds,
        "ocr_pages": ocr,
        "jobs": s.scalar(select(func.count()).select_from(Job).where(Job.document_id == doc_id)) or 0,
        "errors": s.scalar(select(func.count()).select_from(ProcessingError).where(ProcessingError.document_id == doc_id)) or 0,
    }


@router.get("/projects/{project_id}/documents")
def list_documents(project_id: str, s: Session = Depends(get_session)) -> list[dict]:
    docs = s.scalars(select(Document).where(Document.project_id == project_id).order_by(Document.imported_at)).all()
    return [document_dict(d, doc_stats(s, d.id)) for d in docs]


@router.post("/projects/{project_id}/documents/import")
def import_docs(project_id: str, body: ImportIn, s: Session = Depends(get_session)) -> dict:
    if s.get(Project, project_id) is None:
        raise HTTPException(404, "Project not found")
    results = import_documents(s, project_id, body.paths)
    if any(r["status"] == "imported" for r in results):
        # Schedule rows dropped earlier for lack of a procedure get another chance against the new manual.
        new_ids = {r.get("document_id") for r in results}
        for d in s.scalars(select(Document).where(Document.project_id == project_id, Document.id.not_in(new_ids), Document.status != "error")):
            d.jobs_status = "pending"
        # New manuals may contain procedures for jobs that were not found (or were ambiguous) before.
        for job in s.scalars(
            select(Job).where(Job.project_id == project_id, Job.review_status == "pending",
                              Job.procedure_status.in_(["not_found", "needs_confirmation"]))  # fmt: skip
        ):
            reset_for_rediscovery(s, job)
    return {"results": results}


@router.get("/documents/{document_id}")
def get_document(document_id: str, s: Session = Depends(get_session)) -> dict:
    d = _doc(s, document_id)
    return document_dict(d, doc_stats(s, d.id))


@router.patch("/documents/{document_id}")
def patch_document(document_id: str, body: DocumentPatch, s: Session = Depends(get_session)) -> dict:
    d = _doc(s, document_id)
    audit(s, "document", d.id, "equipment_renamed", d.equipment_name, body.equipment_name, "user")
    d.equipment_name = body.equipment_name.strip()
    return document_dict(d, doc_stats(s, d.id))


@router.delete("/documents/{document_id}")
def delete_document(document_id: str, s: Session = Depends(get_session)) -> dict:
    d = _doc(s, document_id)
    project = s.get(Project, d.project_id)
    if project and project.processing_state == "running":
        raise HTTPException(409, "Pause or cancel processing before removing a document.")
    return remove_document(s, document_id)


@router.get("/documents/{document_id}/pages")
def list_pages(
    document_id: str,
    status: str | None = None,
    kind: str | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=1000),
    s: Session = Depends(get_session),
) -> dict:
    _doc(s, document_id)
    q = select(Page).where(Page.document_id == document_id)
    if status:
        q = q.where(Page.status == status)
    if kind:
        q = q.where(Page.page_kind == kind)
    total = s.scalar(select(func.count()).select_from(q.subquery()))
    rows = s.scalars(q.order_by(Page.page_no).offset(offset).limit(limit)).all()
    return {
        "total": total,
        "items": [
            {"page_no": p.page_no, "status": p.status, "status_reason": p.status_reason, "text_source": p.text_source,
             "char_count": p.char_count, "image_count": p.image_count, "ocr_confidence": p.ocr_confidence,
             "page_kind": p.page_kind, "kind_scores": p.kind_scores, "job_count": p.job_count,
             "attempts": p.attempts, "updated_at": iso(p.updated_at)}
            for p in rows
        ],  # fmt: skip
    }


@router.post("/documents/{document_id}/pages/{page_no}/reprocess")
def reprocess_page(document_id: str, page_no: int, s: Session = Depends(get_session)) -> dict:
    d = _doc(s, document_id)
    n = s.execute(update(Page).where(Page.document_id == d.id, Page.page_no == page_no).values(status="pending", attempts=0)).rowcount
    if not n:
        raise HTTPException(404, "Page not found")
    d.jobs_status = "pending"
    return {"ok": True}


@router.post("/documents/{document_id}/reprocess")
def reprocess_document(document_id: str, s: Session = Depends(get_session)) -> dict:
    """Re-extract every page and re-identify jobs with the current extraction logic.

    Jobs found again keep their identity; reviewed or edited jobs are never deleted
    (they are flagged stale if no longer found). Unreviewed jobs that are no longer
    found are replaced by the new extraction.
    """
    d = _doc(s, document_id)
    project = s.get(Project, d.project_id)
    if project and project.processing_state == "running":
        raise HTTPException(409, "Pause or cancel processing before re-processing a document.")
    if d.status == "error":
        raise HTTPException(422, d.error_message or "Document cannot be processed")
    n = s.execute(update(Page).where(Page.document_id == d.id).values(status="pending", attempts=0)).rowcount
    d.jobs_status = d.embed_status = "pending"
    d.text_status = "pending"
    audit(s, "document", d.id, "reprocess_requested", None, {"pages": n}, "user")
    return {"pages": n}


@router.get("/documents/{document_id}/pages/{page_no}/image")
def page_image(document_id: str, page_no: int, scale: float = Query(1.5, gt=0, le=3), s: Session = Depends(get_session)) -> FileResponse:
    d = _doc(s, document_id)
    if d.status == "error":
        raise HTTPException(422, d.error_message or "Document cannot be rendered")
    try:
        path = render_page(d.stored_path, d.sha256, page_no, scale)
    except IndexError as exc:
        raise HTTPException(404, str(exc)) from exc
    except PdfProblem as exc:
        raise HTTPException(422, exc.message) from exc
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "private, max-age=86400"})


@router.get("/documents/{document_id}/pages/{page_no}/text")
def page_text(document_id: str, page_no: int, s: Session = Depends(get_session)) -> dict:
    d = _doc(s, document_id)
    page = s.scalar(select(Page).where(Page.document_id == d.id, Page.page_no == page_no))
    if page is None:
        raise HTTPException(404, "Page not found")
    pt = s.get(PageText, page.id)
    try:
        geom = page_geometry(d.stored_path, page_no)
        words = page_words(d.stored_path, page_no) if page.text_source != "ocr" else []
    except PdfProblem as exc:
        raise HTTPException(422, exc.message) from exc
    if page.text_source == "ocr" and pt is not None:
        words = [{"t": ln["t"], "b": ln["b"]} for ln in pt.lines]
    return {
        "page_no": page_no, "geometry": geom, "words": words, "text": pt.text if pt else "",
        "headings": pt.headings if pt else [], "source": page.text_source, "status": page.status,
        "status_reason": page.status_reason, "page_kind": page.page_kind,
    }  # fmt: skip


@router.get("/documents/{document_id}/search")
def search_document(document_id: str, q: str = Query(min_length=2, max_length=200), s: Session = Depends(get_session)) -> dict:
    d = _doc(s, document_id)
    terms = " ".join(f'"{t}"' for t in q.replace('"', " ").split() if t)
    rows = s.execute(
        text(
            "SELECT p.page_no, snippet(pages_fts, 0, '[[', ']]', '…', 14) FROM pages_fts "
            "JOIN pages p ON p.id = pages_fts.page_id WHERE pages_fts MATCH :q AND pages_fts.document_id = :d "
            "ORDER BY p.page_no LIMIT 500"
        ),
        {"q": terms, "d": d.id},
    ).all()
    return {"query": q, "hits": [{"page_no": r[0], "snippet": r[1]} for r in rows]}


@router.get("/documents/{document_id}/pages/{page_no}/highlights")
def page_highlights(document_id: str, page_no: int, q: str = Query(min_length=1, max_length=200), s: Session = Depends(get_session)) -> dict:
    d = _doc(s, document_id)
    page = s.scalar(select(Page).where(Page.document_id == d.id, Page.page_no == page_no))
    if page is None:
        raise HTTPException(404, "Page not found")
    if page.text_source == "ocr":
        pt = s.get(PageText, page.id)
        rects = [ln["b"] for ln in (pt.lines if pt else []) if q.lower() in ln["t"].lower()]
    else:
        rects = search_page(d.stored_path, page_no, q)
    return {"rects": rects}
