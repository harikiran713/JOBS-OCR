"""SQLAlchemy ORM models.

Design rules (see docs/ARCHITECTURE.md):
* Machine-extracted values live on the primary rows and are never overwritten
  by reviewers. Human corrections are stored in ``job_edits`` and every change
  is written to ``audit_log``.
* Large binaries (PDFs, rendered images, evidence crops) live on disk and are
  referenced by path.
* Stable string identifiers (UUID4) for user-facing entities; integer keys for
  high-volume rows such as pages.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    """Stores naive UTC in SQLite, always returns timezone-aware UTC."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc)


class Base(DeclarativeBase):
    type_annotation_map = {datetime: UTCDateTime, dict[str, Any]: JSON, list[Any]: JSON}


# ---------------------------------------------------------------------------
# Projects & documents
# ---------------------------------------------------------------------------


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200))
    vessel_name: Mapped[str] = mapped_column(String(200), default="")
    vessel_imo: Mapped[str] = mapped_column(String(40), default="")
    vessel_type: Mapped[str] = mapped_column(String(100), default="")
    owner: Mapped[str] = mapped_column(String(200), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | archived
    # idle | running | paused  (persisted so processing resumes after restart)
    processing_state: Mapped[str] = mapped_column(String(20), default="idle")
    job_seq: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    documents: Mapped[list[Document]] = relationship(back_populates="project", cascade="all, delete-orphan")


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("project_id", "sha256", name="uq_document_project_hash"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(500))
    original_path: Mapped[str] = mapped_column(Text)
    stored_path: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    title: Mapped[str] = mapped_column(Text, default="")
    pdf_metadata: Mapped[dict[str, Any]] = mapped_column(default=dict)
    # digital | scanned | mixed | unknown — determined after text extraction
    content_type: Mapped[str] = mapped_column(String(20), default="unknown")
    # Equipment the manual covers; guessed from the document, editable by users.
    equipment_name: Mapped[str] = mapped_column(String(300), default="")
    # imported | inspecting | processing | ready | error
    status: Mapped[str] = mapped_column(String(20), default="imported")
    error_message: Mapped[str] = mapped_column(Text, default="")
    warnings: Mapped[list[Any]] = mapped_column(default=list)
    encrypted: Mapped[bool] = mapped_column(Boolean, default=False)
    # pending | partial | complete
    text_status: Mapped[str] = mapped_column(String(20), default="pending")
    # not_needed | pending | partial | complete | unavailable
    ocr_status: Mapped[str] = mapped_column(String(20), default="pending")
    # pending | complete
    jobs_status: Mapped[str] = mapped_column(String(20), default="pending")
    embed_status: Mapped[str] = mapped_column(String(20), default="pending")
    imported_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    project: Mapped[Project] = relationship(back_populates="documents")


class Page(Base):
    __tablename__ = "pages"
    __table_args__ = (
        UniqueConstraint("document_id", "page_no", name="uq_page_doc_no"),
        Index("ix_pages_doc_status", "document_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    page_no: Mapped[int] = mapped_column(Integer)  # 1-based, as printed by viewers
    # pending | processing | completed | needs_review | failed | skipped
    status: Mapped[str] = mapped_column(String(20), default="pending")
    status_reason: Mapped[str] = mapped_column(Text, default="")
    text_source: Mapped[str] = mapped_column(String(20), default="none")  # digital | ocr | vlm | none
    char_count: Mapped[int] = mapped_column(Integer, default=0)
    image_count: Mapped[int] = mapped_column(Integer, default=0)
    ocr_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    # schedule | procedure | parts_list | toc | general | blank | unknown
    page_kind: Mapped[str] = mapped_column(String(20), default="unknown")
    kind_scores: Mapped[dict[str, Any]] = mapped_column(default=dict)
    job_count: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    # Document structure printed in the page margins: {"doc_code", "page_n", "page_m", "labels": [...]}
    layout_meta: Mapped[dict[str, Any]] = mapped_column(default=dict)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class PageText(Base):
    """Extracted text and line layout for a page (1:1 with pages)."""

    __tablename__ = "page_texts"

    page_id: Mapped[int] = mapped_column(ForeignKey("pages.id", ondelete="CASCADE"), primary_key=True)
    text: Mapped[str] = mapped_column(Text, default="")
    # [{"t": text, "b": [x0,y0,x1,y1], "s": size, "f": bold?}, ...] normalised to page height
    lines: Mapped[list[Any]] = mapped_column(default=list)
    headings: Mapped[list[Any]] = mapped_column(default=list)
    source: Mapped[str] = mapped_column(String(20), default="digital")
    extractor_version: Mapped[str] = mapped_column(String(20), default="")


class TextCache(Base):
    """Content-addressed cache so identical documents are never re-extracted."""

    __tablename__ = "text_cache"
    __table_args__ = (UniqueConstraint("doc_sha256", "page_no", "extractor_version", name="uq_text_cache"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    doc_sha256: Mapped[str] = mapped_column(String(64), index=True)
    page_no: Mapped[int] = mapped_column(Integer)
    extractor_version: Mapped[str] = mapped_column(String(20))
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class PageEmbedding(Base):
    __tablename__ = "page_embeddings"
    __table_args__ = (UniqueConstraint("page_id", "chunk_idx", "model", name="uq_page_embedding"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    page_id: Mapped[int] = mapped_column(ForeignKey("pages.id", ondelete="CASCADE"), index=True)
    chunk_idx: Mapped[int] = mapped_column(Integer, default=0)
    model: Mapped[str] = mapped_column(String(200))
    dim: Mapped[int] = mapped_column(Integer)
    vector: Mapped[bytes] = mapped_column(LargeBinary)  # float32 little-endian, L2-normalised
    text_hash: Mapped[str] = mapped_column(String(64))


# ---------------------------------------------------------------------------
# Maintenance jobs
# ---------------------------------------------------------------------------


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("project_id", "job_key", name="uq_job_key"),
        Index("ix_jobs_project_review", "project_id", "review_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    # Deterministic key (document hash + page + normalised title + frequency) so
    # re-extraction can find the same job and keep reviewer work.
    job_key: Mapped[str] = mapped_column(String(64))
    seq: Mapped[int] = mapped_column(Integer)  # displayed as J-00001 within a project

    # --- machine-extracted values (immutable after extraction) ---
    title: Mapped[str] = mapped_column(Text)
    original_title: Mapped[str] = mapped_column(Text)
    equipment: Mapped[str] = mapped_column(Text, default="")
    component: Mapped[str] = mapped_column(Text, default="")
    action: Mapped[str] = mapped_column(String(40), default="")
    frequency_original: Mapped[str] = mapped_column(Text, default="")
    frequency_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    frequency_unit: Mapped[str] = mapped_column(String(20), default="")
    # calendar | operating_hours | engine_hours | running_hours | cycle | event | condition | unspecified
    frequency_type: Mapped[str] = mapped_column(String(20), default="unspecified")
    frequency_alternatives: Mapped[list[Any]] = mapped_column(default=list)
    frequency_uncertain: Mapped[bool] = mapped_column(Boolean, default=False)
    frequency_notes: Mapped[str] = mapped_column(Text, default="")
    schedule_page_no: Mapped[int] = mapped_column(Integer)
    source_text: Mapped[str] = mapped_column(Text, default="")
    source_bbox: Mapped[list[Any]] = mapped_column(default=list)
    cross_reference: Mapped[str] = mapped_column(Text, default="")
    extra_fields: Mapped[dict[str, Any]] = mapped_column(default=dict)
    extraction_method: Mapped[str] = mapped_column(String(40), default="")
    extraction_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    extraction_notes: Mapped[list[Any]] = mapped_column(default=list)
    # extracted | needs_review
    extraction_status: Mapped[str] = mapped_column(String(20), default="extracted")

    # --- pipeline state ---
    # pending | verified | needs_confirmation | confirmed | not_found | failed
    procedure_status: Mapped[str] = mapped_column(String(30), default="pending")
    # pending | generated | provisional | needs_review | failed | not_available
    description_status: Mapped[str] = mapped_column(String(30), default="pending")
    # pending | approved | rejected | needs_changes
    review_status: Mapped[str] = mapped_column(String(20), default="pending")
    # Set when a re-extraction no longer finds this job but it was kept because a human reviewed it.
    stale: Mapped[bool] = mapped_column(Boolean, default=False)
    reprocess_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    edits: Mapped[list[JobEdit]] = relationship(cascade="all, delete-orphan")


class JobEdit(Base):
    """Current human-corrected value for a job field (machine value is untouched)."""

    __tablename__ = "job_edits"
    __table_args__ = (UniqueConstraint("job_id", "field", name="uq_job_edit_field"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    field: Mapped[str] = mapped_column(String(60))
    value: Mapped[Any] = mapped_column(JSON)
    edited_by: Mapped[str] = mapped_column(String(100), default="reviewer")
    edited_at: Mapped[datetime] = mapped_column(default=utcnow)


class ProcedureMatch(Base):
    __tablename__ = "procedure_matches"
    __table_args__ = (Index("ix_matches_job", "job_id", "rank"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    start_page: Mapped[int] = mapped_column(Integer)
    end_page: Mapped[int] = mapped_column(Integer)
    # Normalised y-coordinates of the procedure start heading / end boundary.
    start_y: Mapped[float] = mapped_column(Float, default=0.0)
    end_y: Mapped[float] = mapped_column(Float, default=1.0)
    heading: Mapped[str] = mapped_column(Text, default="")
    rank: Mapped[int] = mapped_column(Integer, default=0)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    score_breakdown: Mapped[dict[str, Any]] = mapped_column(default=dict)
    evidence: Mapped[dict[str, Any]] = mapped_column(default=dict)
    stages: Mapped[list[Any]] = mapped_column(default=list)  # which retrieval stages surfaced it
    # candidate | verified | selected | rejected
    status: Mapped[str] = mapped_column(String(20), default="candidate")
    selected: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str] = mapped_column(String(20), default="machine")  # machine | human
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class JobDescription(Base):
    __tablename__ = "job_descriptions"
    __table_args__ = (Index("ix_desc_job_kind", "job_id", "kind", "is_current"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    procedure_match_id: Mapped[int | None] = mapped_column(
        ForeignKey("procedure_matches.id", ondelete="SET NULL"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(10))  # long | short
    version: Mapped[int] = mapped_column(Integer, default=1)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    content: Mapped[str] = mapped_column(Text, default="")
    # long: [{"type": "step"|"safety"|..., "text": ..., "page": n, "number": "3"}]
    blocks: Mapped[list[Any]] = mapped_column(default=list)
    # extractive | llm:<model> | human
    method: Mapped[str] = mapped_column(String(100), default="extractive")
    # generated | provisional | needs_review | approved
    status: Mapped[str] = mapped_column(String(20), default="generated")
    validation: Mapped[dict[str, Any]] = mapped_column(default=dict)
    # Hash of the long description a short one was derived from (staleness detection).
    source_hash: Mapped[str] = mapped_column(String(64), default="")
    created_by: Mapped[str] = mapped_column(String(20), default="machine")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class SourceReference(Base):
    __tablename__ = "source_references"
    __table_args__ = (Index("ix_srcref_job", "job_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    description_id: Mapped[int | None] = mapped_column(
        ForeignKey("job_descriptions.id", ondelete="CASCADE"), nullable=True
    )
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    page_no: Mapped[int] = mapped_column(Integer)
    # schedule | procedure | cross_reference | evidence_image
    ref_type: Mapped[str] = mapped_column(String(30))
    quote: Mapped[str] = mapped_column(Text, default="")
    bbox: Mapped[list[Any]] = mapped_column(default=list)
    image_path: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class ReviewDecision(Base):
    __tablename__ = "review_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    decision: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
    comment: Mapped[str] = mapped_column(Text, default="")
    reviewer: Mapped[str] = mapped_column(String(100), default="reviewer")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_entity", "entity_type", "entity_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity_type: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(60))
    before: Mapped[Any] = mapped_column(JSON, nullable=True)
    after: Mapped[Any] = mapped_column(JSON, nullable=True)
    actor: Mapped[str] = mapped_column(String(100), default="system")
    at: Mapped[datetime] = mapped_column(default=utcnow)


# ---------------------------------------------------------------------------
# Processing queue
# ---------------------------------------------------------------------------


class ProcessingRun(Base):
    __tablename__ = "processing_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    trigger: Mapped[str] = mapped_column(String(40), default="user")
    # running | paused | completed | cancelled | completed_with_errors
    status: Mapped[str] = mapped_column(String(30), default="running")
    message: Mapped[str] = mapped_column(Text, default="")
    tasks_total: Mapped[int] = mapped_column(Integer, default=0)
    tasks_done: Mapped[int] = mapped_column(Integer, default=0)
    tasks_failed: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)


class ProcessingTask(Base):
    __tablename__ = "processing_tasks"
    __table_args__ = (
        Index("ix_tasks_status_priority", "status", "priority", "id"),
        Index("ix_tasks_project_status", "project_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("processing_runs.id", ondelete="SET NULL"), nullable=True)
    # inspect_document | extract_pages | embed_pages | extract_jobs | discover_procedures | describe_jobs
    kind: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
    # Prevents the coordinator from enqueuing the same unit of work twice.
    dedupe_key: Mapped[str] = mapped_column(String(200), index=True)
    # pending | running | completed | failed | cancelled
    status: Mapped[str] = mapped_column(String(20), default="pending")
    priority: Mapped[int] = mapped_column(Integer, default=100)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    not_before: Mapped[datetime | None] = mapped_column(nullable=True)
    progress_message: Mapped[str] = mapped_column(Text, default="")
    last_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)


class ProcessingError(Base):
    __tablename__ = "processing_errors"
    __table_args__ = (Index("ix_errors_project", "project_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=True)
    task_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # document: the PDF/page itself is the problem; application: a bug or environment failure
    category: Mapped[str] = mapped_column(String(20))
    stage: Mapped[str] = mapped_column(String(40), default="")
    message: Mapped[str] = mapped_column(Text)
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


# ---------------------------------------------------------------------------
# Export & settings
# ---------------------------------------------------------------------------


class ExportTemplate(Base):
    __tablename__ = "export_templates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    # [{"field": "job_code", "header": "Job ID"}, ...]
    columns: Mapped[list[Any]] = mapped_column(default=list)
    # same_sheet | separate_sheets
    description_layout: Mapped[str] = mapped_column(String(20), default="same_sheet")
    builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class ExportRecord(Base):
    __tablename__ = "export_history"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    file_path: Mapped[str] = mapped_column(Text)
    template_name: Mapped[str] = mapped_column(String(200), default="")
    scope: Mapped[str] = mapped_column(String(30))
    job_count: Mapped[int] = mapped_column(Integer, default=0)
    options: Mapped[dict[str, Any]] = mapped_column(default=dict)
    warnings: Mapped[list[Any]] = mapped_column(default=list)
    status: Mapped[str] = mapped_column(String(20), default="completed")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
