"""Task handlers – one per pipeline stage.

Handlers receive a ``TaskContext`` giving them a DB session factory, settings,
the optional AI provider and ``run_cpu`` (executes MuPDF/OCR work in a worker
process). Each handler commits its results in small transactions so progress
survives interruption; re-running a handler is always safe (idempotent).
"""

from __future__ import annotations

import hashlib
import re
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..db.models import (
    Document,
    Job,
    JobDescription,
    Page,
    PageEmbedding,
    PageText,
    ProcedureMatch,
    ProcessingError,
    SourceReference,
    TextCache,
)
from ..db.session import session_scope
from ..descriptions.long import extract_long_description
from ..descriptions.short import generate_short
from ..extraction.frequency import parse_frequency
from ..extraction.classify import SCHEDULE_TITLE
from ..extraction.layout import is_running_margin
from ..extraction.jobs import JobCandidate, PageContext, extract_jobs_from_page, make_title, parse_legend
from ..pdf.text import EXTRACTOR_VERSION, extract_pages_batch, extract_tables
from ..extraction.classify import detect_action
from ..extraction.jobs import MAKER_DOCUMENT, NO_DOCUMENT
from ..retrieval.discovery import JobQuery, ProcedureFinder
from ..retrieval.doclink import DocumentIndex, link_procedure, reference_code
from ..retrieval.store import PageStore
from ..services.jobs import audit, current_description, edits_for, effective, upsert_extracted_jobs

log = logging.getLogger(__name__)


class TaskCancelled(Exception):
    pass


@dataclass
class TaskContext:
    task_id: int
    project_id: str
    document_id: str | None
    payload: dict[str, Any]
    settings: dict[str, Any]
    provider: Any
    run_cpu: Callable[..., Any]
    progress: Callable[[str], None]
    should_stop: Callable[[], bool]


def _ocr_opts(settings: dict[str, Any]) -> dict[str, Any]:
    return {
        "ocr_enabled": bool(settings.get("ocr.enabled", True)) and settings.get("ocr.engine") != "none",
        "ocr_engine": settings.get("ocr.engine", "auto"),
        "ocr_min_chars": int(settings.get("ocr.min_chars", 40)),
        "ocr_dpi": int(settings.get("ocr.dpi", 200)),
        "ocr_min_confidence": float(settings.get("ocr.min_confidence", 0.6)),
    }


def _cache_version(opts: dict[str, Any]) -> str:
    # OCR options change results for scanned pages, so they are part of the cache key.
    sig = f"{EXTRACTOR_VERSION}|{opts['ocr_enabled']}|{opts['ocr_engine']}|{opts['ocr_dpi']}|{opts['ocr_min_chars']}"
    return hashlib.sha1(sig.encode()).hexdigest()[:16]


def _record_error(s: Session, ctx: TaskContext, category: str, stage: str, message: str, page_no: int | None = None, detail: str = "") -> None:
    s.add(
        ProcessingError(
            project_id=ctx.project_id, document_id=ctx.document_id, task_id=ctx.task_id, page_no=page_no,
            category=category, stage=stage, message=message[:2000], detail=detail[:8000],
        )
    )  # fmt: skip


# ---------------------------------------------------------------------------
# Stage: page text extraction (+ OCR)
# ---------------------------------------------------------------------------


def _write_page(s: Session, doc: Document, page: Page, r: dict[str, Any], project_id: str) -> None:
    page.status = r["status"]
    page.status_reason = r.get("status_reason", "")
    page.text_source = r.get("source", "none") if r.get("text") else "none"
    page.char_count = r.get("char_count", 0)
    page.image_count = r.get("image_count", 0)
    page.ocr_confidence = r.get("ocr_confidence")
    page.page_kind = r.get("kind", "unknown")
    page.kind_scores = r.get("scores", {})
    page.layout_meta = r.get("meta", {})
    pt = s.get(PageText, page.id)
    if pt is None:
        pt = PageText(page_id=page.id)
        s.add(pt)
    pt.text = r.get("text", "")
    pt.lines = r.get("lines", [])
    pt.headings = r.get("headings", [])
    pt.source = page.text_source
    pt.extractor_version = EXTRACTOR_VERSION
    s.execute(text("DELETE FROM pages_fts WHERE page_id = :p"), {"p": page.id})
    if pt.text:
        s.execute(
            text("INSERT INTO pages_fts (text, page_id, document_id, project_id) VALUES (:t, :p, :d, :pr)"),
            {"t": pt.text, "p": page.id, "d": doc.id, "pr": project_id},
        )


def handle_extract_pages(ctx: TaskContext) -> str:
    page_nos: list[int] = ctx.payload["page_nos"]
    opts = _ocr_opts(ctx.settings)
    version = _cache_version(opts)
    max_attempts = int(ctx.settings.get("processing.max_attempts", 3))
    with session_scope() as s:
        doc = s.get(Document, ctx.document_id)
        if doc is None:
            return "Document no longer exists"
        path, sha = doc.stored_path, doc.sha256
        pages = {p.page_no: p for p in s.scalars(select(Page).where(Page.document_id == doc.id, Page.page_no.in_(page_nos)))}
        for p in pages.values():
            p.status = "processing"
            p.attempts = (p.attempts or 0) + 1
        cached = {
            c.page_no: c.payload
            for c in s.scalars(
                select(TextCache).where(TextCache.doc_sha256 == sha, TextCache.extractor_version == version, TextCache.page_no.in_(page_nos))
            )
        }
    todo = [n for n in page_nos if n not in cached]
    ctx.progress(f"Extracting text: pages {page_nos[0]}–{page_nos[-1]} ({len(cached)} cached)")
    results = {n: cached[n] for n in cached}
    if todo:
        if ctx.should_stop():
            raise TaskCancelled()
        for r in ctx.run_cpu(extract_pages_batch, path, todo, opts):
            results[r["page_no"]] = r

    failed = 0
    with session_scope() as s:
        doc = s.get(Document, ctx.document_id)
        pages = {p.page_no: p for p in s.scalars(select(Page).where(Page.document_id == doc.id, Page.page_no.in_(page_nos)))}
        for n in page_nos:
            r = results.get(n)
            page = pages.get(n)
            if page is None or r is None:
                continue
            if r["status"] == "failed":
                failed += 1
                page.status = "pending" if page.attempts < max_attempts else "failed"
                page.status_reason = r.get("status_reason", "")
                _record_error(s, ctx, "document", "extract_pages", f"Page {n}: {r.get('status_reason')}", n, r.get("traceback", ""))
                continue
            _write_page(s, doc, page, r, ctx.project_id)
            if n not in cached and r["status"] == "completed":
                s.add(TextCache(doc_sha256=sha, page_no=n, extractor_version=version, payload=r))
            if r["status"] == "needs_review":
                _record_error(s, ctx, "document", "extract_pages", f"Page {n}: {r.get('status_reason')}", n)
            if n == 1 and not doc.equipment_name and r.get("headings"):
                biggest = max(r["headings"], key=lambda h: h.get("s", 0))
                doc.equipment_name = biggest["t"][:200]
    return f"Extracted {len(page_nos) - failed}/{len(page_nos)} pages ({len(cached)} from cache)"


# ---------------------------------------------------------------------------
# Stage: embeddings (optional)
# ---------------------------------------------------------------------------


def handle_embed_pages(ctx: TaskContext) -> str:
    provider = ctx.provider
    model = ctx.settings.get("ai.embedding_model", "")
    if provider is None or not model:
        return "Skipped: no embedding model configured"
    page_ids: list[int] = ctx.payload["page_ids"]
    with session_scope() as s:
        rows = s.execute(
            select(Page.id, PageText.text, PageText.headings).join(PageText, PageText.page_id == Page.id).where(Page.id.in_(page_ids))
        ).all()
    items = []
    for pid, txt, headings in rows:
        head = " | ".join(h["t"] for h in (headings or [])[:5])
        body = (txt or "").strip()
        if len(body) < 20:
            continue
        chunk = f"{head}\n{body}"[:2000]
        items.append((pid, chunk, hashlib.sha1(chunk.encode()).hexdigest()))
    done = 0
    for i in range(0, len(items), 16):
        if ctx.should_stop():
            raise TaskCancelled()
        batch = items[i : i + 16]
        vectors = provider.embed([b[1] for b in batch])
        with session_scope() as s:
            for (pid, _chunk, h), vec in zip(batch, vectors, strict=False):
                v = np.asarray(vec, dtype=np.float32)
                v /= np.linalg.norm(v) or 1.0
                s.execute(
                    PageEmbedding.__table__.delete().where(PageEmbedding.page_id == pid, PageEmbedding.model == model)
                )
                s.add(PageEmbedding(page_id=pid, chunk_idx=0, model=model, dim=int(v.shape[0]), vector=v.tobytes(), text_hash=h))
        done += len(batch)
        ctx.progress(f"Embedding pages: {done}/{len(items)}")
    return f"Embedded {done} pages with {model}"


# ---------------------------------------------------------------------------
# Stage: job extraction
# ---------------------------------------------------------------------------


def _llm_reconcile(ctx: TaskContext, page_ctx: PageContext, rule_jobs: list[JobCandidate]) -> list[JobCandidate]:
    """Add grounded model-found jobs the rules missed; boost rows both agree on."""
    from ..ai.tasks import llm_extract_jobs
    from ..retrieval.text_sim import content_tokens, coverage

    try:
        llm_jobs = llm_extract_jobs(ctx.provider, page_ctx.text)
    except Exception as exc:  # noqa: BLE001
        log.info("LLM job extraction unavailable for page %s: %s", page_ctx.page_no, exc)
        return rule_jobs
    out = list(rule_jobs)
    for lj in llm_jobs:
        lt = content_tokens(lj["task"] + " " + lj["component"])
        match = next((rj for rj in rule_jobs if coverage(lt, content_tokens(rj.original_title + " " + rj.component)) >= 0.7), None)
        if match is not None:
            match.confidence = min(0.98, match.confidence + 0.05)
            match.notes.append("Confirmed by model extraction.")
            continue
        if not lj["grounded"]:
            continue  # never accept tasks the model could not quote from the page
        freq = parse_frequency(lj["interval_text"], page_ctx.text[:1500]) if lj["interval_text"] else parse_frequency("")
        out.append(
            JobCandidate(
                title=make_title(lj["task"], lj["component"]), original_title=lj["task"], component=lj["component"],
                equipment=page_ctx.equipment, action="", frequency=freq, page_no=page_ctx.page_no,
                source_text=lj["source_quote"][:1000], method="llm", confidence=0.55,
                notes=["Found by model only — verify against the source page."]
                + ([] if lj["interval_grounded"] else ["Model interval not found in page text; discarded."]),
            )
        )  # fmt: skip
    return out


def handle_extract_jobs(ctx: TaskContext) -> str:
    with session_scope() as s:
        doc = s.get(Document, ctx.document_id)
        if doc is None:
            return "Document no longer exists"
        path, sha, equipment, digital = doc.stored_path, doc.sha256, doc.equipment_name, {}
        rows = s.execute(
            select(Page, PageText).join(PageText, PageText.page_id == Page.id).where(Page.document_id == doc.id)
        ).all()
        candidates = []
        doc_context_parts: list[str] = []

        def is_schedule_page(p: Page) -> bool:
            sc = p.kind_scores or {}
            return p.page_kind == "schedule" or sc.get("schedule", 0) >= 0.4 or sc.get("freq_lines", 0) >= 3

        def header_signature(lines: list[dict]) -> str:
            # First body words without numbers (running headers/footers excluded): identical on
            # every page of a table whose header row repeats.
            body = [ln["t"] for ln in sorted(lines or [], key=lambda ln: (ln["b"][1], ln["b"][0])) if not is_running_margin(ln)]
            words = " ".join(body).split()[:6]
            sig = re.sub(r"[\d\s]+", " ", " ".join(words)).strip().lower()
            return sig if len(words) >= 6 and len(re.findall(r"[a-z]{2,}", sig)) >= 3 else ""

        # Continuation pages of a schedule carry only "x" marks and no title, so they do not look like
        # schedules on their own. They are recognised by belonging to the same document (same printed
        # document code) or by repeating the schedule's table header.
        schedule_codes = {
            (p.layout_meta or {}).get("doc_code") for p, t in rows if p.page_kind == "schedule" and SCHEDULE_TITLE.search(t.text)
        } - {None}
        schedule_headers = {header_signature(t.lines) for p, t in rows if p.page_kind == "schedule"} - {""}
        for p, t in rows:
            sc = p.kind_scores or {}
            continuation = (p.layout_meta or {}).get("doc_code") in schedule_codes or header_signature(t.lines) in schedule_headers
            if is_schedule_page(p) or continuation:
                meta = p.layout_meta or {}
                candidates.append((p.page_no, t.text, t.lines, t.headings, sc, p.page_kind, meta))
                digital[p.page_no] = p.text_source == "digital"
            # Schedule introductions define how to read the matrix ("running hours", "B = based on observations").
            if SCHEDULE_TITLE.search(t.text[:3000]) and re.search(r"running\s+hours|operating\s+hours|based\s+on\s+observation", t.text, re.I):
                doc_context_parts.append(t.text[:4000])
    doc_context = " ".join(doc_context_parts)[:12000]
    # Comment legends belong to a schedule document (same document code across its pages).
    legends: dict[str, dict[str, str]] = {}
    for page_no, _txt, lines, _h, _sc, _kind, meta in candidates:
        key = (meta or {}).get("doc_code") or f"page-{page_no}"
        legends.setdefault(key, {}).update(parse_legend(lines or []))
    use_llm = ctx.provider is not None and ctx.settings.get("ai.use_for_job_extraction")
    all_jobs: list[JobCandidate] = []
    notes_total = 0
    for i, (page_no, txt, lines, headings, scores, kind, meta) in enumerate(sorted(candidates, key=lambda c: c[0])):
        if ctx.should_stop():
            raise TaskCancelled()
        ctx.progress(f"Identifying jobs: schedule page {page_no} ({i + 1}/{len(candidates)})")
        tables = ctx.run_cpu(extract_tables, path, page_no) if digital.get(page_no) else []
        labels = (meta or {}).get("labels") or []
        schedule_name = next((lb for lb in labels if SCHEDULE_TITLE.search(lb) or "," in lb), "")
        pctx = PageContext(
            page_no, txt, lines or [], headings or [], scores, equipment, tables, kind=kind, doc_context=doc_context,
            legend=legends.get((meta or {}).get("doc_code") or f"page-{page_no}", {}), schedule_name=schedule_name,
        )  # fmt: skip
        jobs, notes = extract_jobs_from_page(pctx)
        if use_llm:
            jobs = _llm_reconcile(ctx, pctx, jobs)
        notes_total += len(notes)
        all_jobs.extend(jobs)
    all_jobs, outside = _formal_schedule_precedence(all_jobs)
    kept, dropped = split_by_job_definition(all_jobs)
    dropped += outside
    with session_scope() as s:
        stats = upsert_extracted_jobs(s, ctx.project_id, ctx.document_id, sha, kept)
        doc = s.get(Document, ctx.document_id)
        doc.jobs_status = "complete"
        by_page: dict[int, list[str]] = {}
        for j, reason in dropped:
            by_page.setdefault(j.page_no, []).append(f"'{j.original_title[:60]}' ({reason})")
        for page_no, items in sorted(by_page.items()):
            _record_error(
                s, ctx, "document", "extract_jobs",
                f"Page {page_no}: {len(items)} schedule row(s) not created as jobs: " + "; ".join(items[:8]), page_no,
            )  # fmt: skip
    return (
        f"{len(candidates)} schedule pages, {stats['created']} new jobs, {stats['kept']} unchanged, "
        f"{stats['stale']} kept as stale (reviewed), {len(dropped)} rows without interval/equipment not created, "
        f"{notes_total} rows skipped"
    )


def _formal_schedule_precedence(jobs: list[JobCandidate]) -> tuple[list[JobCandidate], list[tuple[JobCandidate, str]]]:
    """When a manual has a formal tabular schedule, interval mentions in prose elsewhere are not extra jobs.

    Descriptive chapters often repeat or paraphrase schedule intervals ("check the water every
    third month"); the tabular schedule is the authoritative job list.
    """
    table_jobs = [j for j in jobs if j.method.startswith("table")]
    if len(table_jobs) < 20:
        return jobs, []
    table_pages = {j.page_no for j in table_jobs}
    kept, dropped = [], []
    for j in jobs:
        if j.method.startswith("table") or j.page_no in table_pages:
            kept.append(j)
        else:
            dropped.append((j, "interval mentioned outside the manual's maintenance schedule"))
    return kept, dropped


def split_by_job_definition(jobs: list[JobCandidate]) -> tuple[list[JobCandidate], list[tuple[JobCandidate, str]]]:
    """Schedule rows missing an interval or equipment stay jobs, flagged for review rather than dropped.

    Only text-line hits without either are dropped: outside a table they are rarely real schedule entries.
    """
    kept, dropped = [], []
    for j in jobs:
        missing = [w for w, ok in (("maintenance interval", j.frequency.found), ("equipment", (j.equipment or "").strip())) if not ok]
        if not missing:
            kept.append(j)
        elif not j.method.startswith("table"):
            dropped.append((j, "no " + " / ".join(missing)))
        else:
            j.notes.append("Schedule row has no " + " or ".join(missing) + " — verify against the source.")
            j.frequency.uncertain = True
            j.confidence = min(j.confidence, 0.4)
            kept.append(j)
    return kept, dropped


# ---------------------------------------------------------------------------
# Stage: procedure discovery
# ---------------------------------------------------------------------------


def handle_discover(ctx: TaskContext) -> str:
    job_ids: list[str] = ctx.payload["job_ids"]
    counts = {"verified": 0, "needs_confirmation": 0, "not_found": 0}
    dropped: list[str] = []
    with session_scope() as s:
        store = PageStore(s, ctx.project_id)
        finder = ProcedureFinder(store, ctx.settings, ctx.provider)
        doc_index = DocumentIndex(store)
        edits = edits_for(s, job_ids)
        for i, job in enumerate(s.scalars(select(Job).where(Job.id.in_(job_ids))).all()):
            if ctx.should_stop():
                raise TaskCancelled()
            if job.procedure_status != "pending":
                continue
            ctx.progress(f"Locating procedures: job {i + 1}/{len(job_ids)}")
            v = effective(job, edits.get(job.id))
            # 1) Explicit link: the schedule names the document that describes the task.
            action = job.action or detect_action(v["title"])
            linked, link_notes = link_procedure(doc_index, job.cross_reference, v["component"], v["title"], action)
            if linked:
                _save_linked_matches(s, job, linked, link_notes)
                counts[job.procedure_status] = counts.get(job.procedure_status, 0) + 1
                s.commit()  # release the write lock after each job (parallel workers)
                continue
            # 2) No usable reference: multi-stage search.
            q = JobQuery(job.id, v["title"], v["component"], v["equipment"], job.action, job.cross_reference, job.document_id, job.schedule_page_no)
            try:
                result = finder.find(q)
                result.notes.extend(link_notes)
                no_doc = bool(NO_DOCUMENT.match(job.cross_reference or "") or MAKER_DOCUMENT.search(job.cross_reference or ""))
                if no_doc and result.candidates and result.status != "not_found":
                    top = result.candidates[0].features
                    if not top.get("component_in_heading") and top.get("semantic", 0) < 0.6:
                        # The schedule says there is no document; a page that does not even name the
                        # component is not evidence of a procedure.
                        result.status = "not_found"
                        result.notes.append("Schedule lists no instruction document and no page names this component.")
                ref = reference_code(job.cross_reference or "")
                if ref and doc_index.available and not doc_index.documents_for(ref) and result.status != "not_found":
                    # The schedule names the exact document; any other page would be a substitute, not this job's description.
                    result.status = "not_found"
                    for c in result.candidates:
                        c.status = "candidate"
                    result.notes.append(
                        f"Schedule names document {ref}, which is not in the imported manuals. Other pages are not accepted "
                        f"in its place — import the manual that contains {ref}, or select a page manually."
                    )
                if result.status == "verified" and no_doc:
                    # The schedule says the manual has no document for this task: a text match is only a candidate.
                    result.status = "needs_confirmation"
                    result.candidates[0].status = "candidate"
                    result.notes.append("Schedule lists no instruction document for this task (N/A / manufacturer); a reviewer must confirm.")
            except Exception as exc:  # noqa: BLE001
                job.procedure_status = "failed"
                _record_error(s, ctx, "application", "discover_procedures", f"Job {job.seq}: {exc}")
                continue
            if result.status == "not_found":
                # Every schedule entry stays a job; a missing procedure is flagged for review, never deleted.
                dropped.append(f"'{v['title'][:60]}' (schedule p.{job.schedule_page_no})")
            s.execute(ProcedureMatch.__table__.delete().where(ProcedureMatch.job_id == job.id, ProcedureMatch.created_by == "machine"))
            for rank, c in enumerate(result.candidates):
                b = c.boundary
                s.add(
                    ProcedureMatch(
                        job_id=job.id, document_id=c.document_id,
                        start_page=b.start_page if b else c.anchor_page, end_page=b.end_page if b else c.anchor_page,
                        start_y=b.start_y if b else float(c.heading.get("y", 0)), end_y=b.end_y if b else 1.0,
                        heading=c.heading.get("t", ""), rank=rank, score=c.score, score_breakdown=c.features,
                        evidence={**c.evidence, "notes": result.notes if rank == 0 else []},
                        stages=sorted(c.stages), status="verified" if c.status == "verified" else "candidate",
                        selected=(rank == 0 and result.status == "verified"),
                    )
                )  # fmt: skip
            job.procedure_status = result.status
            counts[result.status] = counts.get(result.status, 0) + 1
            if result.status == "not_found":
                job.description_status = "not_available"
            s.commit()  # release the write lock after each job (parallel workers)
        if dropped:
            _record_error(
                s, ctx, "document", "discover_procedures",
                f"{len(dropped)} job(s) kept without a procedure — none found in the project's manuals: " + "; ".join(dropped[:10]),
            )  # fmt: skip
    return f"Verified {counts['verified']}, needs confirmation {counts['needs_confirmation']}, procedure not found {counts['not_found']}"


def _save_linked_matches(s: Session, job: Job, linked: list, notes: list[str]) -> None:
    """Store document-code links. A single procedure document is a verified match."""
    s.execute(ProcedureMatch.__table__.delete().where(ProcedureMatch.job_id == job.id, ProcedureMatch.created_by == "machine"))
    unique = len(linked) == 1 or linked[0].title_overlap > linked[1].title_overlap
    for rank, lp in enumerate(linked):
        verified = rank == 0 and unique
        heading = lp.title + (f" — {', '.join(lp.sections)}" if lp.sections else "")
        s.add(
            ProcedureMatch(
                job_id=job.id, document_id=lp.document_id, start_page=lp.start_page, end_page=lp.end_page,
                start_y=0.0, end_y=1.0, heading=heading, rank=rank, score=1.0 if verified else 0.8,
                score_breakdown={"document_code_link": 1.0, "title_overlap": round(lp.title_overlap, 3)},
                evidence={
                    "heading": heading, "boundary": lp.evidence, "skipped_pages": lp.skip_pages, "figure_pages": [],
                    "verification": [f"The schedule references document {lp.code}; the procedure is that document."] if verified else [],
                    "notes": notes if rank == 0 else [],
                },
                stages=["document_code"], status="verified" if verified else "candidate", selected=verified,
            )
        )  # fmt: skip
    job.procedure_status = "verified" if unique else "needs_confirmation"


# ---------------------------------------------------------------------------
# Stage: description generation
# ---------------------------------------------------------------------------


def _index_description(s: Session, d: JobDescription, project_id: str) -> None:
    s.execute(text("DELETE FROM descriptions_fts WHERE job_id = :j AND kind = :k"), {"j": d.job_id, "k": d.kind})
    s.execute(
        text("INSERT INTO descriptions_fts (content, description_id, job_id, project_id, kind) VALUES (:c, :d, :j, :p, :k)"),
        {"c": d.content, "d": d.id, "j": d.job_id, "p": project_id, "k": d.kind},
    )


def save_description(s: Session, job: Job, kind: str, content: str, blocks: list, method: str, status: str,
                     validation: dict, match_id: int | None, source_hash: str, created_by: str = "machine") -> JobDescription:  # fmt: skip
    prev = current_description(s, job.id, kind)
    version = 1
    if prev is not None:
        prev.is_current = False
        version = prev.version + 1
    d = JobDescription(
        job_id=job.id, procedure_match_id=match_id, kind=kind, version=version, is_current=True, content=content,
        blocks=blocks, method=method, status=status, validation=validation, source_hash=source_hash, created_by=created_by,
    )  # fmt: skip
    s.add(d)
    s.flush()
    _index_description(s, d, job.project_id)
    return d


_TABLE_CACHE: dict[tuple[str, int], list[dict]] = {}
_TABLE_CACHE_MAX = 2000


def _cached_tables(ctx: TaskContext, path: str, page_no: int) -> list[dict]:
    """Table detection per page is expensive and many jobs share the same work card pages."""
    key = (path, page_no)
    if key not in _TABLE_CACHE:
        if len(_TABLE_CACHE) >= _TABLE_CACHE_MAX:
            _TABLE_CACHE.pop(next(iter(_TABLE_CACHE)))
        _TABLE_CACHE[key] = ctx.run_cpu(extract_tables, path, page_no)
    return _TABLE_CACHE[key]


def describe_job(s: Session, ctx: TaskContext, store: PageStore, job: Job, force: bool = False) -> str:
    long_prev = current_description(s, job.id, "long")
    if long_prev is not None and long_prev.created_by == "human" and not force:
        return "kept_human"
    match = s.scalar(select(ProcedureMatch).where(ProcedureMatch.job_id == job.id, ProcedureMatch.selected.is_(True)))
    provisional = False
    if match is None:
        match = s.scalar(
            select(ProcedureMatch)
            .where(ProcedureMatch.job_id == job.id, ProcedureMatch.status != "rejected")
            .order_by(ProcedureMatch.rank)
        )
        provisional = True
    if match is None:
        job.description_status = "not_available"
        return "no_match"
    doc = store.doc(match.document_id)
    tables: dict[int, list[dict]] = {}
    for no in range(match.start_page, match.end_page + 1):
        p = store.page(match.document_id, no)
        if p is not None and p.status != "failed" and p.lines:
            try:
                tables[no] = _cached_tables(ctx, doc.stored_path, no)
            except Exception:  # noqa: BLE001 - tables are an enhancement; text is still extracted
                tables[no] = []
    long = extract_long_description(
        store, match.document_id, match.start_page, match.start_y, match.end_page, match.end_y,
        tables, (match.evidence or {}).get("skipped_pages", []),
    )  # fmt: skip
    status = "provisional" if provisional else ("needs_review" if long.warnings or not long.content else "generated")
    long_d = save_description(
        s, job, "long", long.content, long.blocks, "extractive", status,
        {"warnings": long.warnings, "pages": long.pages}, match.id, long.hash,
    )  # fmt: skip
    # Source references: one per procedure page, plus cross-references found in the text.
    s.execute(SourceReference.__table__.delete().where(SourceReference.job_id == job.id, SourceReference.ref_type.in_(["procedure", "cross_reference"])))
    for no in long.pages:
        s.add(SourceReference(job_id=job.id, description_id=long_d.id, document_id=match.document_id, page_no=no, ref_type="procedure"))
    for ref in long.references[:30]:
        s.add(SourceReference(job_id=job.id, description_id=long_d.id, document_id=match.document_id, page_no=ref["page"], ref_type="cross_reference", quote=ref["text"]))

    provider = ctx.provider if ctx.settings.get("ai.use_for_short_descriptions") else None
    short = generate_short(long.content, long.blocks, provider, ctx.settings.get("ai.chat_model", ""))
    short_status = "provisional" if provisional else ("needs_review" if not short.ok else "generated")
    save_description(s, job, "short", short.content, [], short.method, short_status, short.validation, match.id, long.hash)
    job.description_status = "provisional" if provisional else ("needs_review" if (long.warnings or not short.ok) else "generated")
    audit(s, "job", job.id, "descriptions_generated", None, {"match_id": match.id, "pages": [match.start_page, match.end_page], "short_method": short.method})
    return job.description_status


def handle_describe(ctx: TaskContext) -> str:
    job_ids: list[str] = ctx.payload["job_ids"]
    force = bool(ctx.payload.get("force"))
    results: dict[str, int] = {}
    with session_scope() as s:
        store = PageStore(s, ctx.project_id)
        for i, job in enumerate(s.scalars(select(Job).where(Job.id.in_(job_ids)))):
            if ctx.should_stop():
                raise TaskCancelled()
            s.refresh(job)
            if not force and job.description_status != "pending":
                continue  # already handled (e.g. by an earlier batch)
            ctx.progress(f"Extracting descriptions: job {i + 1}/{len(job_ids)}")
            try:
                r = describe_job(s, ctx, store, job, force)
            except Exception as exc:  # noqa: BLE001
                job.description_status = "failed"
                _record_error(s, ctx, "application", "describe_jobs", f"Job {job.seq}: {exc}")
                r = "failed"
            results[r] = results.get(r, 0) + 1
            s.commit()  # release the write lock after each job (parallel workers)
    return ", ".join(f"{k}: {v}" for k, v in sorted(results.items()))


HANDLERS: dict[str, Callable[[TaskContext], str]] = {
    "extract_pages": handle_extract_pages,
    "embed_pages": handle_embed_pages,
    "extract_jobs": handle_extract_jobs,
    "discover_procedures": handle_discover,
    "describe_jobs": handle_describe,
}
