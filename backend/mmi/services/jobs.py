"""Job persistence, effective values and audited human corrections."""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..db.models import AuditLog, Job, JobDescription, JobEdit, Page, ProcedureMatch, Project, SourceReference
from ..extraction.frequency import FREQUENCY_TYPES
from ..extraction.jobs import JobCandidate

# Fields a reviewer may correct. Machine values on ``jobs`` are never overwritten.
EDITABLE_FIELDS = {
    "title": str,
    "equipment": str,
    "component": str,
    "frequency_original": str,
    "frequency_value": (float, int, type(None)),
    "frequency_unit": str,
    "frequency_type": str,
    "frequency_uncertain": bool,
}


def audit(session: Session, entity_type: str, entity_id: str, action: str, before: Any = None, after: Any = None, actor: str = "system") -> None:
    session.add(AuditLog(entity_type=entity_type, entity_id=str(entity_id), action=action, before=before, after=after, actor=actor))


def job_code(job: Job) -> str:
    return f"J-{job.seq:05d}"


def edits_for(session: Session, job_ids: list[str]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if not job_ids:
        return out
    for e in session.scalars(select(JobEdit).where(JobEdit.job_id.in_(job_ids))):
        out.setdefault(e.job_id, {})[e.field] = e.value
    return out


def effective(job: Job, edits: dict[str, Any] | None) -> dict[str, Any]:
    """Merge machine values with human corrections; ``edited`` lists corrected fields."""
    edits = edits or {}
    values = {f: getattr(job, f) for f in EDITABLE_FIELDS}
    extra = dict(job.extra_fields or {})
    for k, v in edits.items():
        if k.startswith("extra."):
            extra[k[6:]] = v
        elif k in values:
            values[k] = v
    values["extra_fields"] = extra
    values["edited_fields"] = sorted(edits.keys())
    ftype = values["frequency_type"]
    values["frequency_type_label"] = FREQUENCY_TYPES.get(ftype, ftype)
    values["frequency_label"] = frequency_label(values)
    return values


def frequency_label(v: dict[str, Any]) -> str:
    ftype, value, unit = v.get("frequency_type"), v.get("frequency_value"), v.get("frequency_unit") or ""
    if ftype in ("event", "condition"):
        original = (v.get("frequency_original") or "").strip()
        if original.upper() == "B":  # schedule column code
            return "Based on observations"
        if len(original) <= 2:
            return FREQUENCY_TYPES.get(ftype, "")
        return original
    if value is None:
        return FREQUENCY_TYPES.get(ftype or "unspecified", "Not specified")
    num = int(value) if float(value).is_integer() else value
    suffix = {
        "operating_hours": "operating hours", "running_hours": "running hours", "engine_hours": "engine hours",
        "hours_unqualified": "hours", "cycle": "cycles",
    }.get(ftype or "", unit + ("s" if num != 1 and unit else ""))  # fmt: skip
    return f"{num} {suffix}".strip()


def apply_edits(session: Session, job: Job, changes: dict[str, Any], actor: str, custom_keys: set[str]) -> dict[str, Any]:
    current = edits_for(session, [job.id]).get(job.id, {})
    before = effective(job, current)
    for field, value in changes.items():
        if field.startswith("extra."):
            if field[6:] not in custom_keys:
                raise ValueError(f"Unknown custom field '{field[6:]}'")
            machine = (job.extra_fields or {}).get(field[6:])
        elif field in EDITABLE_FIELDS:
            if not isinstance(value, EDITABLE_FIELDS[field]):
                raise ValueError(f"Invalid value for {field}")
            if field == "frequency_type" and value not in FREQUENCY_TYPES:
                raise ValueError(f"Unknown frequency type '{value}'")
            machine = getattr(job, field)
        else:
            raise ValueError(f"Field '{field}' cannot be edited")
        existing = session.scalar(select(JobEdit).where(JobEdit.job_id == job.id, JobEdit.field == field))
        if value == machine:
            # Reverting to the machine value removes the correction.
            if existing is not None:
                session.delete(existing)
            continue
        if existing is None:
            session.add(JobEdit(job_id=job.id, field=field, value=value, edited_by=actor))
        else:
            existing.value = value
            existing.edited_by = actor
    session.flush()
    after = effective(job, edits_for(session, [job.id]).get(job.id, {}))
    audit(session, "job", job.id, "edit", {k: before.get(k) for k in changes}, {k: after.get(k) for k in changes}, actor)
    return after


def next_seq(session: Session, project_id: str) -> int:
    project = session.get(Project, project_id)
    project.job_seq = (project.job_seq or 0) + 1
    return project.job_seq


def upsert_extracted_jobs(
    session: Session, project_id: str, document_id: str, doc_sha: str, candidates: list[JobCandidate]
) -> dict[str, int]:
    """Insert new jobs, keep existing ones (by stable key) and retire disappeared ones safely.

    Jobs a human has reviewed or edited are never deleted; if a re-extraction no
    longer finds them they are kept and flagged ``stale``.
    """
    existing = {j.job_key: j for j in session.scalars(select(Job).where(Job.document_id == document_id))}
    seen: set[str] = set()
    stats = {"created": 0, "kept": 0, "retired": 0, "stale": 0}
    for c in candidates:
        key = c.key(doc_sha)
        if key in seen:
            continue
        seen.add(key)
        if key in existing:
            job = existing[key]
            job.stale = False
            job.extra_fields = {**(job.extra_fields or {}), **c.extra}
            job.extraction_confidence = c.confidence
            job.extraction_notes = c.notes
            stats["kept"] += 1
            continue
        job = Job(
            project_id=project_id, document_id=document_id, job_key=key, seq=next_seq(session, project_id),
            title=c.title, original_title=c.original_title, equipment=c.equipment, component=c.component,
            action=c.action, frequency_original=c.frequency.original, frequency_value=c.frequency.value,
            frequency_unit=c.frequency.unit, frequency_type=c.frequency.type,
            frequency_alternatives=c.frequency.alternatives, frequency_uncertain=c.frequency.uncertain,
            frequency_notes=" ".join(c.frequency.notes), schedule_page_no=c.page_no, source_text=c.source_text,
            source_bbox=c.bbox, cross_reference=c.cross_reference, extraction_method=c.method, extra_fields=dict(c.extra),
            extraction_confidence=c.confidence, extraction_notes=c.notes,
            extraction_status="needs_review" if c.needs_review else "extracted",
        )  # fmt: skip
        session.add(job)
        session.flush()
        session.add(
            SourceReference(
                job_id=job.id, document_id=document_id, page_no=c.page_no, ref_type="schedule",
                quote=c.source_text[:2000], bbox=c.bbox,
            )
        )  # fmt: skip
        audit(session, "job", job.id, "extracted", None, {"title": c.title, "method": c.method, "page": c.page_no})
        stats["created"] += 1

    for key, job in existing.items():
        if key in seen:
            continue
        has_edits = session.scalar(select(func.count()).select_from(JobEdit).where(JobEdit.job_id == job.id))
        if job.review_status != "pending" or has_edits:
            job.stale = True
            stats["stale"] += 1
            audit(session, "job", job.id, "marked_stale", None, {"reason": "not found by re-extraction"})
        else:
            session.delete(job)
            stats["retired"] += 1

    # Per-page job counts for the page registry.
    counts = dict(
        session.execute(
            select(Job.schedule_page_no, func.count()).where(Job.document_id == document_id).group_by(Job.schedule_page_no)
        ).all()
    )
    for page in session.scalars(select(Page).where(Page.document_id == document_id, Page.job_count > 0)):
        page.job_count = 0
    for page_no, n in counts.items():
        session.execute(Page.__table__.update().where(Page.document_id == document_id, Page.page_no == page_no).values(job_count=n))
    return stats


def current_description(session: Session, job_id: str, kind: str) -> JobDescription | None:
    return session.scalar(
        select(JobDescription)
        .where(JobDescription.job_id == job_id, JobDescription.kind == kind, JobDescription.is_current.is_(True))
        .order_by(JobDescription.version.desc())
    )


def selected_match(session: Session, job_id: str) -> ProcedureMatch | None:
    return session.scalar(
        select(ProcedureMatch).where(ProcedureMatch.job_id == job_id, ProcedureMatch.selected.is_(True)).limit(1)
    )


def reset_for_rediscovery(session: Session, job: Job, keep_human: bool = True) -> None:
    q = delete(ProcedureMatch).where(ProcedureMatch.job_id == job.id)
    if keep_human:
        q = q.where(ProcedureMatch.created_by == "machine")
    session.execute(q)
    job.procedure_status = "pending"
    long_desc = current_description(session, job.id, "long")
    if long_desc is None or long_desc.created_by != "human":
        job.description_status = "pending"
