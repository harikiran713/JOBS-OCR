"""Long job description extraction.

The long description is *extracted*, not generated: source lines between the
procedure boundaries are reassembled in reading order with running headers
and footers removed, wrapped lines re-joined, and each block classified
(step / safety / tools / materials / table / figure / reference / text).
Nothing is summarised or paraphrased; every block keeps its source page.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from ..extraction.layout import STEP_MARKER, is_running_margin
from ..retrieval.store import PageStore

SAFETY_RX = re.compile(r"^\s*(?:⚠\s*)?(DANGER|WARNING|CAUTION|ATTENTION|NOTICE|IMPORTANT|NOTE|INFO|SAFETY)\b\s*[:!.-]?", re.I)
# Upper-case imperatives used as safety notices without a label ("ALWAYS ensure that ...").
SAFETY_IMPERATIVE = re.compile(r"^\s*(ALWAYS|NEVER|DO NOT|MUST NOT)\b")
SAFETY_LABEL_ONLY = re.compile(r"^\s*(DANGER|WARNING|CAUTION|NOTICE|INFO|NOTE)\s*:?\s*$")
_STEP_TITLE = re.compile(r"^\s*(?:step\s+)?\d{1,2}[.)]\s+\S", re.I)


def _compact(text: str) -> str:
    """Text without whitespace or hyphens, for verbatim comparison across line breaks."""
    return re.sub(r"[\s\-­]+", "", text)


def _merge_left_column_titles(lines: list[dict]) -> list[dict]:
    """Join wrapped step titles in a left-hand column ("2. Mount a dial gauge" / "under the crosshead").

    Work cards often print a numbered step title in a narrow left column with the step text
    beside it. A title that wraps would otherwise be interleaved with the right-hand text.
    """
    titles = [ln for ln in lines if ln["b"][0] < 0.27 and _STEP_TITLE.match(ln["t"])]
    if not titles:
        return lines
    right = [ln for ln in lines if ln["b"][0] >= 0.27]
    if not any(abs(r["b"][1] - t["b"][1]) < 0.006 and r["b"][0] > t["b"][2] for t in titles for r in right):
        return lines  # no side-by-side layout on this page
    left = sorted((ln for ln in lines if ln["b"][2] < 0.3), key=lambda ln: ln["b"][1])
    consumed: set[int] = set()
    replaced: dict[int, dict] = {}
    for t in titles:
        text, last = t["t"], t
        for ln in left:
            if ln is t or id(ln) in consumed or ln["b"][1] <= last["b"][1]:
                continue
            height = max(last["b"][3] - last["b"][1], 0.008)
            if ln["b"][1] - last["b"][3] > height * 1.2 or _STEP_TITLE.match(ln["t"]) or SAFETY_LABEL_ONLY.match(ln["t"]):
                break
            text = text[:-1] + ln["t"] if text.endswith("-") else f"{text} {ln['t']}"
            consumed.add(id(ln))
            last = ln
        if last is not t:
            replaced[id(t)] = {**t, "t": text, "b": [t["b"][0], t["b"][1], max(t["b"][2], last["b"][2]), t["b"][3]]}
    return [replaced.get(id(ln), ln) for ln in lines if id(ln) not in consumed]
TOOLS_RX = re.compile(r"^\s*(?:special\s+)?tools?(?:\s+required)?\s*:", re.I)
MATERIALS_RX = re.compile(r"^\s*(?:materials?|consumables?|spare\s+parts?(?:\s+required)?|auxiliary\s+materials?)\s*:", re.I)
XREF_RX = re.compile(
    r"\b(?:see|refer\s+to|according\s+to|as\s+described\s+in|in\s+accordance\s+with)\s+"
    r"(?:procedure|section|chapter|page|table|fig(?:ure)?\.?|appendix|drawing|plate)?\s*[\w.\-/]+|"
    r"\b(?:fig(?:ure)?\.?|table|appendix|procedure)\s+[A-Z]?\d[\w.\-/]*",
    re.I,
)
STEP_NO_RX = re.compile(r"^\s*(?:step\s+)?(\d{1,2}|[a-z])[.)]\s+", re.I)
INLINE_STEPS = re.compile(r"(?<=[.!?])\s+(?=\d{1,2}\.\s+[A-Z])")
VALUE_RX = re.compile(
    r"\d+(?:[.,]\d+)?\s*(?:[–-]\s*\d+(?:[.,]\d+)?\s*)?(?:N\s?·?m|Nm|kNm|kgf?\s?m|mm|µm|um|bar|MPa|kPa|psi|°C|°F|rpm|ft[\s-]?lbs?|%|l\b|kg\b|minutes?|min\b|hours?|h\b)",
    re.I,
)


@dataclass
class LongDescription:
    content: str
    blocks: list[dict[str, Any]]
    references: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    pages: list[int] = field(default_factory=list)

    @property
    def hash(self) -> str:
        return hashlib.sha256(self.content.encode()).hexdigest()


def _block_type(text: str) -> tuple[str, str]:
    m = SAFETY_RX.match(text)
    if m:
        level = m.group(1).upper()
        # Only upper-case "NOTE"/"INFO" labels are notices; "Note the position ..." is an instruction.
        if level in ("NOTE", "INFO", "NOTICE") and not m.group(1).isupper():
            return "text", ""
        return "safety", level
    m = SAFETY_IMPERATIVE.match(text)
    if m:
        return "safety", "WARNING"
    if TOOLS_RX.match(text):
        return "tools", ""
    if MATERIALS_RX.match(text):
        return "materials", ""
    m = STEP_NO_RX.match(text)
    if m:
        return "step", m.group(1)
    if STEP_MARKER.match(text):
        return "step", ""
    return "text", ""


def _in_box(line: dict, box: list[float]) -> bool:
    cx = (line["b"][0] + line["b"][2]) / 2
    cy = (line["b"][1] + line["b"][3]) / 2
    return box[0] - 0.003 <= cx <= box[2] + 0.003 and box[1] - 0.003 <= cy <= box[3] + 0.003


def extract_long_description(
    store: PageStore,
    doc_id: str,
    start_page: int,
    start_y: float,
    end_page: int,
    end_y: float,
    tables_by_page: dict[int, list[dict]] | None = None,
    skip_pages: list[int] | None = None,
    include_heading: bool = True,
) -> LongDescription:
    tables_by_page = tables_by_page or {}
    skip = set(skip_pages or [])
    blocks: list[dict[str, Any]] = []
    warnings: list[str] = []
    pages: list[int] = []

    # Repeated header/footer lines across the range are removed even outside the margin band.
    seen_margin: dict[str, int] = {}
    for no in range(start_page, end_page + 1):
        p = store.page(doc_id, no)
        if p:
            for ln in p.lines:
                if is_running_margin(ln):
                    seen_margin[re.sub(r"\d+", "#", ln["t"])] = seen_margin.get(re.sub(r"\d+", "#", ln["t"]), 0) + 1

    for no in range(start_page, end_page + 1):
        if no in skip:
            continue
        p = store.page(doc_id, no)
        if p is None:
            warnings.append(f"Page {no} is missing from the page registry.")
            continue
        pages.append(no)
        if p.status in ("failed", "needs_review"):
            warnings.append(f"Page {no} has processing status '{p.status}'; verify the extracted text against the PDF.")
        lo = start_y - 0.002 if no == start_page else 0.0
        hi = end_y - 0.001 if no == end_page else 1.01
        lines = [ln for ln in p.lines if lo <= ln["b"][1] < hi and not is_running_margin(ln)]
        if not include_heading and no == start_page:
            lines = [ln for ln in lines if ln["b"][1] > start_y + 0.002]
        tables = [t for t in tables_by_page.get(no, []) if lo <= t["bbox"][1] < hi]
        body_chars = sum(len(ln["t"]) for ln in lines)
        if body_chars < 80 and (p.image_count > 0 or not p.headings):
            caption = " ".join(ln["t"] for ln in lines).strip()
            # Only the printed caption is kept; the UI marks the block as a figure without adding text.
            blocks.append({"type": "figure", "text": caption, "page": no})
            continue
        if p.kind == "unknown" and not lines:
            warnings.append(f"Page {no} has no extracted text.")

        lines = _merge_left_column_titles(lines)
        lines.sort(key=lambda ln: (round(ln["b"][1], 3), ln["b"][0]))
        emitted_tables: set[int] = set()
        current: dict[str, Any] | None = None
        prev_line: dict | None = None
        pending_label: str | None = None
        for ln in lines:
            bare = SAFETY_LABEL_ONLY.match(ln["t"])
            if bare:
                # A notice label printed as its own text item (beside an icon): attach it to its notice.
                label = bare.group(1).upper()  # level for styling; the printed word is kept in the text
                beside = current is not None and current["type"] in ("safety", "text") and ln["b"][1] - current.get("_y", -1) < 0.035
                if beside and not SAFETY_RX.match(current["text"]):
                    current["type"] = "safety"
                    current["text"] = f"{bare.group(1)} {current['text']}"
                    current["level"] = label
                else:
                    pending_label = bare.group(1)
                continue
            if pending_label and not SAFETY_RX.match(ln["t"]):
                ln = {**ln, "t": f"{pending_label} {ln['t']}"}
                pending_label = None
            t_idx = next((i for i, t in enumerate(tables) if _in_box(ln, t["bbox"])), None)
            if t_idx is not None:
                if t_idx not in emitted_tables:
                    emitted_tables.add(t_idx)
                    rows = ["\t".join(c for c in row) for row in tables[t_idx]["rows"]]
                    blocks.append({"type": "table", "text": "\n".join(rows), "page": no})
                    current = None
                continue
            text = ln["t"].strip()
            if not text or seen_margin.get(re.sub(r"\d+", "#", text), 0) >= 2:
                continue
            is_heading = any(abs(h["y"] - ln["b"][1]) < 0.002 and h["t"] == text for h in p.headings)
            if is_heading:
                blocks.append({"type": "heading", "text": text, "page": no})
                current = None
                prev_line = ln
                continue
            for part in INLINE_STEPS.split(text):
                btype, label = _block_type(part)
                gap = (ln["b"][1] - prev_line["b"][3]) if prev_line is not None else 0
                line_h = max(ln["b"][3] - ln["b"][1], 0.008)
                continues = (
                    current is not None
                    and btype == "text"
                    and gap < line_h * 1.1
                    and not current["text"].rstrip().endswith((".", ":", "!", "?"))
                ) or (current is not None and btype == "text" and part[:1].islower() and gap < line_h * 1.5)
                # Two-column layouts: a step title on the left and its text on the right, same line.
                side_by_side = (
                    current is not None and prev_line is not None and abs(ln["b"][1] - prev_line["b"][1]) < 0.006
                    and ln["b"][0] > prev_line["b"][2] and current["type"] == "step"
                )  # fmt: skip
                if side_by_side and btype == "text":
                    # A bare step number ("1.") stays on the line of its title; nothing else is added.
                    sep = " " if re.fullmatch(r"\s*(?:step\s+)?\d{1,2}[.)]\s*", current["text"], re.I) else "\n"
                    current["text"] = current["text"] + sep + part
                elif continues:
                    joiner = "" if current["text"].endswith("-") and part[:1].islower() else " "
                    current["text"] = (current["text"][:-1] if joiner == "" else current["text"]) + joiner + part
                else:
                    current = {"type": btype, "text": part, "page": no, "_y": ln["b"][1]}
                    if btype == "step" and label:
                        current["number"] = label
                    if btype == "safety":
                        current["level"] = label
                    blocks.append(current)
            prev_line = ln

    # Cross-references and technical values are tagged, never rewritten.
    references: list[dict[str, Any]] = []
    for b in blocks:
        refs = sorted({m.group(0).strip(" .,;") for m in XREF_RX.finditer(b["text"])})
        if refs:
            b["refs"] = refs
            for r in refs:
                references.append({"text": r, "page": b["page"]})
        vals = [m.group(0) for m in VALUE_RX.finditer(b["text"])]
        if vals:
            b["values"] = vals

    for b in blocks:
        b.pop("_y", None)

    # Verbatim guarantee: every word of every block must exist in the text of its source page
    # range. Blocks that fail are removed (never kept silently) and reported.
    source = _compact(" ".join((store.page(doc_id, no).text if store.page(doc_id, no) else "") for no in pages))
    verified: list[dict[str, Any]] = []
    for b in blocks:
        missing = [w for w in b["text"].split() if _compact(w) and _compact(w) not in source]
        if missing:
            warnings.append(f"Text on page {b['page']} could not be matched to the manual and was left out: {' '.join(missing[:6])}")
            continue
        verified.append(b)
    blocks = verified
    content = "\n".join(b["text"] for b in blocks if b["text"].strip()).strip()
    if not content:
        warnings.append("No text could be extracted from the procedure pages.")
    return LongDescription(content=content, blocks=blocks, references=references, warnings=warnings, pages=pages)
