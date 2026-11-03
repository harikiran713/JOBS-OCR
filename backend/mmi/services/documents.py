"""Document import and removal.

Imported PDFs are stream-copied into the managed library (originals are
never touched) and hashed; the page registry is created immediately so every
page has an explicit processing state from the start.
"""

from __future__ import annotations

import logging
import re
import uuid
from pathlib import Path

from sqlalchemy import delete, insert, select, text
from sqlalchemy.orm import Session

from ..config import get_paths
from ..db.models import Document, Job, Page, Project
from ..pdf.inspect import PdfProblem, copy_and_hash, inspect_pdf, validate_pdf_path
from .jobs import audit

log = logging.getLogger(__name__)

_GENERIC = re.compile(
    r"\b(?:instruction|operating|operation|maintenance|service|technical|user|owner'?s?|manual|handbook|book|guide|"
    r"and|&|for|the|volume|vol\.?|part|rev\.?\s*\w*|edition|final|draft|pdf|copy)\b",
    re.I,
)


def guess_equipment(title: str, filename: str) -> str:
    for source in (title, Path(filename).stem):
        if re.fullmatch(r"[0-9a-f-]{20,}", (source or "").strip(), re.I):
            continue  # PDF titles are sometimes just a UUID
        s = re.sub(r"[_]+", " ", source or "")
        s = re.sub(r"^(?:\d+[-.]?)+\.?\s+(?=\S)", "", s)  # leading numbering such as "1-1.1. "
        s = re.sub(r"[(（][^)）]*[)）]", " ", s)  # parentheticals such as "(PART I, II)" or "(1)"
        s = _GENERIC.sub(" ", s)
        s = re.sub(r"\s+", " ", s).strip(" -–—:,.")
        if len(re.findall(r"[A-Za-z]", s)) >= 4:
            return s[:200]
    return ""


def import_documents(session: Session, project_id: str, paths: list[str]) -> list[dict]:
    project = session.get(Project, project_id)
    if project is None:
        raise LookupError("Project not found")
    results = []
    library = get_paths().documents / project_id[:8]
    for raw in paths:
        src = Path(raw)
        entry: dict = {"path": raw, "filename": src.name}
        try:
            validate_pdf_path(src)
        except PdfProblem as exc:
            entry.update(status="rejected", code=exc.code, message=exc.message)
            results.append(entry)
            continue
        # Short names keep paths well below the Windows 260-character limit.
        dest = library / f"{uuid.uuid4().hex[:16]}.pdf"
        try:
            sha, size = copy_and_hash(src, dest)
        except OSError as exc:
            entry.update(status="rejected", code="io_error", message=f"Could not copy file: {exc}")
            results.append(entry)
            continue
        dup = session.scalar(select(Document).where(Document.project_id == project_id, Document.sha256 == sha))
        if dup is not None:
            dest.unlink(missing_ok=True)
            entry.update(status="duplicate", document_id=dup.id, message=f"Already imported as {dup.filename}")
            results.append(entry)
            continue
        doc = Document(
            project_id=project_id, filename=src.name, original_path=str(src), stored_path=str(dest),
            sha256=sha, size_bytes=size,
        )  # fmt: skip
        try:
            info = inspect_pdf(dest)
            doc.page_count = info.page_count
            doc.title = info.title
            doc.pdf_metadata = info.metadata
            doc.encrypted = info.encrypted
            doc.warnings = info.warnings
            doc.equipment_name = guess_equipment(info.title, src.name)
            doc.status = "imported"
        except PdfProblem as exc:
            # Keep a record so the user sees why the file cannot be processed.
            doc.status = "error"
            doc.error_message = exc.message
            doc.text_status = doc.ocr_status = doc.jobs_status = doc.embed_status = "unavailable"
        session.add(doc)
        session.flush()
        if doc.page_count:
            session.execute(
                insert(Page),
                [{"document_id": doc.id, "page_no": n, "status": "pending"} for n in range(1, doc.page_count + 1)],
            )
        audit(session, "document", doc.id, "imported", None, {"filename": doc.filename, "pages": doc.page_count, "status": doc.status})
        entry.update(status="imported" if doc.status != "error" else "error", document_id=doc.id,
                     message=doc.error_message or f"{doc.page_count} pages")  # fmt: skip
        results.append(entry)
    return results


def remove_document(session: Session, document_id: str) -> dict:
    doc = session.get(Document, document_id)
    if doc is None:
        raise LookupError("Document not found")
    jobs = session.scalars(select(Job).where(Job.document_id == document_id)).all()
    reviewed = sum(1 for j in jobs if j.review_status != "pending")
    page_ids = select(Page.id).where(Page.document_id == document_id)
    session.execute(text("DELETE FROM pages_fts WHERE document_id = :d"), {"d": document_id})
    session.execute(
        text("DELETE FROM descriptions_fts WHERE job_id IN (SELECT id FROM jobs WHERE document_id = :d)"), {"d": document_id}
    )
    session.execute(delete(Page).where(Page.id.in_(page_ids)))
    stored = Path(doc.stored_path)
    audit(session, "document", doc.id, "removed", {"filename": doc.filename, "jobs": len(jobs), "reviewed_jobs": reviewed}, None, "user")
    session.delete(doc)
    session.flush()
    try:
        if stored.is_relative_to(get_paths().documents):
            stored.unlink(missing_ok=True)
    except OSError as exc:
        log.warning("Could not delete stored file %s: %s", stored, exc)
    return {"removed_jobs": len(jobs), "removed_reviewed_jobs": reviewed}
