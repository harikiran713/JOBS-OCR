"""Procedure linking through document codes.

Many manufacturers' manuals print a document code on every page (e.g. MAN Energy
Solutions: ``1465-0201-0018`` in the header, ``3 (12)`` page counter, and rotated
margin labels such as "Crosshead / Work Card / Dismantling"). Their maintenance
schedules reference those documents ("Doc ID 1465-0201"). When both exist, the link
between a job and its procedure is explicit — no similarity guessing is needed.

The section used for the description follows the task's action:
* checking / inspection / measuring  -> the "Checking" (or "Inspection") section
* overhaul / repair                  -> dismantling, overhaul and mounting sections
* replacement                        -> dismantling and mounting sections
* anything else, or no matching section -> the whole document
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import select

from ..db.models import Document, Page
from .store import PageStore
from .text_sim import content_tokens, coverage

# Document types that describe how to perform work, in order of preference.
PROCEDURE_TYPES = ("work card", "job card", "procedure", "instruction", "maintenance")
NON_PROCEDURE_TYPES = ("table of contents", "preface", "plate", "drawing", "spare", "data")

SECTION_FOR_ACTION = {
    "inspect": ["checking", "check", "inspection", "inspecting"],
    "measure": ["checking", "check", "inspection", "measuring", "measurement"],
    "test": ["checking", "check", "testing", "test", "inspection"],
    "overhaul": ["dismantling", "overhaul", "overhauling", "mounting", "assembly", "reassembly"],
    "repair": ["dismantling", "overhaul", "repair", "mounting", "assembly"],
    "replace": ["dismantling", "replacement", "mounting", "assembly", "reassembly"],
    "adjust": ["adjustment", "adjusting", "checking", "setting"],
    "clean": ["cleaning", "overhaul", "checking"],
}


@dataclass
class DocPage:
    document_id: str
    page_no: int
    code: str
    labels: list[str]
    page_n: int | None


@dataclass
class LinkedProcedure:
    document_id: str
    code: str
    title: str
    doc_type: str
    start_page: int
    end_page: int
    skip_pages: list[int] = field(default_factory=list)
    sections: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    title_overlap: float = 0.0


def _norm_code(s: str) -> str:
    return re.sub(r"[\s.]+", "-", (s or "").strip()).strip("-")


def reference_code(cross_reference: str) -> str | None:
    """'1465-0201' / '1465 0201' -> '1465-0201'; None for N/A, manufacturer or free text."""
    m = re.search(r"\b\d{3,5}(?:[-\s.]\d{2,5}){1,3}\b", cross_reference or "")
    return _norm_code(m.group(0)) if m else None


SECTION_NAMES = {
    "checking", "check", "inspection", "dismantling", "overhaul", "overhauling", "mounting", "assembly",
    "reassembly", "replacement", "testing", "cleaning", "adjustment", "adjusting",
}  # fmt: skip


class DocumentIndex:
    """Pages grouped by the document code printed on them (built once per discovery task)."""

    def __init__(self, store: PageStore):
        self.store = store
        self.by_code: dict[str, list[DocPage]] = {}
        rows = store.s.execute(
            select(Page.document_id, Page.page_no, Page.layout_meta)
            .join(Document, Document.id == Page.document_id)
            .where(Document.project_id == store.project_id)
            .order_by(Page.document_id, Page.page_no)
        ).all()
        for doc_id, page_no, meta in rows:
            meta = meta or {}
            code = meta.get("doc_code")
            if code:
                self.by_code.setdefault(_norm_code(code), []).append(
                    DocPage(doc_id, page_no, _norm_code(code), meta.get("labels") or [], meta.get("page_n"))
                )

    @property
    def available(self) -> bool:
        return bool(self.by_code)

    def documents_for(self, ref: str) -> list[str]:
        """Full document codes starting with a schedule reference ('1465-0201' -> '1465-0201-0018')."""
        return [c for c in self.by_code if c == ref or c.startswith(ref + "-")]


def _labels_info(pages: list[DocPage]) -> tuple[str, str, dict[int, str]]:
    """(title, document type, {page_no: section}) from margin labels."""
    title, doc_type = "", ""
    sections: dict[int, str] = {}
    counts: dict[str, int] = {}
    for p in pages:
        for lb in p.labels:
            counts[lb] = counts.get(lb, 0) + 1
    labels = [lb for p in pages for lb in p.labels]
    # Prefer a label that *is* a type ("Work Card"), then one that contains a type word.
    for pref in (PROCEDURE_TYPES + NON_PROCEDURE_TYPES + ("description",)):
        exact = next((lb for lb in labels if lb.strip().lower() == pref), None)
        if exact:
            doc_type = exact
            break
    if not doc_type:
        doc_type = next((lb for lb in labels if any(t in lb.lower() for t in PROCEDURE_TYPES + NON_PROCEDURE_TYPES)), "")
    # The title is the label shared by (almost) all pages that is not the type.
    common = [lb for lb, n in counts.items() if n >= max(1, len(pages) * 0.8) and lb != doc_type]
    title = common[0] if common else ""
    for p in pages:
        varying = [lb for lb in p.labels if lb not in (title, doc_type)]
        if varying:
            sections[p.page_no] = varying[0]
    return title, doc_type, sections


def _sections_from_headings(index: DocumentIndex, document_id: str, page_nos: list[int]) -> dict[int, str]:
    """Section per page from headings such as "Checking" / "Dismantling" when margins carry no section label."""
    sections: dict[int, str] = {}
    current = ""
    for no in sorted(page_nos):
        page = index.store.page(document_id, no)
        if page is not None:
            for h in page.headings:
                name = re.sub(r"^[\d.\s]+", "", h["t"]).strip().lower()
                if name in SECTION_NAMES and h["y"] < 0.5:
                    current = h["t"].strip()
                    break
        if current:
            sections[no] = current
    return sections


def link_procedure(index: DocumentIndex, cross_reference: str, component: str, title: str, action: str) -> tuple[list[LinkedProcedure], list[str]]:
    """Resolve a schedule document reference to procedure page ranges. Returns (candidates, notes)."""
    notes: list[str] = []
    ref = reference_code(cross_reference)
    if not ref or not index.available:
        return [], notes
    codes = index.documents_for(ref)
    if not codes:
        notes.append(f"Schedule reference {ref} was not found as a document in the imported manuals.")
        return [], notes

    out: list[LinkedProcedure] = []
    want = content_tokens(f"{component} {title}")
    for code in codes:
        pages = index.by_code[code]
        doc_title, doc_type, sections = _labels_info(pages)
        low_type = doc_type.lower()
        if any(t in low_type for t in NON_PROCEDURE_TYPES) and not any(t in low_type for t in PROCEDURE_TYPES):
            continue  # data sheets, plates, drawings and contents pages are not procedures
        # Pages of this document in one PDF, in order (ignore stray mentions, e.g. a contents page).
        by_doc: dict[str, list[DocPage]] = {}
        for p in pages:
            by_doc.setdefault(p.document_id, []).append(p)
        document_id, dpages = max(by_doc.items(), key=lambda kv: len(kv[1]))
        dpages = [p for p in dpages if p.page_n is not None] or dpages
        all_nos = [p.page_no for p in dpages]
        if not any(n in sections for n in all_nos):
            sections = _sections_from_headings(index, document_id, all_nos)
        wanted_sections = SECTION_FOR_ACTION.get(action, [])
        chosen = [p.page_no for p in dpages if sections.get(p.page_no, "").lower() in wanted_sections] if wanted_sections else []
        used_sections: list[str] = []
        if chosen:
            used_sections = sorted({sections[n] for n in chosen}, key=lambda s: min(n for n in chosen if sections[n] == s))
            start, end = min(chosen), max(chosen)
            skip = [n for n in range(start, end + 1) if n in all_nos and n not in chosen]
            ev = f"section(s) {', '.join(used_sections)} selected for a '{action}' task"
        else:
            start, end, skip = min(all_nos), max(all_nos), []
            ev = "whole document (no section specific to the task)"
        overlap = coverage(want, content_tokens(doc_title)) if want else 0.0
        out.append(
            LinkedProcedure(
                document_id=document_id, code=code, title=doc_title, doc_type=doc_type, start_page=start, end_page=end,
                skip_pages=skip, sections=used_sections, title_overlap=overlap,
                evidence=[
                    f"Schedule Doc ID {ref} → document {code} '{doc_title or '?'}' ({doc_type or 'type not labelled'}), "
                    f"pages {min(all_nos)}–{max(all_nos)}.",
                    f"Description pages {start}–{end}: {ev}.",
                ],
            )
        )  # fmt: skip
    if not out and codes:
        notes.append(f"Schedule reference {ref} points only to data sheets/plates, not to a work procedure.")
    # Prefer work cards / procedures, then the best title match.
    out.sort(key=lambda lp: (not any(t in lp.doc_type.lower() for t in PROCEDURE_TYPES), -lp.title_overlap, lp.code))
    return out, notes
