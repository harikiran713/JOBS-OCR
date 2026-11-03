"""Procedure boundary detection.

Starting from the anchor heading, walk forward until a heading of the same or
higher rank begins a different section. Handles:

* multi-page procedures and "(continued)" pages,
* diagram/figure pages inside a procedure (little or no text) – included,
* procedures interrupted by unrelated pages and resumed (detected by the step
  numbering continuing after the interruption),
* hard limits (configurable maximum span) so a missing end heading never
  swallows the rest of a manual.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..extraction.layout import is_running_margin
from .store import PageInfo, PageStore

_STEP_NO = re.compile(r"^\s*(?:step\s+)?(\d{1,2})[.)]\s+\S", re.I)


@dataclass
class Boundary:
    start_page: int
    start_y: float
    end_page: int
    end_y: float
    evidence: list[str] = field(default_factory=list)
    included_figure_pages: list[int] = field(default_factory=list)
    skipped_pages: list[int] = field(default_factory=list)


def _sec_depth(sec: str) -> int:
    return sec.count(".") + sec.count("-") + 1 if sec else 0


def _ends_section(anchor: dict, h: dict) -> bool:
    """True if heading ``h`` starts a new section at the anchor's level or above."""
    if h.get("cont") or h["t"].strip().lower() == anchor["t"].strip().lower():
        return False
    a_sec, h_sec = anchor.get("sec", ""), h.get("sec", "")
    if a_sec and h_sec:
        return _sec_depth(h_sec) <= _sec_depth(a_sec)
    # Compare visual strength when numbering is not available on both.
    return float(h.get("s", 0)) >= float(anchor.get("s", 0)) - 0.6 and (h.get("b") or h.get("s", 0) >= anchor.get("s", 0))


def _step_numbers(page: PageInfo, after_y: float = 0.0, before_y: float = 1.01) -> list[int]:
    nums = []
    for ln in page.lines:
        if after_y <= ln["b"][1] < before_y and not is_running_margin(ln):
            m = _STEP_NO.match(ln["t"])
            if m:
                nums.append(int(m.group(1)))
    return nums


def _first_body_line(page: PageInfo) -> dict | None:
    for ln in sorted(page.lines, key=lambda ln: ln["b"][1]):
        if not is_running_margin(ln):
            return ln
    return None


def detect_boundaries(store: PageStore, doc_id: str, anchor_page: int, anchor: dict, max_pages: int = 30) -> Boundary:
    doc = store.doc(doc_id)
    start = store.page(doc_id, anchor_page)
    b = Boundary(start_page=anchor_page, start_y=float(anchor.get("y", 0.0)), end_page=anchor_page, end_y=1.0)
    if start is None:
        b.evidence.append("Anchor page not found.")
        return b

    # Same page: a later heading at the same/higher level closes the procedure.
    for h in start.headings:
        if h["y"] > anchor.get("y", 0.0) + 0.001 and _ends_section(anchor, h):
            b.end_y = h["y"]
            b.evidence.append(f"Ends on p.{anchor_page} at heading '{h['t'][:60]}'.")
            return b

    last_steps = _step_numbers(start, anchor.get("y", 0.0))
    last_step = max(last_steps) if last_steps else 0
    page_no = anchor_page
    while page_no < min(doc.page_count, anchor_page + max_pages - 1):
        nxt = store.page(doc_id, page_no + 1)
        if nxt is None:
            break
        if nxt.kind in ("parts_list", "toc", "schedule") and not nxt.headings:
            b.evidence.append(f"Stopped before p.{nxt.page_no}: page type '{nxt.kind}'.")
            break
        body_chars = sum(len(ln["t"]) for ln in nxt.lines if not is_running_margin(ln))
        if body_chars < 80 and nxt.image_count > 0 or (body_chars < 80 and not nxt.headings and body_chars > 0):
            # Figure/diagram page (graphics with a caption) – part of the procedure if steps resume after it.
            b.included_figure_pages.append(nxt.page_no)
            page_no += 1
            b.end_page, b.end_y = page_no, 1.0
            continue
        stop = next((h for h in nxt.headings if _ends_section(anchor, h)), None)
        if stop is None:
            steps = _step_numbers(nxt)
            continued = any(h.get("cont") for h in nxt.headings) or any(
                "continued" in ln["t"].lower() for ln in nxt.lines[:4] if not is_running_margin(ln)
            )
            # Continuation needs positive evidence; otherwise a missing end heading would
            # swallow the following chapter (whose headings may simply be smaller).
            procedure_like = float(nxt.scores.get("procedure", 0.0)) >= 0.2
            steps_continue = bool(steps) and (not last_step or min(steps) > last_step - 1)
            if not (continued or procedure_like or steps_continue):
                b.evidence.append(f"Stopped before p.{nxt.page_no}: page does not continue the procedure.")
                break
            page_no += 1
            b.end_page, b.end_y = page_no, 1.0
            if steps:
                last_step = max(last_step, max(steps))
            reason = "'continued' marker" if continued else "step numbering continues" if steps_continue else "procedure content"
            b.evidence.append(f"p.{page_no}: continuation ({reason}).")
            continue
        first = _first_body_line(nxt)
        steps_before = _step_numbers(nxt, 0.0, stop["y"])
        if first is not None and stop["y"] - first["b"][1] > 0.02 or steps_before:
            # Content precedes the next heading: procedure continues onto this page then ends.
            page_no += 1
            b.end_page, b.end_y = page_no, stop["y"]
            b.evidence.append(f"Ends on p.{page_no} at heading '{stop['t'][:60]}'.")
        else:
            b.evidence.append(f"Ends before p.{nxt.page_no}: new section '{stop['t'][:60]}'.")
        break
    else:
        if page_no >= anchor_page + max_pages - 1:
            b.evidence.append(f"Reached the maximum procedure length ({max_pages} pages); verify the end page.")

    # Interrupted procedures: an unrelated page followed by the step sequence continuing.
    if last_step:
        for gap in (1, 2):
            cand = store.page(doc_id, b.end_page + gap + 1)
            if cand is None:
                break
            first = _first_body_line(cand)
            m = _STEP_NO.match(first["t"]) if first else None
            if m and int(m.group(1)) == last_step + 1:
                skipped = list(range(b.end_page + 1, b.end_page + gap + 1))
                b.skipped_pages.extend(skipped)
                b.evidence.append(
                    f"Step {last_step + 1} resumes on p.{cand.page_no} after unrelated page(s) {skipped}; included."
                )
                b.end_page = cand.page_no
                stop = next((h for h in cand.headings if _ends_section(anchor, h)), None)
                b.end_y = stop["y"] if stop else 1.0
                break
    if b.included_figure_pages:
        b.evidence.append(f"Figure page(s) {b.included_figure_pages} included within the procedure.")
    return b
