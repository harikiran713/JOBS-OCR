"""Project-wide search across jobs, page text and descriptions.

Results are labelled by provenance so users can tell verbatim source text
(pages, extracted long descriptions) from generated summaries.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..db.models import Document, JobDescription, Project
from ..db.session import get_session
from .routes_jobs import build_rows

router = APIRouter()


def _fts_query(q: str) -> str:
    phrase = re.findall(r'"([^"]+)"', q)
    rest = re.sub(r'"[^"]+"', " ", q)
    terms = [t for t in re.findall(r"[\w.\-/]+", rest) if len(t) >= 2]
    parts = [f'"{p.strip()}"' for p in phrase if p.strip()]
    parts += [f'"{t}"*' if not any(c in t for c in ".-/") else f'"{t}"' for t in terms]
    return " AND ".join(parts)


@router.get("/projects/{project_id}/search")
def search(project_id: str, q: str = Query(min_length=2, max_length=300), limit: int = Query(50, le=200), s: Session = Depends(get_session)) -> dict:
    if s.get(Project, project_id) is None:
        raise HTTPException(404, "Project not found")
    needle = q.lower().strip('"')
    docs = {d.id: d.filename for d in s.scalars(select(Document).where(Document.project_id == project_id))}

    jobs = []
    for r in build_rows(s, project_id):
        fields = {"Job title": r["title"], "Equipment": r["equipment"], "Component": r["component"],
                  "Frequency": f"{r['frequency_original']} {r['frequency_label']}", "Job ID": r["code"],
                  "Manual": r["schedule_document"]}  # fmt: skip
        hit = [k for k, v in fields.items() if needle in (v or "").lower()]
        if hit:
            jobs.append({"job": r, "matched_fields": hit})
        if len(jobs) >= limit:
            break

    match = _fts_query(q)
    pages, descriptions = [], []
    if match:
        try:
            rows = s.execute(
                text(
                    "SELECT f.page_id, f.document_id, p.page_no, p.page_kind, snippet(pages_fts, 0, '[[', ']]', '…', 18) "
                    "FROM pages_fts f JOIN pages p ON p.id = f.page_id WHERE pages_fts MATCH :q AND f.project_id = :pid "
                    "ORDER BY bm25(pages_fts) LIMIT :n"
                ),
                {"q": match, "pid": project_id, "n": limit},
            ).all()
            pages = [
                {"document_id": r[1], "document_name": docs.get(r[1], ""), "page_no": r[2], "page_kind": r[3],
                 "snippet": r[4], "provenance": "source_text"}
                for r in rows
            ]  # fmt: skip
            drows = s.execute(
                text(
                    "SELECT f.description_id, f.job_id, f.kind, snippet(descriptions_fts, 0, '[[', ']]', '…', 18) "
                    "FROM descriptions_fts f WHERE descriptions_fts MATCH :q AND f.project_id = :pid "
                    "ORDER BY bm25(descriptions_fts) LIMIT :n"
                ),
                {"q": match, "pid": project_id, "n": limit},
            ).all()
            methods = {d.id: (d.method, d.created_by) for d in s.scalars(select(JobDescription).where(JobDescription.id.in_([r[0] for r in drows])))} if drows else {}
            titles = {r["id"]: (r["code"], r["title"]) for r in build_rows(s, project_id, list({r[1] for r in drows}))} if drows else {}
            for r in drows:
                method, by = methods.get(r[0], ("", ""))
                if by == "human":
                    prov = "reviewer_edited"
                elif r[2] == "long":
                    prov = "extracted_source_text"
                elif method.startswith("llm"):
                    prov = "ai_generated_summary"
                else:
                    prov = "extractive_summary"
                code, title = titles.get(r[1], ("", ""))
                descriptions.append({"job_id": r[1], "job_code": code, "job_title": title, "kind": r[2], "snippet": r[3], "provenance": prov, "method": method})
        except Exception as exc:  # noqa: BLE001 - invalid FTS syntax from user input
            return {"query": q, "jobs": jobs, "pages": [], "descriptions": [], "error": f"Search syntax not supported: {exc}"}
    return {"query": q, "jobs": jobs, "pages": pages, "descriptions": descriptions}
