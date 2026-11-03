"""Multi-stage procedure discovery.

Stage 1  exact      – phrase search for the title, component and cross-reference
Stage 2  lexical    – BM25 over all project pages (FTS5, porter stemming, synonyms)
Stage 3  semantic   – cosine similarity over page embeddings (when a model is configured)
Stage 4  ranking    – feature-based score (heading match, component, action,
                      equipment, structure, cross-reference, retrieval ranks)
Stage 5  boundaries – start/end page detection from the anchor heading
Stage 6  validation – rule-based verification, optional grounded LLM check.
                      Uncertain results stay *candidates* for a reviewer.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ..extraction.classify import detect_action
from .boundaries import Boundary, detect_boundaries
from .store import PageInfo, PageStore
from .text_sim import (
    action_match,
    action_tokens,
    content_tokens,
    coverage,
    fts_terms,
    normalise_ref,
)

log = logging.getLogger(__name__)

ACTION_SYNONYMS = {
    "overhaul": ["overhaul", "dismantling", "reconditioning"],
    "inspect": ["inspection", "check", "examination"],
    "replace": ["replacement", "renewal", "exchange"],
    "clean": ["cleaning", "washing", "flushing"],
    "lubricate": ["lubrication", "greasing"],
    "test": ["test", "testing"],
    "adjust": ["adjustment", "setting"],
    "measure": ["measurement", "measuring", "clearance"],
    "calibrate": ["calibration"],
}

# Every page that contains a query term is scored — no top-k cut-off, so a procedure far from
# its schedule (or deep in a large manual) is never skipped.
ALL_PAGES = 1_000_000

# Margin labels naming the document type. Reference documents (system descriptions, data sheets,
# plates) explain equipment but are not the procedure for a job.
_REFERENCE_LABEL = re.compile(
    r"^\s*(?:description|data|plates?|drawings?|preface|table\s+of\s+contents|contents|index)\s*$|,\s*data\s*$", re.I
)
_BLANK_PAGE = re.compile(r"intentionally\s+(?:left\s+)?blank", re.I)
# Schedule rows that state there is nothing to do ("Bearings – no Duties During Operation").
NO_TASK = re.compile(r"\bno\s+(?:duties|tasks?|maintenance|work)\b", re.I)
_PROCEDURE_LABEL = re.compile(r"work\s*card|job\s*card|procedure|instruction", re.I)
_SECTION_LABEL = re.compile(
    r"^\s*(?:checking|check|inspection|dismantling|overhaul(?:ing)?|mounting|assembly|reassembly|replacement|"
    r"testing|cleaning|adjust(?:ment|ing)|special\s+running)\s*$",
    re.I,
)


def page_doc_type(labels: list[str]) -> str:
    """'reference' | 'procedure' | '' (manual does not label document types)."""
    if any(_REFERENCE_LABEL.search(lb) for lb in labels):
        return "reference"
    if any(_PROCEDURE_LABEL.search(lb) for lb in labels):
        return "procedure"
    return ""


def page_title(labels: list[str]) -> str:
    """Document title from margin labels ("Crosshead" from Crosshead / Work Card / Dismantling)."""
    keep = [lb for lb in labels if not (_PROCEDURE_LABEL.search(lb) or _REFERENCE_LABEL.search(lb) or _SECTION_LABEL.match(lb))]
    return " ".join(keep)


@dataclass
class JobQuery:
    job_id: str
    title: str
    component: str
    equipment: str
    action: str
    cross_reference: str
    document_id: str
    schedule_page: int

    @property
    def text(self) -> str:
        return f"{self.component} {self.title}".strip()


@dataclass
class Candidate:
    document_id: str
    anchor_page: int
    heading: dict
    score: float = 0.0
    features: dict[str, float] = field(default_factory=dict)
    stages: set[str] = field(default_factory=set)
    boundary: Boundary | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
    status: str = "candidate"


@dataclass
class DiscoveryResult:
    status: str  # verified | needs_confirmation | not_found
    candidates: list[Candidate]
    notes: list[str]


def _heading_score(q: JobQuery, heading: str, doc_title: str = "") -> dict[str, float]:
    """``doc_title``: the document title printed in the page margin; a section heading such as
    "Checking" belongs to it, so together they name the component and the action."""
    h_content = content_tokens(heading) | content_tokens(doc_title)
    comp = content_tokens(q.component) or content_tokens(q.title)
    title = content_tokens(q.title) | comp
    act = q.action or detect_action(q.title)
    xref = normalise_ref(q.cross_reference)
    xref_hit = 1.0 if xref and heading.lower().lstrip().startswith(xref) else 0.0
    title_obj = content_tokens(q.title)
    comp_cov = max(coverage(comp, h_content), coverage(title_obj, h_content) if title_obj else 0.0)
    act_m = action_match(act, action_tokens(heading) or action_tokens(doc_title))
    title_cov = coverage(title, h_content)
    d_title = content_tokens(doc_title)
    obj = comp if content_tokens(q.component) else title_obj
    return {
        "heading_match": round(0.5 * comp_cov + 0.3 * act_m + 0.2 * title_cov, 4),
        "component_in_heading": round(comp_cov, 4),
        "action_match": act_m,
        "xref": xref_hit,
        # Document-title evidence (manuals that print a document title in the margin).
        "has_doc_title": 1.0 if d_title else 0.0,
        "component_in_doc_title": round(coverage(obj, d_title), 4) if d_title else 0.0,
        "doc_title_in_component": round(coverage(d_title, obj), 4) if d_title else 0.0,
    }


def _best_heading(q: JobQuery, page: PageInfo) -> tuple[dict | None, dict[str, float]]:
    best, best_f = None, {"heading_match": 0.0}
    doc_title = page_title(page.labels)
    for h in page.headings:
        f = _heading_score(q, h["t"], doc_title)
        key = f["heading_match"] + f["xref"]
        if key > best_f["heading_match"] + best_f.get("xref", 0.0):
            best, best_f = h, f
    return best, best_f


class ProcedureFinder:
    def __init__(self, store: PageStore, settings: dict[str, Any], provider=None):  # noqa: ANN001
        self.store = store
        self.settings = settings
        self.provider = provider
        self.max_pages = int(settings.get("processing.max_procedure_pages", 30))
        self.embedding_model = settings.get("ai.embedding_model", "") if provider else ""

    # -- stages 1-3: candidate pages ------------------------------------------
    def _candidate_pages(self, q: JobQuery, notes: list[str]) -> dict[int, dict[str, Any]]:
        found: dict[int, dict[str, Any]] = {}

        def add(page_id: int, stage: str, rank_score: float) -> None:
            entry = found.setdefault(page_id, {"stages": set(), "lexical": 0.0, "semantic": 0.0, "exact": 0.0})
            entry["stages"].add(stage)
            key = "exact" if stage == "exact" else stage
            entry[key] = max(entry[key], rank_score)

        # Stage 1: exact phrases
        phrases = []
        comp_terms = fts_terms(q.component)
        if comp_terms:
            phrases.append(" ".join(comp_terms))
        title_terms = fts_terms(q.title)
        if title_terms:
            phrases.append(" ".join(title_terms))
        ref = normalise_ref(q.cross_reference)
        if ref:
            phrases.append(" ".join(fts_terms(ref.replace(".", " ").replace("-", " ")) or [ref]))
        for ph in phrases:
            if not ph:
                continue
            results = self.store.fts(f'"{ph}"', limit=ALL_PAGES)
            for i, (pid, _) in enumerate(results):
                add(pid, "exact", 1.0 - i / max(len(results), 1))

        # Stage 2: lexical with action synonyms
        terms = set(comp_terms + title_terms)
        act = q.action or detect_action(q.title)
        terms.update(ACTION_SYNONYMS.get(act, []))
        if terms:
            query = " OR ".join(f'"{t}"' for t in sorted(terms))
            results = self.store.fts(query, limit=ALL_PAGES)
            for i, (pid, _) in enumerate(results):
                add(pid, "lexical", 1.0 - i / max(len(results), 1))

        # Stage 3: semantic
        if self.embedding_model and self.provider is not None:
            emb = self.store.embeddings(self.embedding_model)
            if emb is None:
                notes.append("Semantic stage skipped: no page embeddings for this project yet.")
            else:
                try:
                    vec = np.asarray(self.provider.embed([f"Procedure: {q.text}"])[0], dtype=np.float32)
                    vec /= np.linalg.norm(vec) or 1.0
                    mat, ids = emb
                    sims = mat @ vec
                    top = np.argsort(-sims)[:200]
                    for idx in top:
                        add(ids[int(idx)], "semantic", float(max(sims[int(idx)], 0.0)))
                except Exception as exc:  # noqa: BLE001
                    notes.append(f"Semantic stage failed: {exc}")
        else:
            notes.append("Semantic stage not run (no embedding model configured).")
        return found

    # -- stage 4: ranking ----------------------------------------------------
    def _score(self, q: JobQuery, page: PageInfo, heading: dict, hf: dict, retr: dict, has_semantic: bool) -> tuple[float, dict]:
        doc = self.store.doc(page.document_id)
        page_tokens = self.store.content_tokens(page)
        comp = content_tokens(q.component) or content_tokens(q.title)
        text_match = coverage(comp | content_tokens(q.title), page_tokens)
        if page.document_id == q.document_id:
            equip = 1.0
        else:
            eq_tokens = content_tokens(q.equipment)
            equip = max(
                coverage(eq_tokens, content_tokens(f"{doc.equipment} {doc.title} {doc.filename}")) if eq_tokens else 0.5,
                0.8 * coverage(comp, page_tokens),
            )
        structure = float(page.scores.get("procedure", 0.0))
        f = {
            **hf,
            "text_match": round(text_match, 4),
            "equipment": round(equip, 4),
            "structure": round(structure, 4),
            "lexical": round(retr.get("lexical", 0.0), 4),
            "exact": round(retr.get("exact", 0.0), 4),
            "semantic": round(retr.get("semantic", 0.0), 4),
        }
        if has_semantic:
            s = (0.33 * f["heading_match"] + 0.12 * text_match + 0.1 * equip + 0.15 * structure
                 + 0.08 * f["lexical"] + 0.07 * f["exact"] + 0.15 * f["semantic"])  # fmt: skip
        else:
            s = (0.38 * f["heading_match"] + 0.17 * text_match + 0.1 * equip + 0.15 * structure
                 + 0.1 * f["lexical"] + 0.1 * f["exact"])  # fmt: skip
        if f["xref"]:
            s += 0.3
        f["doc_type"] = page_doc_type(page.labels)
        if f["doc_type"] == "reference":
            s *= 0.5
            f["penalty"] = "reference document (description / data / plate), not a procedure"
        elif page.kind in ("toc", "parts_list"):
            s *= 0.5
            f["penalty"] = f"page type {page.kind}"
        elif page.kind == "schedule" and structure < 0.3:
            s *= 0.6
            f["penalty"] = "schedule page"
        return round(min(s, 1.0), 4), f

    def _anchor_for(self, q: JobQuery, page: PageInfo) -> tuple[PageInfo, dict | None, dict]:
        """Find the procedure start: a matching heading on this page or up to 6 pages before."""
        heading, hf = _best_heading(q, page)
        if heading is not None and (hf["heading_match"] >= 0.45 or hf["xref"]):
            return page, heading, hf
        for back in range(1, 7):
            prev = self.store.page(page.document_id, page.page_no - back)
            if prev is None or prev.kind in ("toc", "schedule", "parts_list") or _BLANK_PAGE.search(prev.text[:400]):
                break
            h, f = _best_heading(q, prev)
            if h is not None and (f["heading_match"] >= 0.45 or f["xref"]):
                return prev, h, f
        return page, heading, hf

    def find(self, q: JobQuery) -> DiscoveryResult:
        notes: list[str] = []
        if NO_TASK.search(q.title):
            return DiscoveryResult("not_found", [], ["Schedule row states there is no task, so there is no procedure to find."])
        pages = self._candidate_pages(q, notes)
        has_semantic = any("semantic" in v["stages"] for v in pages.values())
        groups: dict[tuple[str, int, float], Candidate] = {}
        for page_id, retr in pages.items():
            page = self.store.page_by_id(page_id)
            if page is None:
                continue
            if page.document_id == q.document_id and page.page_no == q.schedule_page:
                continue  # the schedule row itself is not the procedure
            if page.kind in ("toc", "blank", "schedule") or _BLANK_PAGE.search(page.text[:400]):
                continue  # contents, schedule tables and blank pages are never the procedure itself
            anchor_page, heading, hf = self._anchor_for(q, page)
            if heading is None:
                heading = {"t": "", "y": 0.0, "s": 0, "b": False, "sec": "", "level": 9}
                hf = {"heading_match": 0.0, "component_in_heading": 0.0, "action_match": 0.0, "xref": 0.0}
            score, feats = self._score(q, anchor_page, heading, hf, retr, has_semantic)
            key = (anchor_page.document_id, anchor_page.page_no, round(float(heading.get("y", 0)), 3))
            cand = groups.get(key)
            if cand is None or score > cand.score:
                stages = set(retr["stages"]) | (cand.stages if cand else set())
                groups[key] = Candidate(anchor_page.document_id, anchor_page.page_no, heading, score, feats, stages)
            else:
                cand.stages |= set(retr["stages"])

        notes.append(f"Searched {len(pages)} page(s) containing the job's terms across all project manuals.")
        everything = sorted(groups.values(), key=lambda c: c.score, reverse=True)
        # A page is only offered as this job's description when it names the job's *whole* component
        # (heading or document title), the action fits, and it is not a reference document.
        # A partial name ("Cylinder Lubricator" for "Cylinder Lubricator Level Sensor") is a different object.
        ranked = [c for c in everything if self._names_job(q, c)][:8]
        if not ranked or ranked[0].score < 0.3:
            closest = everything[:3]
            for c in closest:
                c.boundary = detect_boundaries(self.store, c.document_id, c.anchor_page, c.heading, self.max_pages)
                c.evidence.update({"heading": c.heading.get("t", ""), "boundary": c.boundary.evidence, "snippet": self._excerpt(c, 400)})
            return DiscoveryResult(
                "not_found", closest,
                notes + ["No procedure page names this job's component and action; closest pages kept for manual selection only."],
            )  # fmt: skip

        # Stage 5: boundaries for the top candidates
        for c in ranked[:5]:
            c.boundary = detect_boundaries(self.store, c.document_id, c.anchor_page, c.heading, self.max_pages)

        # Remove candidates that fall inside a better candidate's range (continuation pages).
        kept: list[Candidate] = []
        for c in ranked[:5]:
            if any(
                k.document_id == c.document_id and k.boundary and k.boundary.start_page <= c.anchor_page <= k.boundary.end_page
                and not c.heading.get("t") for k in kept
            ):  # fmt: skip
                continue
            kept.append(c)
        ranked = kept

        # Stage 6: validation
        top = ranked[0]
        second = ranked[1].score if len(ranked) > 1 else 0.0
        margin = top.score - second
        f = top.features
        reasons = []
        verified = False
        if f.get("xref") and f.get("component_in_heading", 0) >= 0.5:
            verified = True
            reasons.append(f"Schedule cross-reference '{q.cross_reference}' matches the procedure heading.")
        elif (
            f.get("heading_match", 0) >= 0.75 and f.get("component_in_heading", 0) >= 0.99
            and f.get("action_match", 0) >= 1.0 and f.get("structure", 0) >= 0.3 and margin >= 0.12
        ):  # fmt: skip
            verified = True
            reasons.append("Heading names the same component and action, page has procedure structure, clear margin.")

        if not verified and self.provider is not None and self.settings.get("ai.use_for_procedure_verification"):
            excerpt = self._excerpt(top)
            try:
                from ..ai.tasks import llm_verify_procedure

                v = llm_verify_procedure(self.provider, f"{q.title} (component: {q.component or '-'}; equipment: {q.equipment or '-'})", excerpt)
                top.evidence["llm_check"] = v
                if v["verdict"] == "match" and v["grounded"] and top.score >= 0.5 and margin >= 0.08:
                    verified = True
                    reasons.append("Rule score supported by a grounded model check.")
                elif v["verdict"] == "no_match":
                    top.score = round(top.score * 0.7, 4)
                    notes.append("Model check: top candidate does not describe this job.")
            except Exception as exc:  # noqa: BLE001
                notes.append(f"Model verification unavailable: {exc}")

        for c in ranked:
            c.evidence.update(
                {
                    "heading": c.heading.get("t", ""),
                    "boundary": c.boundary.evidence if c.boundary else [],
                    "figure_pages": c.boundary.included_figure_pages if c.boundary else [],
                    "skipped_pages": c.boundary.skipped_pages if c.boundary else [],
                    "snippet": self._excerpt(c, 400),
                }
            )
        if verified:
            top.status = "verified"
            top.evidence["verification"] = reasons
            return DiscoveryResult("verified", ranked, notes)
        if margin < 0.08 and len(ranked) > 1:
            notes.append("Several candidates score similarly; reviewer confirmation required.")
        return DiscoveryResult("needs_confirmation", ranked, notes)

    @staticmethod
    def _names_job(q: JobQuery, c: Candidate) -> bool:
        f = c.features
        if f.get("xref"):
            return True
        if f.get("action_match", 0) <= 0 or f.get("doc_type") == "reference":
            return False
        if f.get("has_doc_title"):
            # The page belongs to a titled document: that document must be about this component. A heading
            # inside another component's document ("Turning gear switch" in the Cylinder Liner card) is a step there.
            # A title naming a more specific object ("Hydraulic Tools" for "All tools", "Injection Valve
            # Spindle Guide" for "Injection Valve") is a different component.
            if f.get("doc_title_in_component", 0) < 0.99:
                return False
            # Same object, or a parent document whose text names the sub-component
            # ("Fuel Oil" card: "checked for water content").
            return f.get("component_in_doc_title", 0) >= 0.99 or f.get("text_match", 0) >= 0.99
        if f.get("component_in_heading", 0) >= 0.99:
            return True
        # Job without a separate component ("General cleaning of the burner and air register"):
        # the heading must name part of it and the page must contain every word of it.
        return not q.component.strip() and f.get("component_in_heading", 0) > 0 and f.get("text_match", 0) >= 0.99

    def _excerpt(self, c: Candidate, limit: int = 5000) -> str:
        parts = []
        end = c.boundary.end_page if c.boundary else c.anchor_page
        for no in range(c.anchor_page, min(end, c.anchor_page + 4) + 1):
            p = self.store.page(c.document_id, no)
            if p is None:
                continue
            lines = [ln["t"] for ln in p.lines if no != c.anchor_page or ln["b"][1] >= c.heading.get("y", 0) - 0.001]
            parts.append("\n".join(lines))
        return "\n".join(parts)[:limit]
