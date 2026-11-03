"""Read access to page text/layout and the search indexes for a project."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..db.models import Document, Page, PageEmbedding, PageText


@dataclass
class PageInfo:
    page_id: int
    document_id: str
    page_no: int
    kind: str
    scores: dict
    text: str
    lines: list[dict]
    headings: list[dict]
    image_count: int
    status: str
    labels: list[str] = field(default_factory=list)  # margin labels: title / document type / section


@dataclass
class DocInfo:
    id: str
    filename: str
    title: str
    equipment: str
    page_count: int
    stored_path: str
    sha256: str


class PageStore:
    """Small LRU-ish cache over page rows. One instance per task; never shared across threads."""

    def __init__(self, session: Session, project_id: str, max_cached: int = 6000):
        self.s = session
        self.project_id = project_id
        self._pages: dict[tuple[str, int], PageInfo | None] = {}
        self._max = max_cached
        self._tokens: dict[int, set[str]] = {}
        self._docs: dict[str, DocInfo] = {}
        self._emb: tuple[np.ndarray, list[int]] | None = None
        self._emb_model: str | None = None

    # -- documents ---------------------------------------------------------
    def docs(self) -> dict[str, DocInfo]:
        if not self._docs:
            for d in self.s.scalars(select(Document).where(Document.project_id == self.project_id)):
                self._docs[d.id] = DocInfo(d.id, d.filename, d.title, d.equipment_name, d.page_count, d.stored_path, d.sha256)
        return self._docs

    def doc(self, doc_id: str) -> DocInfo:
        return self.docs()[doc_id]

    # -- pages -------------------------------------------------------------
    def page(self, doc_id: str, page_no: int) -> PageInfo | None:
        key = (doc_id, page_no)
        if key in self._pages:
            return self._pages[key]
        row = self.s.execute(
            select(Page, PageText)
            .join(PageText, PageText.page_id == Page.id, isouter=True)
            .where(Page.document_id == doc_id, Page.page_no == page_no)
        ).first()
        info = None
        if row is not None:
            p, t = row
            info = PageInfo(
                p.id, p.document_id, p.page_no, p.page_kind, p.kind_scores or {},
                t.text if t else "", (t.lines if t else []) or [], (t.headings if t else []) or [],
                p.image_count, p.status, list((p.layout_meta or {}).get("labels") or []),
            )  # fmt: skip
        if len(self._pages) >= self._max:
            self._pages.pop(next(iter(self._pages)))
        self._pages[key] = info
        return info

    def content_tokens(self, page: PageInfo) -> set[str]:
        """Content tokens of a page's text, computed once per page (every page may be scored for every job)."""
        if page.page_id not in self._tokens:
            from .text_sim import content_tokens

            self._tokens[page.page_id] = content_tokens(page.text)
        return self._tokens[page.page_id]

    def page_by_id(self, page_id: int) -> PageInfo | None:
        row = self.s.execute(select(Page.document_id, Page.page_no).where(Page.id == page_id)).first()
        return self.page(row[0], row[1]) if row else None

    # -- lexical -----------------------------------------------------------
    def fts(self, match: str, limit: int = 40) -> list[tuple[int, float]]:
        """Return ``(page_id, bm25)`` for the project; lower bm25 = better."""
        if not match.strip():
            return []
        try:
            rows = self.s.execute(
                text(
                    "SELECT page_id, bm25(pages_fts) AS r FROM pages_fts "
                    "WHERE pages_fts MATCH :q AND project_id = :p ORDER BY r LIMIT :n"
                ),
                {"q": match, "p": self.project_id, "n": limit},
            ).all()
        except Exception:  # noqa: BLE001 - malformed query must not break discovery
            return []
        return [(int(r[0]), float(r[1])) for r in rows]

    # -- semantic ----------------------------------------------------------
    def embeddings(self, model: str) -> tuple[np.ndarray, list[int]] | None:
        if self._emb is not None and self._emb_model == model:
            return self._emb
        rows = self.s.execute(
            select(PageEmbedding.page_id, PageEmbedding.vector, PageEmbedding.dim)
            .join(Page, Page.id == PageEmbedding.page_id)
            .join(Document, Document.id == Page.document_id)
            .where(Document.project_id == self.project_id, PageEmbedding.model == model)
        ).all()
        if not rows:
            return None
        dim = rows[0][2]
        mat = np.frombuffer(b"".join(r[1] for r in rows if r[2] == dim), dtype=np.float32).reshape(-1, dim)
        ids = [r[0] for r in rows if r[2] == dim]
        self._emb, self._emb_model = (mat, ids), model
        return self._emb
