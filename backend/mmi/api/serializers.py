"""Conversion of ORM rows to API dictionaries (the typed contract mirrored in desktop/src/lib/types.ts)."""

from __future__ import annotations

from typing import Any

from ..db.models import Document, Job, JobDescription, ProcedureMatch, ProcessingRun, Project, SourceReference
from ..services.jobs import effective, job_code


def iso(dt) -> str | None:  # noqa: ANN001
    return dt.isoformat().replace("+00:00", "Z") if dt else None


def project_dict(p: Project, stats: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": p.id, "name": p.name, "vessel_name": p.vessel_name, "vessel_imo": p.vessel_imo,
        "vessel_type": p.vessel_type, "owner": p.owner, "notes": p.notes, "status": p.status,
        "processing_state": p.processing_state, "created_at": iso(p.created_at), "updated_at": iso(p.updated_at),
        "stats": stats or {},
    }  # fmt: skip


def document_dict(d: Document, stats: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": d.id, "project_id": d.project_id, "filename": d.filename, "original_path": d.original_path,
        "size_bytes": d.size_bytes, "page_count": d.page_count, "title": d.title, "metadata": d.pdf_metadata,
        "content_type": d.content_type, "equipment_name": d.equipment_name, "status": d.status,
        "error_message": d.error_message, "warnings": d.warnings or [], "encrypted": d.encrypted,
        "text_status": d.text_status, "ocr_status": d.ocr_status, "jobs_status": d.jobs_status,
        "embed_status": d.embed_status, "imported_at": iso(d.imported_at), "sha256": d.sha256, "stats": stats or {},
    }  # fmt: skip


def job_row(j: Job, edits: dict[str, Any] | None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    v = effective(j, edits)
    extra = extra or {}
    return {
        "id": j.id, "code": job_code(j), "seq": j.seq, "project_id": j.project_id, "document_id": j.document_id,
        "title": v["title"], "equipment": v["equipment"], "component": v["component"], "action": j.action,
        "frequency_original": v["frequency_original"], "frequency_value": v["frequency_value"],
        "frequency_unit": v["frequency_unit"], "frequency_type": v["frequency_type"],
        "frequency_type_label": v["frequency_type_label"], "frequency_label": v["frequency_label"],
        "frequency_uncertain": v["frequency_uncertain"], "schedule_page_no": j.schedule_page_no,
        "schedule_document": extra.get("schedule_document", ""), "procedure": extra.get("procedure"),
        "extraction_method": j.extraction_method, "extraction_confidence": j.extraction_confidence,
        "extraction_status": j.extraction_status, "procedure_status": j.procedure_status,
        "description_status": j.description_status, "review_status": j.review_status, "stale": j.stale,
        "edited_fields": v["edited_fields"], "extra_fields": v["extra_fields"],
        "has_short": extra.get("has_short", False), "updated_at": iso(j.updated_at),
    }  # fmt: skip


def job_machine_values(j: Job) -> dict[str, Any]:
    return {
        "title": j.title, "original_title": j.original_title, "equipment": j.equipment, "component": j.component,
        "frequency_original": j.frequency_original, "frequency_value": j.frequency_value,
        "frequency_unit": j.frequency_unit, "frequency_type": j.frequency_type,
        "frequency_uncertain": j.frequency_uncertain, "frequency_notes": j.frequency_notes,
        "frequency_alternatives": j.frequency_alternatives, "extra_fields": j.extra_fields,
    }  # fmt: skip


def match_dict(m: ProcedureMatch, doc_name: str) -> dict[str, Any]:
    return {
        "id": m.id, "document_id": m.document_id, "document_name": doc_name, "start_page": m.start_page,
        "end_page": m.end_page, "start_y": m.start_y, "end_y": m.end_y, "heading": m.heading, "rank": m.rank,
        "score": m.score, "score_breakdown": m.score_breakdown, "evidence": m.evidence, "stages": m.stages,
        "status": m.status, "selected": m.selected, "created_by": m.created_by, "created_at": iso(m.created_at),
    }  # fmt: skip


def description_dict(d: JobDescription | None) -> dict[str, Any] | None:
    if d is None:
        return None
    return {
        "id": d.id, "kind": d.kind, "version": d.version, "content": d.content, "blocks": d.blocks,
        "method": d.method, "status": d.status, "validation": d.validation, "created_by": d.created_by,
        "procedure_match_id": d.procedure_match_id, "created_at": iso(d.created_at),
    }  # fmt: skip


def ref_dict(r: SourceReference, doc_name: str) -> dict[str, Any]:
    return {
        "id": r.id, "document_id": r.document_id, "document_name": doc_name, "page_no": r.page_no,
        "ref_type": r.ref_type, "quote": r.quote, "bbox": r.bbox, "image_path": r.image_path,
    }  # fmt: skip


def run_dict(r: ProcessingRun) -> dict[str, Any]:
    return {
        "id": r.id, "project_id": r.project_id, "trigger": r.trigger, "status": r.status, "message": r.message,
        "tasks_total": r.tasks_total, "tasks_done": r.tasks_done, "tasks_failed": r.tasks_failed,
        "started_at": iso(r.started_at), "finished_at": iso(r.finished_at),
    }  # fmt: skip
