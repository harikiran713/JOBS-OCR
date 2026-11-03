from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..ai.providers import try_provider
from ..db.models import (
    AuditLog,
    Document,
    Job,
    JobDescription,
    Page,
    ProcedureMatch,
    Project,
    ReviewDecision,
    SourceReference,
)
from ..db.session import get_session
from ..processing.engine import engine
from ..processing.handlers import TaskContext, describe_job, save_description
from ..retrieval.store import PageStore
from ..services import settings as settings_service
from ..services.jobs import apply_edits, audit, current_description, edits_for, effective, reset_for_rediscovery
from .serializers import description_dict, iso, job_machine_values, job_row, match_dict, ref_dict

router = APIRouter()

ATTENTION_RANK = {
    "procedure_failed": 0, "description_failed": 0, "procedure_needs_confirmation": 1, "procedure_not_found": 2,
    "extraction_needs_review": 2, "frequency_uncertain": 3, "frequency_missing": 4, "description_needs_review": 3,
    "description_provisional": 5, "stale": 1, "source_rejected": 1,
}  # fmt: skip


def missing_job_parts(s: Session, j: Job) -> list[str]:
    """A job must have an interval, equipment and a linked Job Description."""
    v = effective(j, edits_for(s, [j.id]).get(j.id))
    long_d = current_description(s, j.id, "long")
    missing = []
    if v["frequency_type"] == "unspecified":
        missing.append("a maintenance interval")
    if not (v["equipment"] or "").strip():
        missing.append("equipment")
    if long_d is None or not long_d.content.strip():
        missing.append("a job description (select the procedure pages)")
    return missing


def attention(row: dict[str, Any]) -> list[str]:
    reasons = []
    if row["extraction_status"] == "needs_review":
        reasons.append("extraction_needs_review")
    if row["frequency_uncertain"]:
        reasons.append("frequency_uncertain")
    if row["frequency_type"] == "unspecified":
        reasons.append("frequency_missing")
    ps, ds = row["procedure_status"], row["description_status"]
    if ps in ("needs_confirmation", "not_found", "failed"):
        reasons.append(f"procedure_{ps}")
    if ds in ("needs_review", "provisional", "failed"):
        reasons.append(f"description_{ds}")
    if row["stale"]:
        reasons.append("stale")
    return reasons


def _summaries(s: Session, project_id: str, job_ids: list[str] | None = None) -> tuple[dict, dict, dict]:
    docs = {d.id: d.filename for d in s.scalars(select(Document).where(Document.project_id == project_id))}
    q = (
        select(ProcedureMatch)
        .join(Job, Job.id == ProcedureMatch.job_id)
        .where(Job.project_id == project_id, ProcedureMatch.status != "rejected")
        .order_by(ProcedureMatch.selected.desc(), ProcedureMatch.rank)
    )
    if job_ids is not None:
        q = q.where(Job.id.in_(job_ids))
    procs: dict[str, dict] = {}
    for m in s.scalars(q):
        if m.job_id in procs:
            continue
        procs[m.job_id] = {
            "match_id": m.id, "document_id": m.document_id, "document_name": docs.get(m.document_id, ""),
            "start_page": m.start_page, "end_page": m.end_page, "selected": m.selected, "status": m.status,
            "score": m.score,
        }  # fmt: skip
    shorts = set(
        s.scalars(
            select(JobDescription.job_id).join(Job, Job.id == JobDescription.job_id).where(
                Job.project_id == project_id, JobDescription.kind == "short", JobDescription.is_current.is_(True)
            )
        )
    )
    return docs, procs, {j: True for j in shorts}


def build_rows(s: Session, project_id: str, job_ids: list[str] | None = None) -> list[dict[str, Any]]:
    q = select(Job).where(Job.project_id == project_id).order_by(Job.seq)
    if job_ids is not None:
        q = q.where(Job.id.in_(job_ids))
    jobs = s.scalars(q).all()
    edits = edits_for(s, [j.id for j in jobs])
    docs, procs, shorts = _summaries(s, project_id, job_ids)
    rows = []
    for j in jobs:
        r = job_row(j, edits.get(j.id), {"schedule_document": docs.get(j.document_id, ""), "procedure": procs.get(j.id), "has_short": j.id in shorts})
        r["attention"] = attention(r)
        rows.append(r)
    return rows


@router.get("/projects/{project_id}/jobs")
def list_jobs(
    project_id: str,
    q: str = "",
    review_status: str | None = None,
    procedure_status: str | None = None,
    description_status: str | None = None,
    document_id: str | None = None,
    frequency_type: str | None = None,
    attention_only: bool = False,
    sort: str = "seq",
    desc: bool = False,
    offset: int = Query(0, ge=0),
    limit: int = Query(500, ge=1, le=5000),
    s: Session = Depends(get_session),
) -> dict:
    rows = build_rows(s, project_id)
    if q:
        needle = q.lower()
        rows = [
            r for r in rows
            if needle in " ".join(str(r[k] or "") for k in ("code", "title", "equipment", "component", "frequency_original", "frequency_label", "schedule_document")).lower()
            or (r["procedure"] and needle in r["procedure"]["document_name"].lower())
        ]  # fmt: skip
    for key, val in (("review_status", review_status), ("procedure_status", procedure_status),
                     ("description_status", description_status), ("document_id", document_id), ("frequency_type", frequency_type)):  # fmt: skip
        if val:
            allowed = set(val.split(","))
            rows = [r for r in rows if r[key] in allowed]
    if attention_only:
        rows = [r for r in rows if r["attention"]]
    if rows and sort not in rows[0]:
        sort = "seq"

    def sort_key(r: dict) -> Any:
        v = r.get(sort)
        if sort == "frequency_label":
            return (r["frequency_type"], r["frequency_value"] or 0)
        return (v is None, str(v).lower() if isinstance(v, str) else v if v is not None else 0)

    rows.sort(key=sort_key, reverse=desc)
    return {"total": len(rows), "items": rows[offset : offset + limit]}


def _job(s: Session, job_id: str) -> Job:
    j = s.get(Job, job_id)
    if j is None:
        raise HTTPException(404, "Job not found")
    return j


def job_detail(s: Session, j: Job) -> dict:
    docs = {d.id: d.filename for d in s.scalars(select(Document).where(Document.project_id == j.project_id))}
    row = build_rows(s, j.project_id, [j.id])[0]
    matches = s.scalars(select(ProcedureMatch).where(ProcedureMatch.job_id == j.id).order_by(ProcedureMatch.selected.desc(), ProcedureMatch.rank)).all()
    history = s.scalars(select(JobDescription).where(JobDescription.job_id == j.id).order_by(JobDescription.kind, JobDescription.version.desc())).all()
    refs = s.scalars(select(SourceReference).where(SourceReference.job_id == j.id).order_by(SourceReference.ref_type, SourceReference.page_no)).all()
    decisions = s.scalars(select(ReviewDecision).where(ReviewDecision.job_id == j.id).order_by(ReviewDecision.id.desc())).all()
    audit_rows = s.scalars(select(AuditLog).where(AuditLog.entity_type == "job", AuditLog.entity_id == j.id).order_by(AuditLog.id.desc()).limit(100)).all()
    cfg = settings_service.get_all(s)
    return {
        **row,
        "machine": job_machine_values(j),
        "source_text": j.source_text,
        "source_bbox": j.source_bbox,
        "cross_reference": j.cross_reference,
        "extraction_notes": j.extraction_notes,
        "matches": [match_dict(m, docs.get(m.document_id, "")) for m in matches],
        "long": description_dict(current_description(s, j.id, "long")),
        "short": description_dict(current_description(s, j.id, "short")),
        "description_history": [
            {"id": d.id, "kind": d.kind, "version": d.version, "method": d.method, "created_by": d.created_by,
             "status": d.status, "is_current": d.is_current, "created_at": iso(d.created_at)}
            for d in history
        ],  # fmt: skip
        "references": [ref_dict(r, docs.get(r.document_id, "")) for r in refs],
        "decisions": [
            {"id": d.id, "decision": d.decision, "payload": d.payload, "comment": d.comment, "reviewer": d.reviewer, "created_at": iso(d.created_at)}
            for d in decisions
        ],
        "audit": [
            {"id": a.id, "action": a.action, "before": a.before, "after": a.after, "actor": a.actor, "at": iso(a.at)}
            for a in audit_rows
        ],
        "custom_fields": cfg["extraction.custom_fields"],
    }


@router.get("/jobs/{job_id}")
def get_job(job_id: str, s: Session = Depends(get_session)) -> dict:
    return job_detail(s, _job(s, job_id))


class JobPatch(BaseModel):
    changes: dict[str, Any] = Field(min_length=1)
    reviewer: str = Field(default="Reviewer", max_length=100)


@router.patch("/jobs/{job_id}")
def patch_job(job_id: str, body: JobPatch, s: Session = Depends(get_session)) -> dict:
    j = _job(s, job_id)
    custom = {f["key"] for f in settings_service.get(s, "extraction.custom_fields") or [] if isinstance(f, dict) and "key" in f}
    try:
        apply_edits(s, j, body.changes, body.reviewer, custom)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    s.add(ReviewDecision(job_id=j.id, decision="edit", payload=body.changes, reviewer=body.reviewer))
    if j.review_status == "approved":
        j.review_status = "pending"  # an edit after approval requires a new approval
    j.updated_at = j.updated_at  # touch
    return job_detail(s, j)


ReviewAction = Literal["approve", "reject", "needs_changes", "reset", "mark_frequency_uncertain", "request_reprocess"]


class ReviewIn(BaseModel):
    action: ReviewAction
    comment: str = Field(default="", max_length=5000)
    reviewer: str = Field(default="Reviewer", max_length=100)
    match_id: int | None = None


def _regenerate(s: Session, j: Job, force: bool) -> str:
    cfg = settings_service.get_all(s)
    ctx = TaskContext(
        task_id=0, project_id=j.project_id, document_id=None, payload={}, settings=cfg, provider=try_provider(cfg),
        run_cpu=lambda fn, *a: engine.run_cpu(1, fn, *a), progress=lambda _m: None, should_stop=lambda: False,
    )  # fmt: skip
    return describe_job(s, ctx, PageStore(s, j.project_id), j, force=force)


def _confirm_match(s: Session, j: Job, m: ProcedureMatch, reviewer: str, boundaries_changed: bool = False) -> None:
    for other in s.scalars(select(ProcedureMatch).where(ProcedureMatch.job_id == j.id, ProcedureMatch.id != m.id, ProcedureMatch.selected.is_(True))):
        other.selected = False
        if other.status == "selected":
            other.status = "candidate"
    m.selected = True
    m.status = "selected" if m.status != "verified" else "verified"
    j.procedure_status = "confirmed"
    audit(s, "job", j.id, "procedure_confirmed", None, {"match_id": m.id, "pages": [m.start_page, m.end_page]}, reviewer)
    long_d = current_description(s, j.id, "long")
    if long_d is None:
        j.description_status = "pending"
        return
    if long_d.procedure_match_id == m.id and not boundaries_changed:
        # Provisional text came from this exact procedure: it is now confirmed, no regeneration needed.
        for kind in ("long", "short"):
            d = current_description(s, j.id, kind)
            if d is not None and d.status == "provisional":
                d.status = "generated"
        if j.description_status == "provisional":
            j.description_status = "generated"
    elif long_d.created_by == "machine":
        _regenerate(s, j, force=False)  # machine text from a different procedure is safe to replace
    else:
        j.description_status = "needs_review"
        long_d.validation = {**(long_d.validation or {}), "outdated": "Procedure changed after manual edit; regenerate if appropriate."}


@router.post("/jobs/{job_id}/review")
def review(job_id: str, body: ReviewIn, s: Session = Depends(get_session)) -> dict:
    j = _job(s, job_id)
    before = j.review_status
    if body.action == "approve":
        if body.match_id is not None:
            m = s.get(ProcedureMatch, body.match_id)
            if m is None or m.job_id != j.id:
                raise HTTPException(404, "Procedure match not found")
            _confirm_match(s, j, m, body.reviewer)
        elif j.procedure_status == "needs_confirmation":
            raise HTTPException(409, "Confirm or reject the proposed procedure before approving this job.")
        missing = missing_job_parts(s, j)
        if missing:
            raise HTTPException(409, "A job needs " + ", ".join(missing) + " before it can be approved.")
        j.review_status = "approved"
        for kind in ("long", "short"):
            d = current_description(s, j.id, kind)
            if d is not None and d.status in ("generated", "provisional", "needs_review", "edited"):
                d.status = "approved"
    elif body.action == "reject":
        j.review_status = "rejected"
    elif body.action == "needs_changes":
        j.review_status = "needs_changes"
    elif body.action == "reset":
        j.review_status = "pending"
    elif body.action == "mark_frequency_uncertain":
        apply_edits(s, j, {"frequency_uncertain": True}, body.reviewer, set())
    elif body.action == "request_reprocess":
        reset_for_rediscovery(s, j)
        j.reprocess_requested = True
        s.flush()
        engine.start_project(j.project_id, trigger="reprocess_job")
    s.add(ReviewDecision(job_id=j.id, decision=body.action, payload={"match_id": body.match_id}, comment=body.comment, reviewer=body.reviewer))
    audit(s, "job", j.id, f"review:{body.action}", before, j.review_status, body.reviewer)
    s.flush()
    return job_detail(s, j)


class BulkReviewIn(BaseModel):
    job_ids: list[str] = Field(min_length=1, max_length=5000)
    action: Literal["approve", "reject", "needs_changes", "reset"]
    reviewer: str = Field(default="Reviewer", max_length=100)


@router.post("/jobs/bulk-review")
def bulk_review(body: BulkReviewIn, s: Session = Depends(get_session)) -> dict:
    done, skipped = 0, []
    status_for = {"approve": "approved", "reject": "rejected", "needs_changes": "needs_changes", "reset": "pending"}
    for j in s.scalars(select(Job).where(Job.id.in_(body.job_ids))):
        if body.action == "approve" and j.procedure_status == "needs_confirmation":
            skipped.append({"id": j.id, "reason": "procedure needs confirmation"})
            continue
        if body.action == "approve" and (missing := missing_job_parts(s, j)):
            skipped.append({"id": j.id, "reason": "missing " + ", ".join(missing)})
            continue
        before = j.review_status
        j.review_status = status_for[body.action]
        if body.action == "approve":
            for kind in ("long", "short"):
                d = current_description(s, j.id, kind)
                if d is not None and d.status in ("generated", "needs_review", "edited"):
                    d.status = "approved"
        s.add(ReviewDecision(job_id=j.id, decision=f"bulk_{body.action}", reviewer=body.reviewer))
        audit(s, "job", j.id, f"review:bulk_{body.action}", before, j.review_status, body.reviewer)
        done += 1
    return {"updated": done, "skipped": skipped}


class MatchSelectIn(BaseModel):
    reviewer: str = Field(default="Reviewer", max_length=100)


@router.post("/jobs/{job_id}/matches/{match_id}/select")
def select_match(job_id: str, match_id: int, body: MatchSelectIn, s: Session = Depends(get_session)) -> dict:
    j = _job(s, job_id)
    m = s.get(ProcedureMatch, match_id)
    if m is None or m.job_id != j.id:
        raise HTTPException(404, "Procedure match not found")
    if m.status == "rejected":
        m.status = "candidate"
    _confirm_match(s, j, m, body.reviewer)
    s.add(ReviewDecision(job_id=j.id, decision="select_procedure", payload={"match_id": m.id}, reviewer=body.reviewer))
    s.flush()
    return job_detail(s, j)


@router.post("/jobs/{job_id}/matches/{match_id}/reject")
def reject_match(job_id: str, match_id: int, body: MatchSelectIn, s: Session = Depends(get_session)) -> dict:
    j = _job(s, job_id)
    m = s.get(ProcedureMatch, match_id)
    if m is None or m.job_id != j.id:
        raise HTTPException(404, "Procedure match not found")
    was_selected = m.selected
    m.status, m.selected = "rejected", False
    remaining = s.scalar(select(ProcedureMatch.id).where(ProcedureMatch.job_id == j.id, ProcedureMatch.status != "rejected").limit(1))
    if was_selected or j.procedure_status in ("verified", "needs_confirmation", "confirmed"):
        j.procedure_status = "needs_confirmation" if remaining else "not_found"
    for kind in ("long", "short"):
        d = current_description(s, j.id, kind)
        if d is not None and d.procedure_match_id == m.id:
            d.status = "needs_review"
            d.validation = {**(d.validation or {}), "source_rejected": "The procedure this text came from was marked incorrect."}
            j.description_status = "needs_review"
    s.add(ReviewDecision(job_id=j.id, decision="reject_procedure", payload={"match_id": m.id}, reviewer=body.reviewer))
    audit(s, "job", j.id, "procedure_rejected", {"match_id": m.id}, None, body.reviewer)
    s.flush()
    return job_detail(s, j)


class ManualMatchIn(BaseModel):
    document_id: str
    start_page: int = Field(ge=1)
    end_page: int = Field(ge=1)
    start_y: float = Field(default=0.0, ge=0, le=1)
    end_y: float = Field(default=1.0, ge=0, le=1)
    heading: str = Field(default="", max_length=500)
    reviewer: str = Field(default="Reviewer", max_length=100)
    match_id: int | None = None  # when set, adjusts the boundaries of an existing match


@router.post("/jobs/{job_id}/matches/manual")
def manual_match(job_id: str, body: ManualMatchIn, s: Session = Depends(get_session)) -> dict:
    j = _job(s, job_id)
    doc = s.get(Document, body.document_id)
    if doc is None or doc.project_id != j.project_id:
        raise HTTPException(404, "Document not found in this project")
    if body.end_page < body.start_page or body.end_page > doc.page_count:
        raise HTTPException(422, f"Invalid page range (document has {doc.page_count} pages)")
    if body.match_id is not None:
        m = s.get(ProcedureMatch, body.match_id)
        if m is None or m.job_id != j.id:
            raise HTTPException(404, "Procedure match not found")
        original = {"document_id": m.document_id, "start_page": m.start_page, "end_page": m.end_page, "start_y": m.start_y, "end_y": m.end_y}
        m.evidence = {**(m.evidence or {}), "original_bounds": (m.evidence or {}).get("original_bounds", original), "adjusted_by": body.reviewer}
        m.document_id, m.start_page, m.end_page, m.start_y, m.end_y = doc.id, body.start_page, body.end_page, body.start_y, body.end_y
        m.created_by = "human"
        decision = "adjust_boundaries"
    else:
        m = ProcedureMatch(
            job_id=j.id, document_id=doc.id, start_page=body.start_page, end_page=body.end_page, start_y=body.start_y,
            end_y=body.end_y, heading=body.heading, rank=99, score=1.0, status="selected", created_by="human",
            evidence={"manual": True, "selected_by": body.reviewer},
        )  # fmt: skip
        s.add(m)
        decision = "manual_procedure"
    s.flush()
    _confirm_match(s, j, m, body.reviewer, boundaries_changed=body.match_id is not None)
    s.add(ReviewDecision(job_id=j.id, decision=decision, payload=body.model_dump(), reviewer=body.reviewer))
    s.flush()
    return job_detail(s, j)


class RegenerateIn(BaseModel):
    force: bool = False  # True also replaces human-edited descriptions (previous versions are kept)
    reviewer: str = Field(default="Reviewer", max_length=100)


@router.post("/jobs/{job_id}/descriptions/regenerate")
def regenerate(job_id: str, body: RegenerateIn, s: Session = Depends(get_session)) -> dict:
    j = _job(s, job_id)
    result = _regenerate(s, j, body.force)
    s.add(ReviewDecision(job_id=j.id, decision="regenerate_descriptions", payload={"force": body.force, "result": result}, reviewer=body.reviewer))
    s.flush()
    if result == "kept_human":
        raise HTTPException(409, "The long description was edited by a reviewer. Use force to replace it (previous versions are kept).")
    return job_detail(s, j)


class DescriptionEdit(BaseModel):
    content: str = Field(max_length=200_000)
    reviewer: str = Field(default="Reviewer", max_length=100)


@router.put("/jobs/{job_id}/descriptions/{kind}")
def edit_description(job_id: str, kind: Literal["long", "short"], body: DescriptionEdit, s: Session = Depends(get_session)) -> dict:
    j = _job(s, job_id)
    prev = current_description(s, j.id, kind)
    blocks = prev.blocks if prev is not None and kind == "long" and prev.content == body.content else []
    save_description(
        s, j, kind, body.content, blocks, "human", "edited", {"edited_from_version": prev.version if prev else None},
        prev.procedure_match_id if prev else None, prev.source_hash if prev else "", created_by="human",
    )  # fmt: skip
    if kind == "long":
        short = current_description(s, j.id, "short")
        if short is not None and short.created_by == "machine":
            short.validation = {**(short.validation or {}), "outdated": "Long description was edited; regenerate the short description if needed."}
            short.status = "needs_review"
    audit(s, "job", j.id, f"description_edited:{kind}", prev.content[:4000] if prev else None, body.content[:4000], body.reviewer)
    s.add(ReviewDecision(job_id=j.id, decision=f"edit_{kind}_description", reviewer=body.reviewer))
    if j.review_status == "approved":
        j.review_status = "pending"
    s.flush()
    return job_detail(s, j)


@router.post("/jobs/{job_id}/descriptions/short/regenerate")
def regenerate_short(job_id: str, body: MatchSelectIn, s: Session = Depends(get_session)) -> dict:
    """Rebuild only the short description from the current (possibly human-edited) long description."""
    from ..descriptions.short import generate_short

    j = _job(s, job_id)
    long_d = current_description(s, j.id, "long")
    if long_d is None or not long_d.content:
        raise HTTPException(409, "No long description to summarise.")
    cfg = settings_service.get_all(s)
    provider = try_provider(cfg) if cfg["ai.use_for_short_descriptions"] else None
    short = generate_short(long_d.content, long_d.blocks or [], provider, cfg["ai.chat_model"])
    save_description(s, j, "short", short.content, [], short.method, "generated" if short.ok else "needs_review",
                     short.validation, long_d.procedure_match_id, long_d.source_hash)  # fmt: skip
    s.add(ReviewDecision(job_id=j.id, decision="regenerate_short", reviewer=body.reviewer))
    s.flush()
    return job_detail(s, j)


@router.get("/projects/{project_id}/review-queue")
def review_queue(project_id: str, s: Session = Depends(get_session)) -> dict:
    if s.get(Project, project_id) is None:
        raise HTTPException(404, "Project not found")
    rows = [r for r in build_rows(s, project_id) if r["review_status"] in ("pending", "needs_changes")]
    for r in rows:
        r["priority"] = min((ATTENTION_RANK.get(a, 9) for a in r["attention"]), default=10)
    rows.sort(key=lambda r: (r["priority"], r["seq"]))
    doc_names = {d.id: d.filename for d in s.scalars(select(Document).where(Document.project_id == project_id))}
    pages = s.execute(
        select(Page.document_id, Page.page_no, Page.status, Page.status_reason, Page.page_kind)
        .where(Page.document_id.in_(list(doc_names)), Page.status.in_(["needs_review", "failed"]))
        .order_by(Page.document_id, Page.page_no)
        .limit(500)
    ).all()
    return {
        "jobs": rows,
        "pages": [
            {"document_id": p[0], "document_name": doc_names.get(p[0], ""), "page_no": p[1], "status": p[2], "status_reason": p[3], "page_kind": p[4]}
            for p in pages
        ],
    }
